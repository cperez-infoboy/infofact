"""Application settings loaded from environment / .env.

Secrets are NOT placed inside the agent container; only the backend reads them.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Punto de montaje FIJO dentro del container del agente.
# Es el target del bind-mount `docker run -v <host_path>:/workspaces`.
# Debe coincidir con:
#   - WORKDIR en Dockerfile.agent
#   - el `-w` de DockerSandbox.execute/upload_files/download_files
#   - las referencias a /workspaces en el system prompt (agent_service.py)
# No es una settings: NO cambia nunca. Si lo movieras, habría que tocar esos
# tres sitios a la vez o el agente operaría en un directorio distinto al montado.
WORKSPACE_CONTAINER_PATH = "/workspaces"

# Root del repo (padre del paquete backend). Sirve para anclar paths de datos
# RELATIVOS (p. ej. workspaces_host_root) de forma independiente al CWD del
# proceso, que puede derivar en sesiones largas y romper la resolucion de rutas.
# Los paths ABSOLUTOS (compose: host-path para el bind-mount DinD del agente) se
# usan tal cual en workspaces_root.
_REPO_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_url: str = "http://localhost:8080"
    secret_key: str = "dev-only-change-in-production"
    database_url: str = "sqlite+aiosqlite:///./data/infofact.db"
    checkpointer_db: str = "./data/checkpoints.db"
    webui_port: int = 8080

    # LLM. Any OpenAI-compatible endpoint. Defaults to Z.ai GLM (Coding Plan);
    # swap to OpenAI, OpenRouter, Together, local vLLM, etc. by changing these.
    # Empty key is allowed at import time so the sandbox can be smoke-tested
    # without an LLM; agent_service refuses to build the model until it is set.
    llm_api_key: str = ""
    llm_base_url: str = "https://api.z.ai/api/coding/paas/v4"
    llm_model: str = "glm-5.2"
    # Tope de tokens de SALIDA por llamada al modelo. Sin este valor el
    # request no envia max_tokens y el proxy OpenAI-compatible aplica su
    # default (4096): razonamiento + un write_file grande (~10K chars =
    # 3-4K tokens) no entra, la respuesta se corta a mitad del tool_use
    # (finish_reason=length) y los argumentos llegan vacios -> "file_path:
    # Field required" en loop de reintentos (incidente sesion 49). 32K
    # cubre escrituras de documentos completos (SRS ~30-50K chars) mas
    # razonamiento; es un techo, no un presupuesto, asi que no tiene costo.
    llm_max_tokens: int = 32_768

    # Streaming del modelo conversacional. True: ChatOpenAI usa la API de
    # streaming (SSE) y el relay recibe AIMessageChunk delta a delta, así el
    # chat despliega el texto a medida que el modelo genera (typewriter
    # real). False: cada llamada devuelve el AIMessage completo y el texto
    # aparece entero al terminar (comportamiento previo). Kill-switch por si
    # el proveedor OpenAI-compatible falla con SSE.
    llm_agent_streaming: bool = True

    # Vision (image understanding) for /captura over diagrams and UI mockups.
    # GLM-5.2 is text-only; vision needs a separate multimodal model. By default
    # it reuses the agent's endpoint + key (z.ai/glm-4.6v), but vision can point
    # at ANY OpenAI-compatible provider with multimodal capability by setting its
    # own base_url + api_key + model. Example -- route vision to OpenRouter:
    #   LLM_VISION_BASE_URL=https://openrouter.ai/api/v1
    #   LLM_VISION_API_KEY=sk-or-...
    #   LLM_VISION_MODEL=google/gemini-...   (or any vision-capable model)
    # Both LLM_VISION_* overrides default to empty and fall back to the agent's
    # shared values, so existing deployments keep working unchanged.
    # glm-5v-turbo (multimodal coding, better for UI mockups) needs a higher plan;
    # leave llm_vision_model_ui empty to reuse llm_vision_model for every image
    # kind, or set it once your plan includes it.
    llm_vision_model: str = "glm-4.6v"
    llm_vision_model_ui: str = ""  # e.g. "glm-5v-turbo"; empty -> reuse llm_vision_model
    llm_vision_api_key: str = ""  # vision-only API key; empty -> reuse llm_api_key
    llm_vision_base_url: str = ""  # OpenAI-compatible vision endpoint; empty -> reuse llm_base_url
    vision_image_max_bytes: int = 5 * 1024 * 1024  # Z.ai cap: 5MB / 6000x6000 per image
    vision_max_pictures_per_doc: int = 12  # bound vision calls on image-heavy PDFs
    # Rasterization scale for PDF pictures handed to the vision model. Docling's
    # default (1.0 ~ 72 DPI) renders vector diagrams (ER schemas, architecture)
    # too small to read -- e.g. the SIGSA DB schema rasterized to 317x594 and
    # became illegible, so glm-4.6v hallucinated placeholders. 4.0 (~288 DPI)
    # makes dense schemas readable. Lower to cut vision cost on simpler docs.
    vision_images_scale: float = 4.0

    # Guardas de tamaño (incidente de la sesión 44: un mensaje assistant de
    # 1.1 MB —el JSON crudo del anotador— envenenó la historia del chat y el
    # input del LLM en cada turno). Cuatro techos env-overridables:
    #   - relay_max_delta_chars: cap por evento `token` SSE. Una llamada
    #     anidada no-streaming puede llegar como UN delta gigante.
    #   - relay_max_assistant_chars: techo de acumulación de texto assistant
    #     por turno (los reports legítimos observados son de 1-5K).
    #   - llm_max_message_chars: ~6K tokens por mensaje individual; una
    #     anotación sana de ~100 secciones sigue entrando.
    #   - llm_total_input_chars: ~60K tokens de prompt total por turno.
    relay_max_delta_chars: int = 2_000
    relay_max_assistant_chars: int = 30_000
    # Techo del thinking (reasoning_content de GLM) por turno. Es texto
    # EFÍMERO de UI (evento SSE `thinking`): no se persiste ni entra al input
    # del modelo, así que un techo holgado solo acota el transporte.
    relay_max_thinking_chars: int = 60_000
    llm_max_message_chars: int = 24_000
    llm_total_input_chars: int = 240_000

    # Agent container lifecycle
    agent_image: str = "infofact-agent"
    # Path en el HOST donde viven los workspaces por perfil. En dev es relativo
    # al repo (no requiere root para crear). En compose, el backend corre dentro
    # del WebUI container; este valor apunta a la vista INTERNA del WebUI
    # container, que compose bind-mountea al host. El target dentro del container
    # del agente SIEMPRE es WORKSPACE_CONTAINER_PATH (constante arriba).
    workspaces_host_root: str = "./data/workspaces"
    container_idle_timeout: int = 1800
    registry_file: str = "./data/container_registry.json"

    docker_gid: str = "998"

    # Timeout por comando ejecutado en el sandbox (cada `docker exec` que hace
    # el agente via DockerSandbox.execute). Default 30s: suficiente para la fase
    # de requerimientos (curl a un feed, lectura/escritura de archivos, grep);
    # acota el daño de un fetch colgado. Antes era 120s hardcodeado y un solo
    # turno de "buscar noticias" quemó 228s (dos llamadas curl a Google News
    # cercanas al cap, apiladas en un mismo superstep). Subir a 60-120 cuando
    # llegue la fase de implementacion (npm install y similares).
    sandbox_exec_timeout: int = 30

    # Timeout por request HTTP de las web tools (web_search / fetch_url). Las
    # tools corren host-side (no en el container); este valor acota cada
    # búsqueda y cada fetch individual. 15s cubre búsqueda normal + resolución
    # de redirects; si un sitio cuelga, la tool devuelve error en vez de
    # bloquear el turno del agente (ver incidente de la sesión 6).
    web_tool_timeout: int = 15

    # Techo por archivo para upload binario de documentos fuente (Fase A de
    # ingesta). PDFs de RFP/contratos típicamente pesan 5-20 MB. Excedido ->
    # 413 Request Entity Too Large en POST /api/documents/upload.
    max_upload_bytes: int = 50 * 1024 * 1024

    # OCR fallback (Fase C): GLM-OCR para PDFs escaneados o con tablas rotadas
    # donde Docling pierde texto. Endpoint on-demand dedicado de Z.ai (NO el
    # Coding Plan, que da 1113 Insufficient balance); mismo LLM_API_KEY.
    # ocr_enabled apaga el router OCR globalmente (todo a Docling).
    # ocr_text_layer_threshold: chars/pagina bajo el cual un PDF se considera
    # escaneado y rutea a GLM-OCR (~500 segun el spike sobre sigsa_srs.pdf).
    llm_ocr_model: str = "glm-ocr"
    llm_ocr_base_url: str = "https://api.z.ai/api/paas/v4"
    ocr_enabled: bool = True
    ocr_pdf_dpi: int = 200
    ocr_max_pages: int = 100
    ocr_text_layer_threshold: int = 500

    @property
    def supports_vision(self) -> bool:
        """Whether image understanding is available.

        Gated on an API key + a configured vision model — NOT on the text model
        name: GLM-5.2 cannot see images. The key may be the vision-specific one
        (LLM_VISION_API_KEY) or, falling back, the shared LLM_API_KEY. When False,
        the capture pipeline degrades to Docling-only and
        the analyze_image subagent tool is not registered.
        """
        return bool((self.llm_vision_api_key or self.llm_api_key) and self.llm_vision_model)

    @property
    def workspaces_root(self) -> Path:
        """Workspace root absoluto, independiente del CWD del proceso.

        Los valores RELATIVOS (dev: ``./data/workspaces``) se anclan al root
        del repo para que resolverlos despues no dependa del CWD, que deriva en
        sesiones largas y rompe la resolucion de rutas del workspace. Los
        valores ABSOLUTOS (compose: host-path para el bind-mount DinD del
        agente) se devuelven tales cuales: el bind-mount necesita el path
        visible desde el host.
        """
        p = Path(self.workspaces_host_root)
        if not p.is_absolute():
            p = _REPO_ROOT / p
        return p.resolve()


settings = Settings()
