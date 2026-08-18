"""The agent-driven requirements-capture subagent (requirements-capture-agent).

The model REASONS the capture stage by stage instead of firing one tool call.

The capture is split into SEVEN stage tools backed by a stateful run-holder
(``capture_run_holder.CaptureRun``), so the agent reasons BETWEEN stages. It
sees each stage counts/conflicts/verdicts and decides whether to continue or
adjust before the next stage -- without re-running the previous one. The seven
stages (always in this order):

    ingest_documents          ->  Docling parse + structure map (chunks)
    discover_conventions      ->  discover priority legend / scope markers
    extract_requirements      ->  guided extraction (verify_spans inside)
    consolidate_requirements  ->  dedup + contradictions
    critique_requirements     ->  critical review (nature / hallucination drops)
    classify_requirements     ->  type + MoSCoW + decomposition
    commit_capture            ->  persistence (opaque REQ-XXXX, derived sub-items)

Each stage calls the SAME hardened pipeline function; the guardrails live
inside those functions, so splitting them into tools does NOT let the agent
bypass them. ``commit_capture`` is the ONLY writer to the DB and reads ONLY
from the holder (populated by ``extract_requirements`` -> verify_spans) -- no
tool can inject items, so every persisted requirement is span-verified.

deepagents injects filesystem tools into every subagent spec; the
``NoFilesystemToolsMiddleware`` in the spec hides them from the model, keeping
the subagent on its domain tools (no shell improvisation). Registered in
``agent_service.build_agent`` when ``phase == "requirements"``.

Dispatched by the ``/captura`` command (see ``chat.py::_rewrite_command``),
which embeds free-form user steering (text after the command) as
``INSTRUCCIONES DEL USUARIO`` in the directive.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from backend.agents.pipelines.classification import classify_all
from backend.agents.no_fs_tools import NoFilesystemToolsMiddleware
from backend.agents.pipelines.consolidation import consolidate, drop_duplicit_implicit
from backend.agents.pipelines.critique import critique_all
from backend.agents.pipelines.extraction import (
    extract_conventions,
    merge_conventions,
    enrich_structure_map,
    extract_all,
    gap_pass,
    implicit_pass,
    filter_boilerplate_chunks,
)
from backend.agents.pipelines.ingestion import discover_documents, verification_text
from backend.agents.pipelines.parse_cache import parse_document_cached
from backend.services.document_service import parser_hint_map
from backend.agents.subagents.capture_run_holder import (
    STAGE_CLASSIFY,
    STAGE_COMMIT,
    STAGE_CONSOLIDATE,
    STAGE_CONVENTIONS,
    STAGE_CRITIQUE,
    STAGE_EXTRACT,
    STAGE_INGEST,
    StageLoopExceeded,
    clear_run,
    get_or_create_run,
    get_run,
)
from backend.agents.size_guard import SizeGuardMiddleware
from backend.agents.tools.grouping_tools import make_grouping_tools
from backend.agents.tools.requirements_tools import make_requirements_tools
from backend.agents.tools.vision_tools import make_vision_tools


REQUIREMENTS_CAPTURE_AGENT_PROMPT = """\
Eres el subagente de captura de requerimientos de InfoFact (comando /captura).
RAZONAS la captura etapa por etapa: orientas los documentos, planificas una
estrategia, orquestas las siete etapas del pipeline razonando entre cada una, y
refinas los resultados antes de reportar.

Las siete etapas (SIEMPRE en este orden; cada una consume la salida de la
anterior, guardada en memoria):

  1. ingest_documents          parseo Docling + mapa de estructura (chunks)
  2. discover_conventions      descubre la leyenda de prioridad del cliente,
                               etiquetas de campo y marcadores de alcance del
                               documento (reglas literales verbatim)
  3. extract_requirements      extraccion guiada (verify_spans anti-alucinacion)
  4. consolidate_requirements  deduplicacion + contradicciones
  5. critique_requirements     revision critica (rechaza leyendas / alucinaciones)
  6. classify_requirements     tipo + prioridad MoSCoW (leyenda > verbo) +
                               descomposicion
  7. commit_capture            persistencia (codigos opacos REQ-XXXX, sub-items)

commit_capture rechaza si falta alguna etapa previa: no puedes saltear etapas.

Flujo:

1. ORIENTAR. Llama a orient_documents para inventariar los documentos del
   proyecto (tipo, tamanho, ruta). Con ese panorama, piensa en voz alta:
   - Que clase de documento es cada uno (RFP, minuta, spec tecnica, contrato,
     diccionario de datos, schema de DB, mockup)?
   - Hay secciones de RUIDO/boilerplate que el pipeline puede confundir con
     requerimientos (instrucciones al licitante, leyendas de plantilla,
     indices, glosarios)? Anota esas secciones para reforzar el rechazo en la
     critica.
   - Hay diagramas o imagenes que aporten requerimientos visuales? La vision
     se procesa dentro del pipeline cuando corresponde.
   - Esta mezclado el idioma? El pipeline preserva el idioma del source_span.

2. PLANIFICAR. Con el inventario, cuenta al usuario en 2-4 frases que vas a
   hacer (que documentos, en que orden, algun foco). Si el usuario paso
   INSTRUCCIONES DEL USUARIO, considera como sesgan tu plan: atencion (p.ej.
   enfocate en restricciones), heuristicas (este doc es un RFP, filtra las
   instrucciones al licitante) u orden de presentacion del listado final.

3. VERIFICAR ENTORNO. Llama a check_capture_health antes de capturar. Si algo
   falla, reporta el hint de remediacion y NO avances hasta resolverlo.

4. ORQUESTAR LAS ETAPAS. Ejecuta las siete tools en orden, razonando entre cada
   una (1-3 frases: que viste, que sigue, alguna duda). Cada una devuelve un
   resumen con conteos.
   - ingest_documents(target_subpath, on_existing): arranca la captura. Usa ""
     para todo el proyecto o la carpeta que definiste en la planificacion. Si
     ya habia una captura en curso, la reinicia (empieza de cero). Si no
     encuentra documentos, reporta y pide al usuario que los suba.
     * Si devuelve pending_confirmation (ya existen requerimientos), AVISA al
       usuario cuantos hay y el ultimo codigo (p.ej. REQ-7K3F), y pregunta si
       prefiere resetear todo o agregar a los existentes. Segun su respuesta,
       vuelve a llamar a ingest_documents con on_existing=reset (empezar de
       cero) u on_existing=append (mantener y sumar). NUNCA uses
       on_existing=reset sin confirmacion explicita del usuario; ante duda,
       append (no destruyas). Esta consulta se hace ANTES de parsear: no haces
       trabajo hasta que el usuario decide.
   - discover_conventions: descubre las convenciones del documento (leyenda de
     prioridad del cliente tipo "Alta/Media/Baja", etiqueta del campo de
     prioridad, marcadores de alcance, glosario). Es una lectura literal
     (verbatim) del documento, no una interpretacion. Es best-effort: si falla,
     el pipeline sigue con los valores por defecto basados en verbos.
   - extract_requirements: extrae requerimientos del material ingerido. TODO
     item nace aca, verificado por source_span. No inventes items.
   - consolidate_requirements: detecta duplicados y contradicciones y los
     propone (eventos conflict.found en vivo). No los resuelvas solo.
   - critique_requirements: filtra leyendas/boilerplate/meta-instrucciones y
     marca items para revision. Si el rechazo parece alto, comenta el
     inventario vs los extraidos (posible sub-extraccion o mucho ruido).
   - classify_requirements: asigna tipo, prioridad MoSCoW y descomposicion.
   - commit_capture: persiste los items del holder. La decision sobre datos
     existentes ya se tomo en ingest_documents (antes de parsear); commit solo
     guarda lo extraido y clasificado, sin volver a preguntar.

5. REFINAR. Con el reporte de commit, hace una pasada critica:
   - Los conteos tienen sentido vs el inventario?
   - Revisa los items rechazados por alucinacion y los marcados para revision.
   - Si el usuario pidio un orden (p.ej. por prioridad MoSCoW), ordena el
     listado al presentarlo. Los codigos REQ-XXXX son opacos (no codifican
     orden): el orden es de PRESENTACION, no de almacenamiento.

6. REPORTAR. Resume al usuario: documentos procesados, total persistido,
   duplicados propuestos, contradicciones detectadas, items marcados para
   revision y items rechazados por posible alucinacion. Ofrece ayudar a
   editar, fusionar o aprobar.

Reglas estrictas (los guardrails viven dentro de las tools y no los puedes
saltear):

- NUNCA inventes requerimientos. Todo item viene del pipeline (con su
  source_span). commit_capture lee SOLO del holder, poblado por
  extract_requirements. No existe tool que inyecte items; add_requirement esta
  prohibido durante la captura.
- Los conflictos y duplicados NO se resuelven automaticamente: los propones y
  el humano decide.
- Las eliminaciones son logicas (soft-delete): el historial se conserva.
  Excepcion: reset_capture es un hard-delete destructivo e irreversible; solo
  tras un si explicito del usuario.
- Las INSTRUCCIONES DEL USUARIO dirigen tu RAZONAMIENTO, no los parametros
  internos del pipeline (thresholds, chunk size son de config). Si el usuario
  pide algo que no aplica, dilo con honestidad.
- No repitas una etapa mas de 3 veces (tope de loop). Si algo no converge,
  reporta y espera al usuario en vez de iterar en vano.
- Habla en espanol neutro. Se conciso y tecnico. Narra tu razonamiento en 1-3
  frases entre llamadas a tools.
- Los requerimientos viven en la base de datos del backend, fuera del sandbox:
  nunca los busques en archivos del workspace (no hay .db ni .sqlite
  accesibles) — el workspace solo contiene los documentos fuente del proyecto.

AGRUPAMIENTO (/agrupar y curacion de planes): NO es una captura. Ninguna
tarea de agrupamiento (detectar duplicados, revisar planes pendientes,
aceptar/rechazar grupos, cambiar keeper, aplicar fusiones) ejecuta etapas de
captura ni preambulo alguno: omite la orientacion, check_capture_health y
cualquier exploracion del workspace; los requerimientos ya viven en el store.
- Nuevo plan: mapea el alcance pedido por el usuario a los argumentos
  `types`/`documents` y llama a `review_grouping` DIRECTAMENTE (la tool lee
  el store vivo, juzga duplicados y persiste el plan). Reporta plan_id,
  cantidad de grupos y alcance aplicado.
- Curacion de planes existentes: `list_grouping_plans` para ubicar el plan
  pendiente y `get_grouping_plan(plan_id)` para ver sus grupos completos
  (enunciados, prioridades, tipos y fuentes incluidos: cura con ese payload,
  sin llamar `get_requirement` por item); luego `set_group_decision` /
  `edit_group` sobre los grupos que el usuario quiere resolver. Nunca
  re-extraigas ni re-captures para resolver agrupamientos pendientes, ni
  regeneres un plan (`review_grouping`) solo para volver a verlo.
- Planes obsoletos: cierra los planes `proposed` cuyo contenido ya fue
  resuelto por un plan aplicado posterior con `archive_grouping_plan` (no
  destructivo: no toca requerimientos).
- Relaciones documentadas: al rechazar una fusion y registrar la relacion con
  `link_requirements`, formalizala con `set_relation_status` (confirmed)
  solo tras aprobacion del usuario — preserva la nota original; `resolved` es
  exclusivo de `resolve_conflict` (contradicciones con winner).
- `apply_grouping_plan` solo tras aprobacion explicita del usuario (las
  fusiones son destructivas).
"""


def _resolve_target(host_workspace: Path, subpath: str) -> Path:
    """Resolve a subpath under the project workspace, refusing traversal.

    The pipeline runs host-side and reads from the bind-mounted workspace on
    disk; this guard keeps the target inside the project's own directory so a
    crafted relative path cannot reach another project or the host filesystem.
    """
    root = host_workspace.resolve()
    target = (root / subpath).resolve() if subpath else root
    if target != root and not str(target).startswith(str(root) + os.sep):
        raise ValueError(
            f"target '{subpath}' escapes the project workspace"
        )
    if not target.exists():
        raise ValueError(f"target '{subpath or '.'}' does not exist")
    return target


def _existing_gate(existing_count: int, on_existing: str) -> str:
    """Decide what the capture does given existing rows.

    Pure policy (no I/O) so it is unit-tested in isolation. Returns one of:
      - "block":   requirements exist and the caller has not decided yet -> the
                   tool must return pending_confirmation and NOT run.
      - "reset":   wipe existing rows first, then run.
      - "proceed": run without wiping (nothing exists, or the user chose append).

    on_existing must be "ask" (default), "reset" or "append"; anything else
    raises ValueError so the tool surfaces a clear error instead of silently
    defaulting.
    """
    if on_existing not in {"ask", "reset", "append"}:
        raise ValueError(
            "on_existing must be 'ask', 'reset' or 'append', got "
            f"{on_existing!r}"
        )
    if on_existing == "append":
        return "proceed"
    if on_existing == "reset":
        return "reset" if existing_count > 0 else "proceed"
    # on_existing == "ask"
    return "block" if existing_count > 0 else "proceed"


async def _count_existing(project_id: int) -> dict:
    """Count existing requirement + grouping rows for one project (preflight).

    Used by the capture guard to decide whether a capture would append over
    existing data. Counts ALL rows (including soft-deleted) so the guard fires
    whenever the project has been captured before, even if every row was later
    rejected -- matches capture_status accounting.
    """
    from sqlalchemy import func, select

    from backend.database import AsyncSessionLocal
    from backend.models.requirement import GroupingPlan, RequirementItem

    async with AsyncSessionLocal() as session:
        req_count = await session.scalar(
            select(func.count())
            .select_from(RequirementItem)
            .where(RequirementItem.project_id == project_id)
        )
        plan_count = await session.scalar(
            select(func.count())
            .select_from(GroupingPlan)
            .where(GroupingPlan.project_id == project_id)
        )
        last_code = await session.scalar(
            select(RequirementItem.code)
            .where(RequirementItem.project_id == project_id)
            .order_by(RequirementItem.id.desc())
            .limit(1)
        )
        return {
            "requirements": int(req_count or 0),
            "grouping_plans": int(plan_count or 0),
            "last_code": last_code,
        }


def _make_emitters():
    """Build (on_progress, on_event) callbacks that stream to the SSE relay.

    Uses LangGraph's custom stream channel (``get_stream_writer``) so events
    reach the SSE relay without changing the tool signature. ``get_stream_writer``
    only resolves inside a LangGraph execution context; outside one (smoke tests,
    direct invocation) it raises — we catch and stay silent so the tool still runs.

    ``on_progress`` carries coarse stage events (``extraction.progress``);
    ``on_event`` carries fine-grained events (``conflict.found``,
    ``validation.report``, ``requirement.added``) for live UI updates.
    """
    from langgraph.config import get_stream_writer

    async def _emit_progress(
        stage: str, message: str, extra: dict | None = None
    ) -> None:
        try:
            writer = get_stream_writer()
        except Exception:  # noqa: BLE001 — no graph context (smoke / direct call)
            return
        try:
            data: dict = {"stage": stage, "message": message}
            if extra:
                data.update(extra)
            writer({"event": "extraction.progress", "data": data})
        except Exception:  # noqa: BLE001 — never break the pipeline for progress
            return

    async def _emit_event(event_type: str, data: dict) -> None:
        try:
            writer = get_stream_writer()
        except Exception:  # noqa: BLE001 — no graph context (smoke / direct call)
            return
        try:
            writer({"event": event_type, "data": data})
        except Exception:  # noqa: BLE001 — never break the pipeline for an event
            return

    return _emit_progress, _emit_event


def _make_check_health_tool():
    """Build the check_capture_health tool.

    Wraps ``check_capture_environment`` (pure probes) and streams a coarse
    progress event so the UI shows 'verificando entorno' before the long capture
    run. No project closure is needed — the probes read global settings.
    """
    from backend.agents.pipelines.preflight import check_capture_environment

    @tool
    async def check_capture_health() -> dict:
        """Verify the capture environment BEFORE running the pipeline.

        Probes dependency imports (docling, opencv, tiktoken,
        sentence-transformers), LLM endpoint reachability, and workspace
        writability. Fast (<2s). Call this FIRST, before the capture stages, so
        a broken dependency fails here with a remediation hint instead of
        crashing mid-capture.
        """
        on_progress, _on_event = _make_emitters()
        await on_progress("preflight", "Verificando entorno de captura...")
        report = check_capture_environment()
        if not report.ok:
            failing = ", ".join(p.name for p in report.failures())
            await on_progress(
                "preflight_failed",
                f"Entorno con problemas: {failing}",
            )
        return report.as_dict()

    return check_capture_health


def _human_size(num_bytes: int) -> str:
    """Compact human-readable byte size for the document inventory."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{int(size)}B" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}GB"


def _make_orient_tool(host_workspace: Path):
    """Build the orient_documents tool (lightweight inventory, no parsing).

    Lists every client document under the target with type/size heuristics so
    the agent can reason about the corpus BEFORE the long capture. Does NOT
    extract text -- that is Docling job inside ``ingest_documents``.
    """

    @tool
    async def orient_documents(target_subpath: str = "") -> dict:
        """Inventory the project client documents (no text extraction).

        Returns each document relative path, type (extension) and size, plus
        the resolved target. Use this to orient yourself BEFORE running
        ingest_documents: decide document order, flag likely noise/boilerplate
        sections, and note diagrams. This does NOT parse the documents -- text
        extraction happens inside ingest_documents via Docling.
        """
        try:
            target = _resolve_target(host_workspace, target_subpath)
        except ValueError as exc:
            return {"error": str(exc)}

        root = host_workspace.resolve()
        docs = discover_documents(target)
        if not docs:
            return {
                "target": str(target.relative_to(root)),
                "documents": [],
                "total": 0,
                "message": (
                    "No se encontraron documentos bajo el target. Pide al "
                    "usuario que suba los documentos de cliente al workspace "
                    "del proyecto y reintenta."
                ),
            }
        inventory = []
        for p in docs:
            size = p.stat().st_size
            inventory.append({
                "path": str(p.relative_to(root)),
                "type": p.suffix.lower().lstrip(".") or "unknown",
                "size_bytes": size,
                "size": _human_size(size),
            })
        return {
            "target": str(target.relative_to(root)),
            "documents": inventory,
            "total": len(inventory),
        }

    return orient_documents


async def _mark_used_in_capture(
    project_id: int, docs: list[Path], host_workspace: Path
) -> None:
    """Mark the discovered documents as used_in_capture in ProjectDocument.

    Maps filesystem paths to rel_path (relative to workspace root) and updates
    the matching rows. Documents not in this batch keep their existing flag
    (append mode); reset mode already cleared all flags via reset_project_capture.
    """
    from sqlalchemy import update as sa_update
    from backend.database import AsyncSessionLocal
    from backend.models.project_document import ProjectDocument

    root = host_workspace.resolve()
    rel_paths: list[str] = []
    for d in docs:
        try:
            rel_paths.append(str(d.relative_to(root)))
        except ValueError:
            continue
    if not rel_paths:
        return
    async with AsyncSessionLocal() as session:
        for rp in rel_paths:
            await session.execute(
                sa_update(ProjectDocument)
                .where(
                    ProjectDocument.project_id == project_id,
                    ProjectDocument.rel_path == rp,
                )
                .values(used_in_capture=True)
            )
        await session.commit()


def _no_run(required_first: str) -> dict:
    """Standard reply when a stage tool runs with no active capture."""
    return {
        "error": "no_capture_in_progress",
        "message": (
            "No hay captura en curso. Llama a " + required_first + " primero "
            "para inicializar el holder de etapa."
        ),
    }


def _make_stage_tools(
    project_id: int,
    host_workspace: Path,
    project_name: str,
    project_description: str,
) -> list:
    """Build the seven staged-capture tools bound to one project.

    Each tool runs ONE pipeline stage, stores its typed output on the per-project
    ``CaptureRun`` (so the next stage consumes it without re-running), and emits
    the same fine events the atomic pipeline emits. ``commit_capture`` is the
    only DB writer and reads only from the holder. Loop caps (3/stage) stop a
    stuck agent from re-running a stage forever; the counters survive a
    re-ingest.
    """

    def _loop_err(exc: StageLoopExceeded) -> dict:
        return {
            "error": "stage_loop_exceeded",
            "stage": exc.stage,
            "calls": exc.calls,
            "cap": exc.cap,
            "message": (
                "La etapa " + exc.stage + " supero el tope de "
                + str(exc.cap) + " intentos. Reporta la situacion al usuario "
                "y espera su decision en vez de reintentar la misma etapa."
            ),
        }

    @tool
    async def ingest_documents(
        target_subpath: str = "", on_existing: str = "ask"
    ) -> dict:
        """Stage 1/6: parse the project documents with Docling (start capture).

        Discovers client documents under the target, runs Docling per document
        and annotates the structure map. This STARTS a capture: if one is
        already in progress it restarts from scratch. Stores chunks + structure
        maps on the run holder for the later stages. Call this first, before
        extract_requirements.

        Args:
            target_subpath: optional folder relative to the project workspace.
                Omit to process the whole project.
            on_existing: what to do when requirements already exist in this
                project. The default "ask" never silently appends: if any
                requirement exists, returns pending_confirmation (with the
                counts and last code) INSTEAD of starting -- surface those to
                the user, then call again with the choice BEFORE any parsing,
                so no work is wasted. Mirrors the existing-data guard.
                - "reset": delete ALL existing requirements + grouping plans
                  first (hard, irreversible), then parse. Use ONLY after the
                  user explicitly chose to start over.
                - "append": keep existing requirements and add the new ones.
        """
        try:
            target = _resolve_target(host_workspace, target_subpath)
        except ValueError as exc:
            return {"error": str(exc)}

        # Up-front existing-data guard: ask BEFORE parsing so the user decides
        # reset/append without waiting for the pipeline. No holder is created
        # on "block" -- the follow-up call starts clean.
        try:
            existing = await _count_existing(project_id)
            decision = _existing_gate(existing["requirements"], on_existing)
        except ValueError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 -- surface, never crash silently
            return {"error": "capture pre-check failed: " + str(exc)}

        if decision == "block":
            n_req = existing["requirements"]
            last_code = existing["last_code"]
            suffix = (" (ultimo codigo " + last_code + ")") if last_code else ""
            return {
                "pending_confirmation": True,
                "requirements": n_req,
                "grouping_plans": existing["grouping_plans"],
                "last_code": last_code,
                "message": (
                    "Ya existen " + str(n_req) + " requerimiento(s) en este "
                    "proyecto" + suffix + ". Pregunta al usuario si prefiere "
                    "resetear todo (borrar requerimientos y agrupamientos, "
                    "empezar de cero) o agregar a los existentes, y vuelve a "
                    "llamar a ingest_documents con on_existing=reset u "
                    "on_existing=append."
                ),
            }

        if decision == "reset":
            from backend.database import AsyncSessionLocal
            from backend.services.requirements_service import (
                reset_project_capture,
            )
            _on_progress_reset, _ = _make_emitters()
            await _on_progress_reset("reset", "Borrando captura previa...")
            try:
                async with AsyncSessionLocal() as session:
                    await reset_project_capture(session, project_id)
            except Exception as exc:  # noqa: BLE001
                return {"error": "reset before capture failed: " + str(exc)}

        run = get_or_create_run(
            project_id,
            target,
            project_name=project_name,
            project_description=project_description,
        )
        try:
            run.bump(STAGE_INGEST)
        except StageLoopExceeded as exc:
            return _loop_err(exc)
        # A fresh ingest restarts the pipeline: drop prior outputs but keep the
        # loop-cap counters so re-ingesting cannot dodge the cap.
        run.target = target
        run.reset_pipeline_outputs()

        on_progress, _on_event = _make_emitters()
        await on_progress(STAGE_INGEST, "discover", {"phase": "start"})
        t0 = time.perf_counter()
        docs = discover_documents(target)
        await on_progress(STAGE_INGEST, str(len(docs)) + " documento(s)")
        if not docs:
            return {
                "error": "no_documents",
                "target": str(target.relative_to(host_workspace.resolve())),
                "message": (
                    "No se encontraron documentos bajo el target. Pide al "
                    "usuario que suba los documentos de cliente al workspace."
                ),
            }

        # parser_hint por documento (Fase C): el usuario puede forzar glm-ocr en
        # /upload o /scan para PDFs rotados born-digital que la heurística auto
        # no pesca. {abs_path: hint} solo para los docs con hint explícito; los
        # demás caen a "auto" (el router decide).
        hint_map = await parser_hint_map(project_id, host_workspace.resolve())

        per_doc: list = []
        doc_texts: dict[str, str] = {}
        all_chunks: list = []
        for idx, d in enumerate(docs):
            chunks, smap = await parse_document_cached(
                d, parser_hint=hint_map.get(str(d.resolve()), "auto")
            )
            await enrich_structure_map(
                smap,
                project_name=run.project_name,
                project_description=run.project_description,
            )
            per_doc.append((chunks, smap))
            doc_texts[smap.document_id] = verification_text(chunks, smap)
            all_chunks.extend(chunks)
            await on_progress(
                STAGE_INGEST, "parseado " + d.name,
                {"current": idx + 1, "total": len(docs)},
            )

        run.timings[STAGE_INGEST] = (time.perf_counter() - t0) * 1000
        await on_progress(
            STAGE_INGEST, "ingestión lista",
            {"phase": "end", "elapsed_ms": round(run.timings[STAGE_INGEST])},
        )
        run.docs = docs
        run.per_doc = per_doc
        run.doc_texts = doc_texts
        run.all_chunks = all_chunks
        run.stages_done.add(STAGE_INGEST)

        # Mark discovered documents as used_in_capture so the SRS RAG
        # only searches within capture-processed documents.
        await _mark_used_in_capture(project_id, docs, host_workspace)

        return {
            "documents": [d.name for d in docs],
            "total_chunks": len(all_chunks),
            "stages_done": sorted(run.stages_done),
        }

    @tool
    async def discover_conventions() -> dict:
        """Stage 2/7: discover document conventions (priority legend, scope).

        Reads each ingested document once to extract the client's own priority
        legend (e.g. "Alta/Media/Baja"), the per-requirement priority field
        label, scope markers (out-of-scope tags) and a glossary. Pure verbatim
        extraction; resolution (legend->MoSCoW) happens later in classify. The
        merged DocumentRules are stored on the holder and threaded into
        extract, critique and classify. Best-effort: a failure here degrades to
        empty rules -> consumers fall back to verb-based defaults, the pipeline
        never aborts. Requires ingest_documents first.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("ingest_documents")
        try:
            run.bump(STAGE_CONVENTIONS)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, _on_event = _make_emitters()
        await on_progress(
            STAGE_CONVENTIONS, "convenciones del documento", {"phase": "start"}
        )
        t0 = time.perf_counter()
        per_doc_rules = []
        for chunks, smap in run.per_doc:
            doc_rules = await extract_conventions(
                smap,
                project_name=run.project_name,
                project_description=run.project_description,
            )
            per_doc_rules.append(doc_rules)
        document_rules = merge_conventions(per_doc_rules)

        run.timings[STAGE_CONVENTIONS] = (time.perf_counter() - t0) * 1000
        run.document_rules = document_rules
        run.stages_done.add(STAGE_CONVENTIONS)
        await on_progress(
            STAGE_CONVENTIONS, "convenciones listas",
            {
                "phase": "end",
                "elapsed_ms": round(run.timings[STAGE_CONVENTIONS]),
                "priority_legend_entries": len(document_rules.priority_legend),
                "scope_markers": len(document_rules.scope_markers),
                "has_priority_field_label": bool(document_rules.priority_field_label),
            },
        )
        return {
            "priority_legend_entries": len(document_rules.priority_legend),
            "priority_field_label": document_rules.priority_field_label,
            "scope_markers": len(document_rules.scope_markers),
            "glossary_terms": len(document_rules.glossary),
            "stages_done": sorted(run.stages_done),
        }

    @tool
    async def extract_requirements() -> dict:
        """Stage 3/7: extract raw requirements from the ingested chunks.

        Runs the guided extractor (verify_spans inside -- the anti-hallucination
        anchor), then per-document gap pass and an implicit-pass, dropping
        duplicit implicits. Every persisted requirement is born here with a
        verified source span. Requires ingest_documents first. Threads the
        document conventions (priority_field_label) discovered in
        extract_conventions so the extractor surfaces a priority_hint per item.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("ingest_documents")
        try:
            run.bump(STAGE_EXTRACT)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, _on_event = _make_emitters()
        await on_progress(STAGE_EXTRACT, "extraccion", {"phase": "start"})
        t0 = time.perf_counter()

        async def _extract_progress(done: int, total: int):
            await on_progress(
                STAGE_EXTRACT, f"{done}/{total} fragmentos",
                {"current": done, "total": total},
            )

        extracted = await extract_all(
            run.all_chunks,
            doc_texts=run.doc_texts,
            project_name=run.project_name,
            project_description=run.project_description,
            rules=run.document_rules,
            on_progress=_extract_progress,
        )
        gap_results = await asyncio.gather(*[
            gap_pass(
                chunks,
                smap,
                extracted,
                project_name=run.project_name,
                project_description=run.project_description,
            )
            for chunks, smap in run.per_doc
        ])
        for gaps in gap_results:
            extracted.extend(gaps)
        implicit_chunks = filter_boilerplate_chunks(
            run.all_chunks, [smap for _, smap in run.per_doc]
        )
        await on_progress(
            STAGE_EXTRACT, "requerimientos implícitos",
            {},
        )

        async def _implicit_progress(done: int, total: int):
            await on_progress(
                STAGE_EXTRACT, f"implícitos {done}/{total}",
                {"current": done, "total": total},
            )

        implicits = await implicit_pass(
            implicit_chunks,
            doc_texts=run.doc_texts,
            project_name=run.project_name,
            project_description=run.project_description,
            on_progress=_implicit_progress,
        )
        implicits = drop_duplicit_implicit(implicits, extracted)
        extracted.extend(implicits)

        run.timings[STAGE_EXTRACT] = (time.perf_counter() - t0) * 1000
        run.extracted = extracted
        run.stages_done.add(STAGE_EXTRACT)
        await on_progress(
            STAGE_EXTRACT, str(len(extracted)) + " items crudos",
            {"phase": "end", "elapsed_ms": round(run.timings[STAGE_EXTRACT])},
        )
        return {
            "raw_items": len(extracted),
            "stages_done": sorted(run.stages_done),
        }

    @tool
    async def consolidate_requirements() -> dict:
        """Stage 4/7: deduplicate and detect contradictions (propose, never auto-resolve).

        Runs exact + two-tier semantic dedup and contradiction detection over
        the extracted items. Emits a conflict.found event per duplicate and
        contradiction so the UI shows them live. Duplicates/contradictions are
        PROPOSED -- the human decides. Requires extract_requirements first.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("ingest_documents")
        try:
            run.bump(STAGE_CONSOLIDATE)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, on_event = _make_emitters()
        await on_progress(STAGE_CONSOLIDATE, "dedup + contradicciones", {"phase": "start"})
        t0 = time.perf_counter()
        cons = await consolidate(run.extracted)
        for dup in cons.duplicates:
            await on_event("conflict.found", {
                "kind": "duplicate",
                "kept_id": dup.kept_id,
                "member_ids": dup.member_ids,
                "kept_statement": dup.kept_statement,
            })
        for pair in cons.contradictions:
            await on_event("conflict.found", {
                "kind": "contradiction",
                "a_id": pair.a_id,
                "b_id": pair.b_id,
                "reason": pair.reason,
                "confidence": pair.confidence,
            })

        run.timings[STAGE_CONSOLIDATE] = (time.perf_counter() - t0) * 1000
        run.cons = cons
        run.stages_done.add(STAGE_CONSOLIDATE)
        await on_progress(
            STAGE_CONSOLIDATE, "consolidación lista",
            {"phase": "end", "elapsed_ms": round(run.timings[STAGE_CONSOLIDATE])},
        )
        return {
            "items": len(cons.items),
            "duplicates": len(cons.duplicates),
            "contradictions": len(cons.contradictions),
            "stages_done": sorted(run.stages_done),
        }

    @tool
    async def critique_requirements() -> dict:
        """Stage 5/7: critical review (drop non-requirements / hallucinations).

        Runs the generator-critic loop over the consolidated items, dropping
        legends/boilerplate/meta-instructions and likely hallucinations, and
        flagging items that need human review. The document conventions
        (priority legend, scope markers, glossary) are surfaced in the prompt
        so the critic does NOT flag the client's own signals as noise. Emits a
        validation.report event with the kept/rejected/flagged counts. Requires
        consolidate_requirements first.
        """
        run = get_run(project_id)
        if run is None or run.cons is None:
            return _no_run("consolidate_requirements")
        try:
            run.bump(STAGE_CRITIQUE)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, on_event = _make_emitters()
        await on_progress(STAGE_CRITIQUE, "revision critica", {"phase": "start"})
        t0 = time.perf_counter()

        async def _crit_progress(done: int, total: int):
            await on_progress(
                STAGE_CRITIQUE, f"{done}/{total} requerimientos",
                {"current": done, "total": total},
            )

        crit = await critique_all(
            run.cons.items, rules=run.document_rules,
            on_progress=_crit_progress,
        )
        await on_event("validation.report", {
            "total": len(run.cons.items),
            "kept": crit.stats.get("kept", len(crit.items)),
            "rejected": len(crit.rejected),
            "flagged": len(crit.flagged),
        })

        run.timings[STAGE_CRITIQUE] = (time.perf_counter() - t0) * 1000
        run.crit = crit
        run.stages_done.add(STAGE_CRITIQUE)
        await on_progress(
            STAGE_CRITIQUE, "crítica lista",
            {"phase": "end", "elapsed_ms": round(run.timings[STAGE_CRITIQUE])},
        )
        return {
            "kept": crit.stats.get("kept", len(crit.items)),
            "rejected": len(crit.rejected),
            "flagged": len(crit.flagged),
            "stages_done": sorted(run.stages_done),
        }

    @tool
    async def classify_requirements() -> dict:
        """Stage 6/7: classify (type + MoSCoW + decomposition).

        Runs the classifier over the critiqued items, assigning type
        (functional/non-functional/...), priority (MoSCoW) and optional
        decomposition into sub-items. PRIORITY precedence: item priority_hint +
        document legend wins over obligation verb; scope markers force WONT.
        Requires critique_requirements first.
        """
        run = get_run(project_id)
        if run is None or run.crit is None:
            return _no_run("critique_requirements")
        try:
            run.bump(STAGE_CLASSIFY)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, _on_event = _make_emitters()
        await on_progress(STAGE_CLASSIFY, "clasificacion", {"phase": "start"})
        t0 = time.perf_counter()
        cls = await classify_all(run.crit.items, rules=run.document_rules)

        run.timings[STAGE_CLASSIFY] = (time.perf_counter() - t0) * 1000
        run.cls = cls
        run.stages_done.add(STAGE_CLASSIFY)
        await on_progress(
            STAGE_CLASSIFY, "clasificación lista",
            {"phase": "end", "elapsed_ms": round(run.timings[STAGE_CLASSIFY])},
        )
        return {
            "classified": len(cls.decisions),
            "sub_items": cls.stats.get("sub_items", 0),
            "stages_done": sorted(run.stages_done),
        }

    @tool
    async def commit_capture() -> dict:
        """Stage 7/7: persist the staged requirements (the only DB writer).

        Writes the classified items to the store with opaque REQ-XXXX codes and
        derived sub-items. Requires every prior stage to have run; refuses
        (missing_stages) otherwise.

        The existing-data guard already ran in ingest_documents (up-front,
        BEFORE parsing): by the time commit runs the user has already chosen
        reset/append, so commit takes no on_existing argument and does not
        re-ask -- it just persists the staged items.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("ingest_documents")
        try:
            run.bump(STAGE_COMMIT)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        missing = run.missing_stages_before_commit()
        if missing:
            return {
                "error": "missing_stages",
                "missing": missing,
                "message": (
                    "Faltan etapas previas antes de commit_capture. Llama a "
                    "cada una en orden (ingest -> discover_conventions -> "
                    "extract -> consolidate -> critique -> classify) y vuelve "
                    "a intentar."
                ),
            }

        on_progress, on_event = _make_emitters()
        from backend.database import AsyncSessionLocal
        from backend.services.requirements_service import _persist

        await on_progress("persist", "guardando", {"phase": "start"})
        t0 = time.perf_counter()
        try:
            async with AsyncSessionLocal() as session:
                ids = await _persist(
                    session, project_id, run.crit.items, run.cls, on_event
                )
        except Exception as exc:  # noqa: BLE001 -- surface to the model
            return {"error": "persist failed: " + str(exc)}

        run.timings["persist"] = (time.perf_counter() - t0) * 1000
        await on_progress(
            "persist", "persistencia lista",
            {"phase": "end", "elapsed_ms": round(run.timings["persist"])},
        )

        run.committed_ids = ids
        total_ms = round(sum(run.timings.values()))
        summary = {
            "persisted": len(ids),
            "sub_items": run.cls.stats.get("sub_items", 0) if run.cls else 0,
            "raw_extracted": len(run.extracted),
            "duplicates_proposed": len(run.cons.duplicates) if run.cons else 0,
            "contradictions": len(run.cons.contradictions) if run.cons else 0,
            "flagged_for_review": len(run.crit.flagged) if run.crit else 0,
            "rejected_hallucination": len(run.crit.rejected) if run.crit else 0,
            "timings": dict(run.timings),
            "total_ms": total_ms,
        }
        await on_progress(
            "done", str(len(ids)) + " requerimientos",
            {"timings": dict(run.timings), "total_ms": total_ms},
        )
        clear_run(project_id)
        return summary

    return [
        ingest_documents,
        discover_conventions,
        extract_requirements,
        consolidate_requirements,
        critique_requirements,
        classify_requirements,
        commit_capture,
    ]


def make_requirements_capture_agent_subagent(
    *,
    project_id: int,
    profile: str,
    project_slug: str,
    project_name: str = "",
    project_description: str = "",
) -> dict[str, Any]:
    """Build the agent-driven requirements-capture subagent for DeepAgents.

    Seven staged tools (ingest -> discover_conventions -> extract -> consolidate
    -> critique -> classify -> commit) backed by a stateful run-holder, so the
    agent reasons BETWEEN stages instead of firing one atomic tool. Plus
    orient_documents, the health pre-flight, and the shared
    editing/grouping/vision tools. deepagents injects FilesystemMiddleware
    into every subagent spec; ``NoFilesystemToolsMiddleware`` hides those
    tools from the model so ingestion goes through ingest_documents (Docling)
    instead of improvised parsing. ``commit_capture`` is the
    only DB writer and reads only from the holder (span-verified items from
    extract_requirements).
    """
    # Imported here to avoid a config import at module load time (tests that
    # rebind settings work without surprises).
    from backend.config import settings

    host_workspace = settings.workspaces_root / profile / project_slug

    health_tool = _make_check_health_tool()
    orient_tool = _make_orient_tool(host_workspace)
    stage_tools = _make_stage_tools(
        project_id, host_workspace, project_name, project_description
    )
    editing_tools = make_requirements_tools(project_id)
    grouping_tools = make_grouping_tools(project_id)
    vision_tools = make_vision_tools(profile, project_slug)

    return {
        "name": "requirements-capture-agent",
        "description": (
            "Captura de requerimientos de InfoFact: razona la extraccion etapa "
            "por etapa (orienta los documentos, planifica, orquesta las siete "
            "etapas del pipeline y refina) en lugar de disparar una sola tool. "
            "Usalo cuando el usuario use el comando /captura o pida una captura "
            "guiada con instrucciones de steering. Tambien es el encargado del "
            "store de requerimientos: edicion de items, agrupamiento de "
            "duplicados (revisar planes pendientes, aceptar/rechazar grupos, "
            "cambiar keeper, aplicar fusiones) y vision de documentos. Las "
            "tareas de agrupamiento NO ejecutan captura: delega solo la "
            "curacion, sin re-extraer. Los guardrails viven dentro de las "
            "tools, no en el prompt."
        ),
        "system_prompt": REQUIREMENTS_CAPTURE_AGENT_PROMPT,
        "tools": [
            health_tool,
            orient_tool,
            *stage_tools,
            *editing_tools,
            *grouping_tools,
            *vision_tools,
        ],
        # deepagents NO propaga el middleware del orquestador a los
        # subagentes (pero SI les inyecta FilesystemMiddleware): cada spec
        # necesita su guarda de tamaño y su filtro de tools de filesystem.
        "middleware": [SizeGuardMiddleware(), NoFilesystemToolsMiddleware()],
    }
