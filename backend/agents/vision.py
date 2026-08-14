"""Image understanding client for the requirements-capture agent.

Lets ``/captura`` see diagrams, UI mockups and other images that live in client
documents. GLM-5.2 (the text model) cannot process images; we call a separate
multimodal model (``glm-4.6v`` by default) on the same OpenAI-compatible
endpoint and API key as the text model. Verified live: the Coding Plan endpoint
serves ``glm-4.6v`` with image input using the same ``LLM_API_KEY``.

All public functions are SYNC. They run either inside the ingestion thread
(Docling is sync and is already wrapped in ``asyncio.to_thread`` by
requirements_service) or wrapped in ``asyncio.to_thread`` by the async subagent
tool. One implementation, two callers — same as Docling.

Gating: every public function checks ``settings.supports_vision`` and degrades
gracefully (returns ``""`` / ``"generic"`` / a plain message) when vision is off,
so callers keep working without it.
"""
from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Literal

from langchain_core.messages import HumanMessage
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from backend.agents.llm import with_nostream
from backend.config import settings

logger = logging.getLogger(__name__)

ImageKind = Literal["diagram", "mockup", "generic"]

# Z.ai rejects images that are too small: a 1x1 probe returned code 1210
# "image input format/parse error". Skip anything below this edge.
MIN_IMAGE_EDGE = 64
# Z.ai cap per image.
_MAX_IMAGE_EDGE = 6000


# --- prompts (neutral Spanish; project convention: no voseo, no spanglish) --

_CLASSIFY_SYSTEM = (
    "Clasifique la imagen adjunta en exactamente una de tres categorías, "
    "respondiendo solo con la palabra correspondiente:\n"
    "- diagram: diagramas técnicos, de arquitectura, de flujo, ER, UML, "
    "esquemas de base de datos, organigramas, mapas conceptuales.\n"
    "- mockup: bocetos o capturas de interfaz de usuario (pantallas, formularios, "
    "tableros, wireframes).\n"
    "- generic: cualquier otra imagen (logo, foto, gráfico de datos, ilustración).\n"
    "Responda con una sola palabra: diagram, mockup o generic."
)

_DIAGRAM_PROMPT = (
    "Esta imagen es un diagrama técnico dentro de un documento de cliente. "
    "Primero identifique de qué tipo de diagrama se trata (esquema de base de "
    "datos o entidad-relación, arquitectura de componentes o despliegue, flujo "
    "de proceso, diagrama de clases UML, organigrama, etc.) y luego descríbalo "
    "con el detalle necesario para derivar requerimientos de software, usando el "
    "marco que corresponda al tipo detectado.\n\n"
    "Si es un ESQUEMA DE BASE DE DATOS o entidad-relación, estructure así:\n"
    "1. Entidades o tablas (con sus nombres exactos).\n"
    "2. Campos, columnas o atributos de cada entidad (nombre y tipo visible: "
    "texto, número, fecha, booleano, etc.).\n"
    "3. Claves: primarias (PK) y foráneas (FK), indicando a qué entidad apunta "
    "cada foránea.\n"
    "4. Relaciones entre entidades con su cardinalidad (1:1, 1:N, N:M) y, si se "
    "indica, si es obligatoria u opcional.\n"
    "5. Restricciones o reglas de integridad visibles (unicidad, no nulo, etc.).\n"
    "Importante: las entidades son tablas y las relaciones son referencias entre "
    "tablas; NO las describa como componentes ni como flujos de datos.\n\n"
    "Si es un diagrama de ARQUITECTURA o COMPONENTES, estructure así:\n"
    "1. Componentes, servicios o nodos (con sus nombres exactos).\n"
    "2. Conexiones y dependencias (quién se conecta con quién y en qué sentido).\n"
    "3. Tecnologías, protocolos o capas visibles.\n"
    "4. Responsabilidades o reglas implícitas.\n\n"
    "Si es un diagrama de FLUJO DE PROCESO, estructure así:\n"
    "1. Actores o roles participantes.\n"
    "2. Pasos o actividades en orden, con decisiones y bifurcaciones.\n"
    "3. Eventos de inicio y de fin.\n\n"
    "Use nombres y etiquetas tal como aparecen. No invente lo que no se ve."
)

_MOCKUP_PROMPT = (
    "Esta imagen es un boceto o captura de una interfaz de usuario dentro de un "
    "documento de cliente. Descríbala con el detalle necesario para derivar "
    "requerimientos de software. Estructure la descripción así:\n"
    "1. Pantalla o ventana y su propósito.\n"
    "2. Elementos de interfaz visibles (encabezados, barras, menús, pestañas, "
    "tablas, listas, tarjetas).\n"
    "3. Campos de formulario (nombre del campo, tipo implícito texto/número/"
    "fecha/selector/casilla, si parece obligatorio, valores o placeholders).\n"
    "4. Acciones disponibles (botones, enlaces) y a dónde llevan.\n"
    "5. Estados, validaciones o reglas implícitas (deshabilitado, error, "
    "obligatoriedad, formatos).\n"
    "6. Navegación o jerarquía entre pantallas, si se infiere.\n"
    "Use etiquetas y textos tal como aparecen. No invente campos no visibles."
)

_GENERIC_PROMPT = (
    "Describa esta imagen con el detalle necesario para derivar requerimientos "
    "de software. Identifique elementos de texto (etiquetas, títulos, datos "
    "numéricos), la naturaleza del contenido y cualquier regla o restricción "
    "implícita. No invente lo que no se ve."
)

_PROMPT_BY_KIND: dict[str, str] = {
    "diagram": _DIAGRAM_PROMPT,
    "mockup": _MOCKUP_PROMPT,
    "generic": _GENERIC_PROMPT,
}


# --- internals -------------------------------------------------------------

def _vision_llm(model: str) -> Runnable:
    """ChatOpenAI pointed at a vision model, outside the stream `messages`.

    Uses the vision-specific endpoint/key (LLM_VISION_*) when set, otherwise
    falls back to the shared LLM_* values -- so vision can target any
    OpenAI-compatible provider independently of the text agent. Lleva el tag
    `nostream` (with_nostream): la respuesta de vision no debe emitirse al
    stream `messages` del grafo (incidente de la sesión 44).
    """
    api_key = settings.llm_vision_api_key or settings.llm_api_key
    if not api_key:
        raise RuntimeError(
            "No API key for vision: set LLM_VISION_API_KEY (or LLM_API_KEY)."
        )
    base_url = settings.llm_vision_base_url or settings.llm_base_url
    return with_nostream(ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0.0,
        streaming=False,
        stream_chunk_timeout=300,  # GLM can stall mid-stream; matches build_llm
        max_retries=2,             # SDK handles 429/5xx natively
    ))


def _image_to_data_uri(path: Path) -> str:
    """Read, validate and size-check an image into a base64 data URI.

    Z.ai caps each image at 5 MB and 6000x6000 px, and rejects images that are
    too small. Oversized images are resized (aspect-preserving) with PIL, which
    is already in the venv via Docling.
    """
    from PIL import Image  # local import: heavy, only needed when sending

    raw = path.read_bytes()
    if len(raw) > settings.vision_image_max_bytes:
        raise ValueError(
            f"image {path} is {len(raw)} bytes, exceeds "
            f"vision_image_max_bytes={settings.vision_image_max_bytes}"
        )
    img = Image.open(io.BytesIO(raw))
    fmt = (img.format or "PNG").lower()
    save_fmt = "JPEG" if fmt in ("jpg", "jpeg") else fmt.upper()
    mime = "image/jpeg" if fmt in ("jpg", "jpeg") else f"image/{fmt}"

    w, h = img.size
    if min(w, h) < MIN_IMAGE_EDGE:
        raise ValueError(
            f"image {path} is {w}x{h}, below minimum {MIN_IMAGE_EDGE}px on an edge"
        )
    if max(w, h) > _MAX_IMAGE_EDGE:
        scale = _MAX_IMAGE_EDGE / max(w, h)
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    buf = io.BytesIO()
    img.save(buf, format=save_fmt)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _model_for_kind(kind: ImageKind) -> str:
    """Pick the vision model for a kind. Mockups use the UI model when configured."""
    if kind == "mockup" and settings.llm_vision_model_ui:
        return settings.llm_vision_model_ui
    return settings.llm_vision_model


def _invoke_with_image(model: str, prompt: str, data_uri: str) -> str:
    llm = _vision_llm(model)
    msg = HumanMessage(content=[
        {"type": "image_url", "image_url": {"url": data_uri}},
        {"type": "text", "text": prompt},
    ])
    resp = llm.invoke([msg])
    content = getattr(resp, "content", "")
    # Some providers return a list of parts instead of a plain string.
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return (content or "").strip()


# --- public API ------------------------------------------------------------

def classify_image_kind(path: Path) -> ImageKind:
    """Classify an image as diagram / mockup / generic.

    Defaults to ``"generic"`` on any failure (vision off, image too small, API
    error) so the caller never crashes.
    """
    if not settings.supports_vision:
        return "generic"
    try:
        data_uri = _image_to_data_uri(path)
        raw = _invoke_with_image(settings.llm_vision_model, _CLASSIFY_SYSTEM, data_uri)
        token = raw.lower().split()[0].strip(".,;:!?-") if raw else ""
        if token in ("diagram", "mockup"):
            return token  # type: ignore[return-value]
        return "generic"
    except Exception as exc:  # noqa: BLE001 — degrade, don't crash the pipeline
        logger.warning("classify_image_kind failed for %s: %s", path, exc)
        return "generic"


def describe_image(path: Path, kind: ImageKind | None = None) -> str:
    """Describe an image for requirement extraction.

    When ``kind`` is None the image is classified first (one extra call); the
    model and prompt are then picked for the detected kind. Returns ``""`` when
    vision is unavailable or every attempt fails — callers treat ``""`` as
    'no description'.

    If a kind-specific UI model is configured but the call fails (e.g. it is not
    in the current subscription plan, code 1311), we retry once with the default
    vision model so the description is still produced.
    """
    if not settings.supports_vision:
        return ""
    try:
        if kind is None:
            kind = classify_image_kind(path)
        data_uri = _image_to_data_uri(path)
        prompt = _PROMPT_BY_KIND.get(kind, _GENERIC_PROMPT)
        model = _model_for_kind(kind)
        try:
            return _invoke_with_image(model, prompt, data_uri)
        except Exception as exc:
            if model != settings.llm_vision_model and settings.llm_vision_model:
                logger.warning(
                    "vision model %s failed (%s); retrying with default %s",
                    model, exc, settings.llm_vision_model,
                )
                return _invoke_with_image(settings.llm_vision_model, prompt, data_uri)
            raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("describe_image failed for %s: %s", path, exc)
        return ""


def analyze_image(path: Path, question: str) -> str:
    """Answer a free-form question about an image.

    Intended for the subagent's ``analyze_image`` tool (directed inspection).
    """
    if not settings.supports_vision:
        return (
            "Visión no disponible: falta LLM_API_KEY o llm_vision_model en la "
            "configuración del backend."
        )
    try:
        data_uri = _image_to_data_uri(path)
        return _invoke_with_image(settings.llm_vision_model, question, data_uri)
    except Exception as exc:  # noqa: BLE001
        return f"No se pudo analizar la imagen {path.name}: {exc}"
