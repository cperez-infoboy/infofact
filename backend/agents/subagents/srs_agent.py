"""Subagente AGENTICO de generación de SRS (comando ``/srs``).

Espejo de ``requirements_capture_agent`` pero para la SÍNTESIS del SRS:
razona la generación etapa por etapa (calidad -> goals -> cobertura ->
commit) sobre los requerimientos vivos del proyecto. Cada herramienta de
etapa acumula su salida en ``SrsRun`` (holder) y emite eventos finos al
SSE; solo ``commit_srs`` persiste el ``SrsDocument``.

Por qué un subagente aparte de la captura: la captura EXTRAE/cura reqs; el
SRS SINTETIZA el entregable quality-assured. Son flujos distintos con
herramientas distintas (aquí no hay ingest/extract, hay analyze/infer/commit).

Orden de etapas (corrección del plan): cobertura lee los goals persistidos,
así que va DESPUÉS de goals. La dependencia es real (``compute_coverage``
llama ``list_goals``); invertirla produciría cobertura de goals vacía.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from backend.agents.subagents.capture_run_holder import StageLoopExceeded
from backend.agents.subagents.srs_run_holder import (
    STAGE_COMMIT,
    STAGE_COVERAGE,
    STAGE_GOALS,
    STAGE_NARRATIVE,
    STAGE_QUALITY,
    STAGE_SEED,
    clear_run,
    get_or_create_run,
    get_run,
)
from backend.agents.no_fs_tools import NoFilesystemToolsMiddleware
from backend.agents.sandboxes.docker_sandbox import DockerSandbox
from backend.agents.llm_retry_guard import EmptyResponseRetryMiddleware
from backend.agents.size_guard import (
    SizeGuardMiddleware,
    make_summarization_middleware,
)
from backend.agents.tools.documents_tools import make_document_read_tools
from backend.agents.tools.srs_tools import make_srs_read_tools
from backend.database import AsyncSessionLocal
from backend.models.requirement import ReqStatus
from backend.models.srs import (
    FindingStatus,
    GoalStatus,
    LinkRelation,
    SrsStatus,
)
from backend.services import goals_engine, srs_coverage, srs_quality, srs_store
from backend.services.requirement_store import list_requirements
from backend.services.srs_assembler import (
    SRS_STRUCTURE,
    _capture_sources,
    _carryover_narrative,
    _draft_narrative,
    _functional_goal_groups,
    draft_narrative_llm,
)
from backend.services.srs_builder import build_srs

logger = logging.getLogger(__name__)

_LIVE_STATUSES = frozenset(
    {ReqStatus.VALIDATED, ReqStatus.APPROVED, ReqStatus.DRAFT}
)

# Severidades cuya detección merece un evento fino individual al SSE (las
# accionables). minor/info se reportan solo en el agregado del retorno de la
# etapa, para no inundar el stream.
_FINDING_EVENT_SEVERITIES = frozenset({"blocker", "major"})


SRS_AGENT_PROMPT = """\
Eres el subagente AGENTICO de generación de SRS de InfoFact. Tu trabajo es \
producir una Especificación de Requerimientos de Software de alta calidad, \
analizada en calidad y con modelado de goals, a partir de los requerimientos \
YA capturados del proyecto. NO extraes requerimientos (eso lo hace la captura); \
los SINTETIZAS y les aplicas análisis de calidad.

Trabajas por ETAPAS, razonando entre cada una:

0. seed_from_last_srs (CONDICIONAL, va primero) — Si ya existe un SRS y el \
usuario pide revisar/ajustar la NARRATIVA de ese documento o REFRESCARLO \
tras una curacion de trazabilidad o edicion de reqs, usa esta tool \
PRIMERO: reusa los hallazgos, goals, cobertura Y NARRATIVA AUTHORED de la \
ultima version y sigue directo a draft_narrative + commit_srs (una version \
nueva en minutos, sin re-juzgar cientos de enunciados). La siembra tolera \
la curacion del store posterior al documento (merges, ediciones, reqs \
nuevos, links): descarta los hallazgos de reqs ya no vivos, reporta el \
diff, y commit_srs re-proyecta trazabilidad y conteos desde el store vivo. \
Si devuelve no_previous_srs o stale_store (store reemplazado por una \
captura nueva), corre el pipeline completo desde analyze_quality. En ese \
flujo, pasa las indicaciones narrativas del usuario al parametro \
`instructions` de `draft_narrative`.
1. analyze_quality  — Analiza la calidad de los requerimientos vivos: \
pre-checks programáticos (INCOSE, requirement smells, EARS) + evaluación LLM \
de ambigüedad semántica. Es DELTA: re-juzga por LLM solo los ítems nuevos o \
editados; los intactos conservan su veredicto previo (por eso los blockers \
conocidos NO cambian de una corrida a la siguiente). El retorno trae \
`blockers_detail`: el inventario COMPLETO de blockers del proyecto.
2. infer_goals      — Infiere el MODELO DE GOALS (GORE): goals funcionales \
(el "para qué"), softgoals NFR (puente con ISO 25010) y obstacles (riesgos a \
mitigar). Vincula cada goal a los requerimientos que lo realizan \
(GoalLink: REALIZES/CONTRIBUTES/CONFLICTS).
3. check_coverage   — Audita la cobertura: ISO/IEC 25010 (qué características \
de calidad no tienen ningún requerimiento), presencia de secciones 29148 y \
cobertura de goals (goals sin reqs que los realicen = gap).
4. draft_narrative  — Redacta con LLM las 7 subsecciones authored del SRS \
(propósito, alcance, definiciones, perspectiva, usuarios, entorno \
y supuestos) usando contexto RAG de los documentos fuente de captura. La \
seccion 1.4 Referencias NO se redacta: se proyecta sola desde el catalogo \
de fuentes de captura. Puedes \
consultar los documentos con search_documents / get_document_passage antes de \
redactar. IMPORTANTE: si el usuario dio indicaciones narrativas, pasalas \
SIEMPRE al parametro `instructions` — es el UNICO canal que llega al \
redactor (el contexto se arma desde el store + RAG; tu conversacion NO se \
le pasa). Cargar citas a tu propio contexto NO basta. Sobre un run \
SEMBRADO, sin instrucciones el texto authored previo se conserva VERBATIM \
(unicamente se re-proyectan conteos y secciones deterministicas) y con \
instrucciones el redactor lo recibe como base a revisar: NUNCA re-drafta \
de cero ni emite notas provisionales.
5. commit_srs       — Persiste el SrsDocument CANDIDATE: combina narrativa \
+ secciones proyectadas + hallazgos + goals + cobertura + matriz de \
trazabilidad. Solo esta etapa escribe la DB.

Mantenimiento de trazabilidad (sin re-correr el pipeline): si el usuario pide \
completar o corregir la vinculacion goal <-> requerimiento de reqs concretos, \
usa goal_coverage (que reqs vivos no aportan a ningun goal) + \
infer_goal_links(req_codes) (re-infiere links SOLO de esos reqs contra los \
goals ya persistidos) o link_goal / unlink_goal para aristas puntuales. \
NO re-corras infer_goals entera para arreglar links puntuales: reemplazaria \
los links de todo el modelo. Re-correr infer_goals es seguro para los codigos \
(upsert: los goals existentes conservan GOAL-XXXX y la curacion humana), pero \
no es el camino para completar trazabilidad de unos pocos reqs.

CONSOLIDACION DEL CATALOGO DE GOALS (curacion quirurgica, sin re-inferencia): \
si el usuario pide consolidar goals (p. ej. el modelo quedo con muchos STALE \
o solapados y hay un plan de fusion acordado), usa:
- edit_goal(goal_code, statement?, rationale?, status?) — resucitar un STALE \
(proposed), confirmar uno curado (confirmed) o retirar uno mal inferido \
(rejected). CONFIRMED/REJECTED son decision humana: las re-inferencias los \
preservan y NO los re-emiten.
- merge_goals(keeper_code, absorbed_codes, statement?, rationale?) — fusiona \
N goals que dicen lo mismo en el keeper: las aristas se RE-APUNTAN in-place \
(la curacion humana de cada link viaja), los absorbidos quedan REJECTED y \
los sub-goals colgantes se re-parentan. Un merge por llamada; presenta el \
plan de fusiones al usuario antes de aplicar. El proximo commit re-proyecta \
el anexo de goals con el catalogo consolidado (se elimina la divergencia \
goals.total del anexo vs resumen oficial).
Tras una curacion (link_goal/link_goals/unlink_goal/merges/ediciones), \
refresca el DOCUMENTO con seed_from_last_srs + draft_narrative + commit_srs: \
la version nueva re-proyecta trazabilidad y conteos desde el store vivo. \
NUNCA re-corras el pipeline completo por una curacion: infer_goals \
reemplaza los links de los goals re-inferidos y PISA la curacion recien \
hecha. Para vincular N pares ya decididos usa link_goals (hasta 40 \
entradas en un llamado); infer_goal_links(req_codes) es para INFERIR los \
links de un lote de reqs.

RUTA DE CAMBIO: elige SIEMPRE la mas liviana que aplique.
- Ajuste de REDACCION del documento (insertar o corregir una nota, reword de \
una subseccion authored, sin tocar enunciados): patch_narrative_section. \
Sin version nueva, sin re-proyeccion: segundos en vez de un commit completo. \
Una nota de la regla N agregada al documento = un patch, no una version.
- Cambio en requerimientos (ediciones, merges, links) que deba reflejarse \
en el documento: seed_from_last_srs + draft_narrative + commit_srs.
- Si commit_srs responde no_new_version, el contenido ya era identico a la \
version vigente: infórmalo y ofrece patch_narrative_section si lo que el \
usuario queria era un ajuste de texto.

VERIFICACION POST-COMMIT LIVIANA: tras commit_srs NO releyas el documento. \
get_latest_srs SIN argumentos trae solo metadatos e indices (la version \
completa pesa megabytes y satura tu ventana: asi cayo la sesion 17 de \
Planitrack2.0). Confirma con version/requirement_count del propio retorno \
del commit + list_srs_sections + conteos de get_coverage / goal_coverage. \
read_srs_section solo para verificar subsecciones puntuales (p. ej. la nota \
que acabas de insertar), NUNCA seccion por seccion el documento entero.

REPORTE DE BLOCKERS (regla dura): la TABLA COMPLETA (req, regla, problema, \
acción sugerida, requiere-decisión sí/no) con TODOS los blockers abiertos \
aparece UNA VEZ POR CONVERSACION: en el informe del commit, o en la \
PROPUESTA DE CURA antes de aplicar apply_cures. En informes intermedios y \
re-análisis reporta CONTEOS + delta (findings_merge: preserved/reopened + \
cuáles son nuevos, por código, sin la tabla): repetir la tabla entera en \
cada mensaje apila decenas de KB en el hilo y satura la ventana. La \
herramienta nunca te trunca el inventario: list_run_findings(severity=\
"blocker") pagina lo que necesites para armar la tabla del commit o de la \
cura.

CURA DE BLOCKERS POR OLAS (el anti-loop): una OLA es un lote de curas que \
se cierra con UN solo re-análisis. Regla dura: NUNCA pidas un análisis \
entre tandas de la misma ola — curar y re-analizar en loop "descubría" \
hallazgos cada vez más finos sobre el texto re-escrito y la curación \
parecía no terminar nunca.
1. PROPON — agrupa los hallazgos por patrón (suelen compartir causa: notas \
"pendiente" en el enunciado, umbrales sin métrica, actores sin rol, \
pronombres). Para cada grupo redacta la corrección y clasifícala: MECÁNICA \
(respaldo literal en la fuente, verificado con search_documents / \
get_document_passage) o DECISIÓN (la fuente calla; requiere elección del \
usuario). Presenta TODO en una sola tabla.
2. APLICA en tandas tras la aprobación — apply_cures(edits=[...], \
waives=[...]) ejecuta ediciones + cierres formales y NO re-analiza: cada \
tanda registra la OLA (cap 50 ediciones por llamada: divide y sigue la \
misma ola). Para hallazgos que el usuario decide tolerar usa \
resolve_findings(status="waived", note=...): lo cerrado NO reaparece \
mientras el enunciado no cambie.
3. CIERRA LA OLA UNA VEZ — close_curation_wave al terminar TODAS las \
tandas: corre UN re-análisis delta (solo re-juzga lo editado) y devuelve \
el DELTA de la ola: resueltos por la cura vs nuevos sobre el texto \
re-escrito. Reporta ESE delta al usuario: es la prueba de progreso de la \
ola. Los hallazgos nuevos menores van a la SIGUIENTE ola o a waives con \
decisión del usuario: NO abras una cura inmediata por cada uno. \
commit_srs cierra cualquier ola olvidada (guard previo al commit), pero el \
reporte delta solo lo entrega close_curation_wave.
4. REFRESCA el documento si el usuario lo pide — seed_from_last_srs -> \
draft_narrative -> commit_srs (minutos).

TRAZABILIDAD COMPLETA: check_coverage informa los requerimientos vivos SIN \
goal (no aportan a ningún objetivo del modelo). Si tras check_coverage hay \
unlinked > 0, cierra esa cola ANTES del commit con infer_goal_links(\
req_codes=...) — es barato (~8 llamadas por 300 reqs) y usa los goals ya \
persistidos — y re-corre check_coverage. Si queda un residuo que no se puede \
vincular con confianza, repórtalo como deuda explícita en el informe final: \
nunca lo ocultes.

Flujo:
1. ORIENTAR — Confirma que hay requerimientos vivos (si no,informa al usuario \
que primero debe capturar requerimientos con /captura_agente).
2. ORIENTARSE EN LAS FUENTES — Usa list_documents y search_documents para \
revisar los documentos que dieron origen a los requerimientos antes de \
redactar el SRS.
3. ORQUESTAR LAS ETAPAS — Ejecuta analyze_quality -> infer_goals -> \
check_coverage -> draft_narrative -> commit_srs EN ESE ORDEN (cobertura \
depende de los goals; narrativa usa el contexto acumulado). Excepcion: si \
la peticion es una actualizacion de solo-narrativa sobre un SRS existente, \
arranca con seed_from_last_srs (etapa 0) y salta a draft_narrative.
4. REFINAR — Si analyze_quality halla blockers, aplica la regla REPORTE DE \
BLOCKERS: tabla completa en tu informe, con la propuesta de cura agrupada \
por patrón (bloque CURA DE BLOCKERS). El usuario decide; no cures sin \
aprobación (salvo reposición literal respaldada en fuente, si las reglas \
del proyecto lo permiten).
5. REPORTAR — Tras commit_srs, resume: versión generada, conteo de reqs, \
hallazgos por severidad (con findings_merge: cuánta curación se preservó), \
la tabla COMPLETA de blockers si quedan abiertos, gaps de cobertura, reqs \
sin goal y goals inferidos. ANTES de reportar exito sobre un ajuste pedido, \
VERIFICA con `read_srs_section` que las secciones objetivo contienen lo \
pedido: nunca afirmes contenido que no leiste de vuelta del SRS persistido.

Reglas estrictas:
- NO inventes requerimientos ni goals. Los goals se infieren SOLO de los \
requerimientos vivos del proyecto.
- El idioma de los enunciados, suggestions y rationale se PRESERVA (no \
traduzcas). Los mensajes van en español neutro.
- Conserva el ciclo de cada etapa (cap de 3 intentos por etapa): si una \
etapa falla reiteradamente, reportalo en vez de entrar en loop.
- Tras commit_srs el SRS queda en estado CANDIDATE; el usuario lo revisa \
(edita narrativa, marca pendientes) y lo cierra (LOCKED) desde la UI.
- Las versiones se pueden DESCARTAR (discard_srs_version o la UI): una \
versión DISCARDED queda listada pero deja de ser la «última» — el próximo \
seed_from_last_srs siembra desde la anterior. LOCKED no se puede descartar.
- REGLAS PERSISTENTES: el proyecto tiene consideraciones duraderas (scope srs \
+ all) que el contexto narrativo inyecta automaticamente como bloque \
PROJECT_RULES. Si el usuario pide que un criterio de redaccion valga "de \
ahora en mas" (terminologia obligatoria, tono, secciones que no pueden \
faltar), registrilo con add_project_rule (scope srs o all) para que persista \
en futuras generaciones del SRS; si lo revoca, retire_project_rule. Las \
indicaciones de UNA generacion siguen yendo por `instructions`.
"""


# ---------------------------------------------------------------------------
# Emisores de eventos al SSE (espejo de requirements_capture._make_emitters).
# Usa el canal custom de LangGraph (get_stream_writer) que chat.py relayea
# sin cambios. try/except anidado: un fallo del relay NUNCA rompe la etapa.
# ---------------------------------------------------------------------------


def _make_emitters():
    from langgraph.config import get_stream_writer

    async def _emit_progress(stage: str, message: str) -> None:
        try:
            writer = get_stream_writer()
        except Exception:  # noqa: BLE001 — fuera de contexto de grafo
            return
        try:
            writer({"event": "srs.progress",
                    "data": {"stage": stage, "message": message}})
        except Exception:  # noqa: BLE001
            return

    async def _emit_event(event_type: str, data: dict) -> None:
        try:
            writer = get_stream_writer()
        except Exception:  # noqa: BLE001
            return
        try:
            writer({"event": event_type, "data": data})
        except Exception:  # noqa: BLE001
            return

    return _emit_progress, _emit_event


def _refresh_run_metrics(run, live: list) -> None:
    """Re-proyecta los totales del run sobre el store vivo.

    Tras un seed_from_last_srs, quality_summary/coverage son el snapshot del
    documento sembrado: la curación posterior (merges, ediciones, reqs
    nuevos) los deja desfasados. Esos totales alimentan el bloque «Resumen
    del alcance especificado» de §2.1 (el determinista lo arma y
    draft_narrative_llm lo reapende) y las métricas del contexto del
    redactor; commit_srs re-proyecta trazabilidad y requirement_codes desde
    el store vivo, así que sin este refresh la versión nueva mezclaba
    conteos viejos con catálogo vivo (sesión 53: §2.1 decía 807/518 con un
    store de 804/515 en v8, v9 y v10). goals_summary se conserva: la
    curación de links no altera el modelo de goals; run.findings ya viene
    filtrado de reqs no vivos por el seed.
    """
    coverage = dict(run.coverage or {})
    totals = dict(coverage.get("totals") or {})
    functional = nfr = 0
    for it in live:
        chars = srs_coverage.REQTYPE_TO_25010.get(it.type, [])
        if "functional_suitability" in chars:
            functional += 1
        elif chars:
            nfr += 1
    totals["live"] = len(live)
    totals["functional"] = functional
    totals["nfr"] = nfr
    coverage["totals"] = totals
    run.coverage = coverage

    findings = run.findings or []
    summary = dict(run.quality_summary or {})
    # Recuento real de lo que replace_findings va a persistir (calidad +
    # cobertura), no el snapshot del documento sembrado.
    summary["total_findings"] = len(findings)
    summary["blockers"] = sum(
        1 for f in findings if f.get("severity") == "blocker"
    )
    run.quality_summary = summary


def _no_run(first_tool: str) -> dict[str, Any]:
    return {
        "error": "no_active_run",
        "message": (
            f"No hay un run SRS activo. Invoca primero `{first_tool}` (se crea "
            "al iniciar el run) o reinicia el comando /srs."
        ),
    }


def _loop_err(exc: StageLoopExceeded) -> dict[str, Any]:
    return {
        "error": "stage_loop_exceeded",
        "stage": exc.stage,
        "calls": exc.calls,
        "cap": exc.cap,
        "message": (
            f"{exc} El cap acota el loop de la etapa dentro de este episodio: "
            "informe al usuario que reintente con el comando /srs (el rearmado "
            "de contadores es router-level; no existe una tool para ello)."
        ),
    }


# ---------------------------------------------------------------------------
# Stage tools.
# ---------------------------------------------------------------------------


def _make_stage_tools(
    project_id: int,
    project_name: str = "",
    project_description: str = "",
) -> list:
    """Construye las 4 herramientas de etapa, cerrando sobre project_id."""

    @tool
    async def seed_from_last_srs() -> dict:
        """Atajo: siembra el run desde el ultimo SRS persistido (solo narrativa).

        Usalo cuando el usuario pida REVISAR la narrativa de un SRS ya generado
        (p. ej. "actualiza el alcance") sin haber cambiado el store: carga los
        hallazgos, goals y cobertura PERSISTIDOS de la ultima version al holder
        y marca las etapas 1-3 como hechas, para que sigas directo a
        draft_narrative + commit_srs (una version nueva en minutos, sin
        re-juzgar cientos de enunciados). Los hallazgos conservan la curacion
        de estado hecha en la UI. Guarda de staleness: rechaza solo si el
        solape entre los codigos del documento y los reqs vivos cae bajo el
        50% (captura que reemplazo el store); la curacion posterior (merges,
        ediciones, reqs nuevos, links) NO bloquea y commit_srs re-proyecta
        trazabilidad y conteos desde el store vivo.
        """
        run = get_or_create_run(
            project_id,
            project_name=project_name,
            project_description=project_description,
        )
        # La siembra es un punto de arranque: limpia salidas previas del run.
        run.reset_pipeline_outputs()
        try:
            run.bump(STAGE_SEED)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        from sqlalchemy import select as sa_select

        from backend.models.requirement import RequirementItem

        async with AsyncSessionLocal() as session:
            srs = await srs_store.get_latest_srs(session, project_id)
            if srs is None:
                return {
                    "error": "no_previous_srs",
                    "message": (
                        "No hay un SRS previo que reutilizar. Corre el pipeline "
                        "completo: analyze_quality -> infer_goals -> "
                        "check_coverage -> draft_narrative -> commit_srs."
                    ),
                }
            live_rows = await session.execute(
                sa_select(RequirementItem.code).where(
                    RequirementItem.project_id == project_id,
                    RequirementItem.status.in_(_LIVE_STATUSES),
                )
            )
            live_codes = {row[0] for row in live_rows.all()}

        # Diff contra los codigos del documento, no conteos ni timestamps:
        # la curacion legitima (merges, ediciones, reqs nuevos, links)
        # cambia ambos y un guard duro bloqueaba el camino quirurgico justo
        # despues de una curacion exitosa (sesion 53: 220 link_goal a mano,
        # luego stale_store -> re-run que ademas pisaba los links curados).
        doc_codes = set(srs.requirement_codes or [])
        overlap = (
            len(doc_codes & live_codes) / len(doc_codes) if doc_codes else 1.0
        )
        if overlap < 0.5:
            return {
                "error": "stale_store",
                "reasons": [
                    f"solape vivos/documento {len(doc_codes & live_codes)}/"
                    f"{len(doc_codes)} (<50%): el store fue reemplazado y los "
                    "hallazgos previos ya no representan el proyecto",
                ],
                "message": (
                    "El store fue reemplazado por una captura nueva. Corre "
                    "el pipeline completo desde analyze_quality."
                ),
            }

        # Hallazgos PERSISTIDOS (conservan la curacion de estado de la UI).
        # Los de reqs ya no vivos (fusionados, rechazados) se descartan: sin
        # anclaje en la version nueva.
        async with AsyncSessionLocal() as session:
            findings = await srs_store.list_findings(session, project_id)
            code_map = await srs_store._req_code_map(session, project_id)
            finding_dicts = srs_store.findings_to_dicts(findings, code_map)
        kept: list[dict] = []
        dropped_codes: list[str] = []
        for f in finding_dicts:
            req_code = f.get("req_code")
            if req_code is not None and req_code not in live_codes:
                dropped_codes.append(req_code)
                continue
            kept.append(f)
        finding_dicts = kept
        new_codes = sorted(live_codes - doc_codes)

        # El payload persistio goals_summary anidado bajo quality_summary
        # (commit_srs lo arma asi); separarlo para el holder.
        quality_summary = dict(srs.quality_summary or {})
        goals_summary = quality_summary.pop("goals", None)
        run.quality_summary = quality_summary or None
        run.goals_summary = goals_summary
        run.coverage = srs.coverage
        run.findings = finding_dicts
        # La prosa authored de la versión previa viaja en el run: sin este
        # canal el redactor re-draftaba de cero y marcaba el documento con
        # notas provisionales aunque el usuario pidiera el texto verbatim
        # (sesión 53 v11). draft_narrative la reutiliza o la revisa.
        run.narrative = dict(srs.narrative or {})
        run.stages_done.update({STAGE_QUALITY, STAGE_GOALS, STAGE_COVERAGE})

        return {
            "stage": STAGE_SEED,
            "reused_from_version": srs.version,
            "narrative_carried": bool(run.narrative),
            "findings_reused": len(finding_dicts),
            "findings_dropped": len(dropped_codes),
            "dropped_finding_codes": dropped_codes[:20],
            "new_requirements": len(new_codes),
            "stages_done": sorted(run.stages_done),
            "message": (
                f"Run sembrado desde la v{srs.version}: calidad, goals, "
                "cobertura y narrativa authored reutilizados. Continua con "
                "draft_narrative (sin instrucciones conserva el texto "
                "verbatim; con instrucciones lo revisa sobre esa base) y "
                "commit_srs: la version nueva re-proyecta trazabilidad, "
                "conteos y markdown desde el store vivo."
            ),
        }

    @tool
    async def analyze_quality() -> dict:
        """Etapa 1/4: análisis de calidad de los requerimientos vivos.

        Pre-checks programáticos (INCOSE/smells/EARS) + evaluación LLM de \
ambigüedad semántica. Acumula los hallazgos en el holder; persisten en \
commit_srs. Emite ``quality.found`` por cada hallazgo accionable \
(blocker/major).
        """
        run = get_run(project_id)
        if run is None:
            # Arranque tolerante: si no hay run, se crea (como ingest en captura).
            run = get_or_create_run(
                project_id,
                project_name=project_name,
                project_description=project_description,
            )
            run.reset_pipeline_outputs()
        try:
            run.bump(STAGE_QUALITY)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, on_event = _make_emitters()
        await on_progress(STAGE_QUALITY, "análisis de calidad (INCOSE + smells + EARS + LLM)")

        async def _report_progress(done: int, total: int) -> None:
            await on_progress(
                STAGE_QUALITY, f"lotes de calidad procesados: {done}/{total}"
            )

        async with AsyncSessionLocal() as session:
            summary, findings = await srs_quality.analyze_quality(
                session, project_id, on_progress=_report_progress
            )

        run.quality_summary = summary
        run.findings = list(findings)
        run.stages_done.add(STAGE_QUALITY)

        emitted = 0
        for f in findings:
            sev = f.get("severity").value if hasattr(f.get("severity"), "value") else str(f.get("severity"))
            if sev in _FINDING_EVENT_SEVERITIES:
                await on_event("quality.found", {
                    "rule_id": f.get("rule_id"),
                    "severity": sev,
                    "dimension": f.get("dimension").value if hasattr(f.get("dimension"), "value") else str(f.get("dimension")),
                    "message": f.get("message"),
                })
                emitted += 1

        return {
            "stage": STAGE_QUALITY,
            "items_analyzed": summary.get("items_analyzed", 0),
            "items_judged": summary.get("items_judged", 0),
            "items_reused": summary.get("items_reused", 0),
            "total_findings": summary.get("total_findings", 0),
            "blockers": summary.get("blockers", 0),
            # Inventario COMPLETO de blockers (req, rule, mensaje): es la lista
            # que debes reportar al usuario DE UNA VEZ — nunca por partes. Si
            # blockers_detail_truncated es true, el resto está en
            # list_run_findings(severity="blocker").
            "blockers_detail": summary.get("blockers_detail", []),
            "blockers_detail_truncated": summary.get(
                "blockers_detail_truncated", False
            ),
            "llm_eval_unavailable": summary.get("llm_eval_unavailable", 0),
            "by_severity": summary.get("by_severity", {}),
            "events_emitted": emitted,
            "stages_done": sorted(run.stages_done),
        }

    @tool
    async def infer_goals() -> dict:
        """Etapa 2/4: inferencia del modelo de goals (GORE: KAOS + i* + NFR).

        Dos fases para acotar la salida (con stores grandes el JSON monolítico \
llegaba truncado y la etapa devolvía 0 goals tras horas de reintentos): \
primero infiere goals funcionales, softgoals NFR y obstacles (5-15), \
después los vincula a los requerimientos (GoalLink) por lotes. PERSISTE \
goals + links. Emite ``goal.inferred`` por cada goal. Si la inferencia \
falla devuelve ``error`` (sin degradar a un modelo vacío).
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("analyze_quality")
        try:
            run.bump(STAGE_GOALS)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, on_event = _make_emitters()
        await on_progress(STAGE_GOALS, "inferencia de goals (GORE)")

        async def _report_progress(phase: str, done: int, total: int) -> None:
            await on_progress(STAGE_GOALS, f"{phase} procesados: {done}/{total}")

        try:
            async with AsyncSessionLocal() as session:
                summary = await goals_engine.infer_goals(
                    session, project_id, on_progress=_report_progress
                )
                goals = await srs_store.list_goals(session, project_id)
        except goals_engine.GoalsInferenceError as exc:
            # Ya no existe la degradación silenciosa a goals=0: el fallo se
            # reporta para que el agente informe en vez de reintentar a ciegas
            # (incidente v6 de Planitrack2.0: 3 reintentos de ~72 minutos).
            logger.error("infer_goals falló en project %s: %s", project_id, exc)
            return {"stage": STAGE_GOALS, "error": f"infer_goals: {exc}"}

        run.goals_summary = summary
        run.stages_done.add(STAGE_GOALS)

        for g in goals:
            await on_event("goal.inferred", {
                "code": g.code,
                "kind": g.kind.value,
                "statement": g.statement,
                "status": g.status.value,
            })

        return {
            "stage": STAGE_GOALS,
            "goals": summary.get("goals", 0),
            "links": summary.get("links", 0),
            "softgoals": summary.get("softgoals", 0),
            "obstacles": summary.get("obstacles", 0),
            "goals_stale": summary.get("goals_stale", 0),
            "goals_judged": summary.get("goals_judged", 0),
            "goals_reused": summary.get("goals_reused", 0),
            "link_scope": summary.get("link_scope", ""),
            "link_batches_failed": summary.get("link_batches_failed", 0),
            "link_batches_retried": summary.get("link_batches_retried", 0),
            "links_partial": summary.get("links_partial", False),
            "message": summary.get("message", ""),
            "stages_done": sorted(run.stages_done),
        }

    @tool
    async def infer_goal_links(req_codes: list[str]) -> dict:
        """Re-infiere links goal<->req SOLO para los requerimientos pedidos.

        Camino quirúrgico para completar o corregir la trazabilidad de unos
        pocos requerimientos: usa los goals YA PERSISTIDOS (códigos GOAL-XXXX
        reales, curación humana incluida) y vincula únicamente esos reqs. NO
        re-corra infer_goals entera para arreglar links puntuales: el upsert
        reemplazaría los links de TODOS los goals de la nueva inferencia.
        Idempotente: la arista que ya existe no se duplica. Requiere goals
        previos (infer_goals); sin ellos devuelve error.

        Args:
            req_codes: códigos REQ-XXXX de los requerimientos a vincular.
        """
        on_progress, _on_event = _make_emitters()

        async def _report(phase: str, done: int, total: int) -> None:
            await on_progress(STAGE_GOALS, f"{phase}: {done}/{total}")

        try:
            async with AsyncSessionLocal() as session:
                return await goals_engine.infer_goal_links_incremental(
                    session, project_id, req_codes, on_progress=_report
                )
        except goals_engine.GoalsInferenceError as exc:
            logger.error(
                "infer_goal_links falló en project %s: %s", project_id, exc
            )
            return {"error": f"infer_goal_links: {exc}"}

    @tool
    async def link_goal(
        goal_code: str,
        req_code: str,
        relation: str,
        rationale: str | None = None,
    ) -> dict:
        """Vincula a mano un goal (GOAL-XXXX) con un requerimiento (REQ-XXXX).

        Idempotente: si la arista ya existe, se actualiza el rationale y
        devuelve created=False. Para aristas puntuales; para lotes de reqs
        use infer_goal_links.

        Args:
            goal_code: código GOAL-XXXX del goal.
            req_code: código REQ-XXXX del requerimiento.
            relation: tipo de vínculo (realizes | contributes | conflicts).
            rationale: motivo breve del vínculo (opcional).
        """
        try:
            rel = LinkRelation(relation)
        except ValueError:
            return {
                "error": "invalid_relation",
                "message": (
                    f"relation invalida: {relation!r}. Valores: "
                    f"{[r.value for r in LinkRelation]}"
                ),
            }
        from backend.agents.tools.requirements_tools import _code_to_id

        async with AsyncSessionLocal() as session:
            goals = await srs_store.list_goals(session, project_id)
            goal = next((g for g in goals if g.code == goal_code), None)
            if goal is None:
                return {
                    "error": "goal_not_found",
                    "message": f"No existe {goal_code} en el proyecto.",
                }
            try:
                req_id = await _code_to_id(session, project_id, req_code)
            except KeyError:
                return {
                    "error": "requirement_not_found",
                    "message": f"No existe {req_code} en el proyecto.",
                }
            result = await srs_store.add_goal_link(
                session,
                project_id,
                goal_id=goal.id,
                req_id=req_id,
                relation=rel,
                rationale=rationale,
                # Curación dirigida por decisión (humana vía agente): el
                # upsert la preserva ante re-inferencias.
                detected_by="human",
            )
        return {"goal": goal.code, "requirement": req_code, **result}

    @tool
    async def link_goals(entries: list[dict]) -> dict:
        """Vincula en UN llamado un lote de pares goal <-> req ya decididos.

        Camino de lote para la curacion de trazabilidad cuando ya se sabe que
        aristas crear: cada entrada es {goal_code, req_code, relation,
        rationale?}. Idempotente por (goal, req, relation): la arista que ya
        existia se actualiza (rationale) y cuenta como updated. Los errores
        individuales (relation invalida, goal/req inexistente) se reportan
        por fila sin abortar el resto del lote. Para INFERIR links de un
        lote de reqs use infer_goal_links; para una arista suelta, link_goal.

        Args:
            entries: hasta 40 entradas {goal_code, req_code, relation, rationale?}.
        """
        max_entries = 40
        if len(entries) > max_entries:
            return {
                "error": "too_many_entries",
                "max_entries": max_entries,
                "message": (
                    f"Recibidas {len(entries)} entradas; divida el lote en "
                    f"llamados de hasta {max_entries}."
                ),
            }
        from backend.agents.tools.requirements_tools import _code_to_id

        async with AsyncSessionLocal() as session:
            goals = await srs_store.list_goals(session, project_id)
            goal_by_code = {g.code: g for g in goals}
            created = updated = 0
            errors: list[dict] = []
            for i, entry in enumerate(entries):
                goal_code = entry.get("goal_code")
                req_code = entry.get("req_code")
                relation = entry.get("relation")
                rationale = entry.get("rationale")
                try:
                    rel = LinkRelation(relation)
                except ValueError:
                    errors.append({
                        "index": i, "goal": goal_code, "req": req_code,
                        "error": "invalid_relation",
                    })
                    continue
                goal = goal_by_code.get(goal_code)
                if goal is None:
                    errors.append({
                        "index": i, "goal": goal_code, "req": req_code,
                        "error": "goal_not_found",
                    })
                    continue
                try:
                    req_id = await _code_to_id(session, project_id, req_code)
                except KeyError:
                    errors.append({
                        "index": i, "goal": goal_code, "req": req_code,
                        "error": "requirement_not_found",
                    })
                    continue
                result = await srs_store.add_goal_link(
                    session,
                    project_id,
                    goal_id=goal.id,
                    req_id=req_id,
                    relation=rel,
                    rationale=rationale,
                    # Curación dirigida por decisión (humana vía agente): el
                    # upsert la preserva ante re-inferencias.
                    detected_by="human",
                )
                if result["created"]:
                    created += 1
                else:
                    updated += 1
        return {
            "linked": created + updated,
            "created": created,
            "updated": updated,
            "errors": errors,
        }

    @tool
    async def unlink_goal(goal_code: str, req_code: str, relation: str) -> dict:
        """Desvincula un goal (GOAL-XXXX) de un requerimiento (REQ-XXXX).

        Args:
            goal_code: código GOAL-XXXX del goal.
            req_code: código REQ-XXXX del requerimiento.
            relation: tipo de vínculo a remover (realizes | contributes | conflicts).
        """
        try:
            rel = LinkRelation(relation)
        except ValueError:
            return {
                "error": "invalid_relation",
                "message": (
                    f"relation invalida: {relation!r}. Valores: "
                    f"{[r.value for r in LinkRelation]}"
                ),
            }
        from backend.agents.tools.requirements_tools import _code_to_id

        async with AsyncSessionLocal() as session:
            goals = await srs_store.list_goals(session, project_id)
            goal = next((g for g in goals if g.code == goal_code), None)
            if goal is None:
                return {
                    "error": "goal_not_found",
                    "message": f"No existe {goal_code} en el proyecto.",
                }
            try:
                req_id = await _code_to_id(session, project_id, req_code)
            except KeyError:
                return {
                    "error": "requirement_not_found",
                    "message": f"No existe {req_code} en el proyecto.",
                }
            removed = await srs_store.remove_goal_link(
                session,
                project_id,
                goal_id=goal.id,
                req_id=req_id,
                relation=rel,
            )
        return {"goal": goal.code, "requirement": req_code, "removed": removed}

    @tool
    async def edit_goal(
        goal_code: str,
        statement: str | None = None,
        rationale: str | None = None,
        status: str | None = None,
    ) -> dict:
        """Edita UN goal: enunciado, rationale o estado (curación quirúrgica).

        Camino de consolidación del catálogo SIN re-inferencia: resucitar un
        STALE (status="proposed"), confirmar uno curado (status="confirmed"),
        retirar uno mal inferido (status="rejected") o reescribir su prosa.
        El upsert de infer_goals preserva CONFIRMED/REJECTED como decisión
        humana (no los re-emite ni los reviva), así que esta curación
        sobrevive a las re-inferencias.

        Args:
            goal_code: código GOAL-XXXX del goal a editar.
            statement: nuevo enunciado (opcional).
            rationale: nuevo rationale (opcional).
            status: proposed | confirmed | rejected | stale (opcional).
        """
        st: GoalStatus | None = None
        if status:
            try:
                # Tolerante a mayúsculas (el LLM a veces envía "PROPOSED").
                st = GoalStatus(str(status).strip().lower())
            except ValueError:
                return {
                    "error": "invalid_status",
                    "message": (
                        f"status invalido: {status!r}. Valores: "
                        f"{[s.value for s in GoalStatus]}"
                    ),
                }
        from backend.agents.tools.requirements_tools import _code_to_goal_id

        async with AsyncSessionLocal() as session:
            try:
                goal_id = await _code_to_goal_id(
                    session, project_id, goal_code
                )
            except KeyError:
                return {
                    "error": "goal_not_found",
                    "message": f"No existe {goal_code} en el proyecto.",
                }
            g = await srs_store.update_goal(
                session,
                goal_id,
                project_id=project_id,
                statement=statement,
                rationale=rationale,
                status=st,
            )
        return {
            "goal": g.code,
            "status": g.status.value,
            "statement": (g.statement or "")[:200],
            "message": (
                f"Goal {g.code} actualizado (status {g.status.value}). "
                "CONFIRMED/REJECTED son decisión humana: las re-inferencias "
                "los preservan."
            ),
        }

    @tool
    async def merge_goals(
        keeper_code: str,
        absorbed_codes: list[str],
        statement: str | None = None,
        rationale: str | None = None,
    ) -> dict:
        """Fusiona goals absorbidos en un keeper (consolidación del catálogo).

        Consolida N goals que dicen lo mismo en UNO (el keeper) sin
        re-inferencia: los links de los absorbidos se RE-APUNTAN al keeper
        in-place (la curación humana de cada arista viaja con él), los
        absorbidos quedan REJECTED (soft: fila y auditoría sobreviven, y
        CONFIRMED/REJECTED no se re-emiten en futuras inferencias),
        sub-goals colgantes se re-parentan y opcionalmente reescribe el
        enunciado del keeper con la síntesis consolidada. Es la vía para
        bajar ~102 goals STALE + solapados a un catálogo canónico (regla de
        rango objetivo 10-30 del motor): plan consensuado con el usuario y
        merges por tandas, uno por llamada.

        Args:
            keeper_code: código GOAL-XXXX que absorbe (queda vivo).
            absorbed_codes: códigos GOAL-XXXX absorbidos (quedan REJECTED).
            statement: nuevo enunciado del keeper (opcional, síntesis).
            rationale: rationale del keeper (opcional).
        """
        async with AsyncSessionLocal() as session:
            try:
                result = await srs_store.merge_goals(
                    session,
                    project_id,
                    keeper_code=keeper_code,
                    absorbed_codes=absorbed_codes,
                    statement=statement,
                    rationale=rationale,
                )
            except KeyError as exc:
                return {
                    "error": "goal_not_found",
                    "message": str(exc),
                }
            except ValueError as exc:
                return {
                    "error": "merge_rejected",
                    "message": str(exc),
                }
        return {
            **result,
            "message": (
                f"{len(result['absorbed'])} goal(s) absorbidos en "
                f"{result['keeper']}: {result['merged_links']} aristas "
                f"re-apuntadas, {result['dropped_links']} duplicadas "
                f"eliminadas. El próximo commit re-proyecta el anexo de "
                "goals."
            ),
        }

    # Edición de enunciados para el srs-agent (cura de blockers, P5): la MISMA
    # tool bulk de la captura, reutilizada de su fábrica — sin duplicación y
    # sin las tools destructivas (delete/merge) que siguen siendo de captura.
    from backend.agents.tools.requirements_tools import make_requirements_tools

    update_requirements_tool = next(
        t
        for t in make_requirements_tools(project_id)
        if t.name == "update_requirements"
    )
    update_requirements = update_requirements_tool

    @tool
    async def list_run_findings(
        severity: str | None = None,
        limit: int = 60,
    ) -> dict:
        """Lee los hallazgos del run EN CURSO (holder), no de la DB.

        Devuelve líneas compactas {req, rule, severity, message} de los
        hallazgos acumulados por analyze_quality / check_coverage en este run,
        ordenados por severidad (blockers primero). A diferencia de
        get_requirement_findings — que lee la DB persistida y solo está
        fresca tras un commit — esta tool sirve DURANTE el run: es la vía
        para ver el inventario completo (p. ej. todos los blockers cuando
        blockers_detail llegó truncado por el cap de 40) sin esperar commit.

        Args:
            severity: filtro opcional (blocker | major | minor | info).
            limit: tope de filas (default 60, cap 200).
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("analyze_quality")
        limit = max(1, min(int(limit or 60), 200))
        async with AsyncSessionLocal() as session:
            code_map = await srs_store._req_code_map(session, project_id)
        rows = []
        for f in run.findings:
            sev = f.get("severity")
            sev = sev.value if hasattr(sev, "value") else str(sev)
            if severity and sev != severity:
                continue
            rows.append(
                {
                    "req": (
                        code_map.get(f.get("req_id"))
                        if f.get("req_id")
                        else None
                    ),
                    "rule": f.get("rule_id"),
                    "severity": sev,
                    "message": (f.get("message") or "")[:160],
                }
            )
        order = {"blocker": 0, "major": 1, "minor": 2, "info": 3}
        rows.sort(key=lambda r: (order.get(r["severity"], 9), r["req"] or ""))
        return {
            "total": len(run.findings),
            "returned": len(rows[:limit]),
            "findings": rows[:limit],
        }

    @tool
    async def resolve_findings(
        targets: list[dict],
        status: str,
        note: str | None = None,
    ) -> dict:
        """Cierra formalmente hallazgos persistidos: "fixed" o "waived".

        Cierre CON MEMORIA: el merge del commit preserva el estado mientras el
        enunciado no cambie — lo cerrado NO reaparece en el siguiente
        re-análisis. Úsalo tras curar un enunciado (status="fixed") o cuando
        el usuario decide convivir con un hallazgo (status="waived", con note
        que documente el criterio — es la alternativa honesta a re-editar).
        NO lo uses sobre reqs cuyo enunciado se edita en la misma curación:
        esos hallazgos se re-juzgan solos en el re-análisis delta.

        Args:
            targets: hasta 60 entradas {req_code, rule_id}.
            status: "fixed" | "waived".
            note: razón del cierre (auditoría; en waived debería siempre ir).
        """
        if len(targets) > 60:
            return {
                "error": "too_many_targets",
                "message": "máximo 60 cierres por llamada; dividir en tandas",
            }
        try:
            st = FindingStatus(status)
        except ValueError:
            return {
                "error": "invalid_status",
                "message": (
                    f"status invalido: {status!r}. Valores: fixed | waived"
                ),
            }
        if st not in (FindingStatus.FIXED, FindingStatus.WAIVED):
            return {
                "error": "invalid_status",
                "message": "status debe ser fixed o waived",
            }
        from sqlalchemy import select as sa_select

        from backend.agents.tools.requirements_tools import _code_to_id
        from backend.models.requirement import RequirementItem

        async with AsyncSessionLocal() as session:
            id_to_code = {
                rid: code
                for rid, code in (
                    await session.execute(
                        sa_select(
                            RequirementItem.id, RequirementItem.code
                        ).where(
                            RequirementItem.project_id == project_id
                        )
                    )
                ).all()
            }
            resolved: list[tuple[int, str]] = []
            not_found: list[dict] = []
            for t in targets:
                try:
                    req_id = await _code_to_id(
                        session, project_id, t["req_code"]
                    )
                except KeyError:
                    not_found.append(t)
                    continue
                resolved.append((req_id, t["rule_id"]))
            result = await srs_store.resolve_findings(
                session,
                project_id,
                targets=resolved,
                status=st,
                note=note,
                resolved_by="agent",
            )
        missing = [
            {"req_code": id_to_code.get(rid), "rule_id": rule}
            for rid, rule in result.get("missing", [])
        ]
        return {
            "resolved": result.get("resolved", 0),
            "missing": missing,
            "not_found": not_found,
            "status": st.value,
            "message": (
                f"{result.get('resolved', 0)} hallazgo(s) cerrados como "
                f"{st.value}. El merge del próximo commit los preserva "
                "mientras el enunciado no cambie."
            ),
        }

    @tool
    async def apply_cures(
        edits: list[dict],
        waives: list[dict] | None = None,
        reanalyze: bool = False,
    ) -> dict:
        """Aplica UNA tanda de cura aprobada. NO re-analiza: cierra la ola aparte.

        Un solo llamado ejecuta: (1) las EDICIONES de enunciados aprobadas por
        el usuario (mismas entradas que update_requirements: {code, statement,
        reason?}, cap 50) y (2) los CIERRES formales ({req_code, rule_id,
        status?: fixed|waived, note?}) para hallazgos que no llevan edición.
        Las ediciones quedan registradas como OLA DE CURACIÓN abierta (dirty):
        ciérrala con close_curation_wave (UN re-análisis delta por ola) y el
        reporte te devuelve solo el DELTA — hallazgos resueltos y nuevos.
        Antes cada cura disparaba su propio re-análisis, el juez re-juzgaba el
        texto re-escrito y 'descubría' hallazgos cada vez más finos: curar
        parecía no terminar nunca. Funciona con o sin run activo: con run
        refresca el holder (el próximo commit persiste los hallazgos frescos);
        sin run igualmente aplica ediciones/cierres y el próximo /srs arranca
        desde ese estado.

        Args:
            edits: hasta 50 entradas {code, statement, reason?}.
            waives: hasta 60 entradas {req_code, rule_id, status?, note?}.
            reanalyze: correr el re-análisis delta al final. Default false:
                en curas por OLAS el análisis es único (close_curation_wave);
                usa true solo en curas de un paso sin olas.
        """
        # (1) Ediciones via la MISMA tool bulk de captura (auditoría de
        # revisiones + autocierre determinista incluidos) + registro de ola.
        edits_result: dict = {"updated": [], "errors": [], "findings_closed": 0}
        if edits:
            edits_result = await update_requirements_tool.ainvoke(
                {"updates": edits}
            )
            run = get_run(project_id)
            if run is not None:
                run.mark_dirty(
                    u.get("req_id") for u in edits_result.get("updated") or []
                )

        # (2) Cierres formales (agrupando por status).
        closures: dict[str, dict] = {}
        if waives:
            by_status: dict[str, list[dict]] = {}
            for w in waives:
                by_status.setdefault(w.get("status") or "waived", []).append(w)
            for status_val, group in by_status.items():
                inner = await resolve_findings.ainvoke(
                    {
                        "targets": [
                            {
                                "req_code": w["req_code"],
                                "rule_id": w["rule_id"],
                            }
                            for w in group
                        ],
                        "status": status_val,
                        "note": group[0].get("note"),
                    }
                )
                closures[status_val] = inner

        # (3) Re-análisis explícito (opt-in): único punto donde apply_cures
        # re-juzga; consume la ola y devuelve el delta contra su snapshot.
        after: dict = {}
        if reanalyze:
            try:
                async with AsyncSessionLocal() as session:
                    summary, findings = await srs_quality.analyze_quality(
                        session, project_id
                    )
                run = get_run(project_id)
                if run is not None:
                    wave = run.wave_report(findings)
                    run.dirty_req_ids.clear()
                    run.quality_summary = summary
                    run.findings = list(findings)
                    run.stages_done.add(STAGE_QUALITY)
                else:
                    wave = {}
                after = {
                    "blockers": summary.get("blockers", 0),
                    "blockers_detail": summary.get("blockers_detail", []),
                    "items_judged": summary.get("items_judged", 0),
                    "items_reused": summary.get("items_reused", 0),
                    "wave_delta": wave,
                }
            except Exception as exc:  # noqa: BLE001 — la cura ya aplicó
                logger.exception("apply_cures: re-análisis delta falló")
                after = {"error": f"re-análisis delta falló: {exc}"}

        run = get_run(project_id)
        wave_open = run is not None and bool(run.dirty_req_ids)
        return {
            "edits_applied": len(edits_result.get("updated") or []),
            "edits_errors": edits_result.get("errors") or [],
            "findings_autoclosed": edits_result.get("findings_closed", 0),
            "closures": closures,
            "after": after,
            "wave_open": wave_open,
            "message": (
                "Ediciones aplicadas y ola de curación ABIERTA: llama "
                "close_curation_wave cuando termines la tanda (UN solo "
                "re-análisis para toda la ola) antes de commit_srs."
                if wave_open
                else "Cura aplicada sin ediciones pendientes de re-análisis."
            ),
        }

    @tool
    async def close_curation_wave() -> dict:
        """Cierra la ola de curación: UN re-análisis delta para TODAS las tandas.

        Tras apply_cures(edits=...) la ola queda abierta: los enunciados
        editados esperan re-juicio. Esta tool corre UN analyze_quality delta
        (solo re-juzga lo editado; lo intacto conserva su veredicto) y devuelve
        el reporte DELTA de la ola contra el inventario previo a curar:
        {resolved_count, resolved[]} (hallazgos que la cura eliminó) y
        {new_count, new[]} (los que el texto re-escrito todavía tiene). REPORTE
        ESE DELTA al usuario — es la prueba de progreso de la ola; el
        inventario completo solo en el informe del commit. Con múltiples tandas
        (cap 50 por llamada) aplica TODAS primero y cierra la ola UNA vez.

        Sin ola abierta (nada editado desde el último análisis) es un no-op.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("analyze_quality")
        if not run.dirty_req_ids:
            return {
                "stage": STAGE_QUALITY,
                "wave_open": False,
                "message": (
                    "No hay ola abierta: ningún enunciado fue editado desde "
                    "el último análisis de calidad. Nada que re-analizar."
                ),
            }
        try:
            run.bump(STAGE_QUALITY)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, _on_event = _make_emitters()
        dirty_count = len(run.dirty_req_ids)
        await on_progress(
            STAGE_QUALITY,
            f"cierre de ola de curación ({dirty_count} reqs editados)",
        )

        async def _report_progress(done: int, total: int) -> None:
            await on_progress(
                STAGE_QUALITY, f"lotes de calidad procesados: {done}/{total}"
            )

        async with AsyncSessionLocal() as session:
            summary, findings = await srs_quality.analyze_quality(
                session, project_id, on_progress=_report_progress
            )
        wave = run.wave_report(findings)
        run.dirty_req_ids.clear()
        run.quality_summary = summary
        run.findings = list(findings)
        run.stages_done.add(STAGE_QUALITY)
        return {
            "stage": STAGE_QUALITY,
            "wave_open": False,
            "items_judged": summary.get("items_judged", 0),
            "items_reused": summary.get("items_reused", 0),
            "total_findings": summary.get("total_findings", 0),
            "blockers": summary.get("blockers", 0),
            "wave_delta": wave,
            "message": (
                f"Ola cerrada: {wave.get('resolved_count', 0)} hallazgo(s) "
                f"resueltos por la cura, {wave.get('new_count', 0)} nuevo(s) "
                "sobre el texto re-escrito. Reporta ese delta; el inventario "
                "completo va solo en el informe del commit."
            ),
        }

    @tool
    async def apply_cure_batch(
        rule_id: str,
        dry_run: bool = True,
        offset: int = 0,
        limit: int = 50,
    ) -> dict:
        """Aplica UNA MISMA cura a todos los hallazgos OPEN de una regla.

        Curas masivas por patrón: cuando un smell determinista concentra
        decenas/miles de hallazgos (p. ej. smell.pronoun en cientos de reqs),
        decidir uno por uno no escala. Resuelve los hallazgos OPEN de esa
        regla que llevan SUGERENCIA de texto completo (aplicable = la
        suggestion es una reescritura del enunciado: reemplaza al enunciado
        tal cual); los de solo consejo (suggestion vacía o nota) quedan fuera
        con motivo. dry_run=True (default) devuelve el preview sin editar
        nada: preséntalo al usuario antes de aplicar.

        Args:
            rule_id: regla a curar en lote (p. ej. "smell.pronoun").
            dry_run: true = preview ({req, actual, propuesta}); false = aplica
                por el mismo camino de update_requirements (auditoría de
                revisiones + ola de curación incluidos; al terminar la ola
                queda abierta para close_curation_wave).
            offset: posición de inicio (para paginar el preview).
            limit: tope por llamada (1-50).
        """
        from backend.services.requirement_store import list_requirements

        limit = max(1, min(int(limit or 50), 50))
        async with AsyncSessionLocal() as session:
            findings = await srs_store.list_findings(session, project_id)
            code_map = await srs_store._req_code_map(session, project_id)
            target = [
                f
                for f in findings
                if f.rule_id == rule_id
                and f.status == FindingStatus.OPEN
                and f.req_id is not None
            ]
            ids = {f.req_id for f in target}
            items = await list_requirements(session, project_id)
            stmt_by_id = {
                it.id: it.statement for it in items if it.id in ids
            }
        target.sort(key=lambda f: (f.req_id, f.id))
        page = target[offset : offset + limit]

        applicable: list[dict] = []
        skipped: list[dict] = []
        for f in page:
            code = code_map.get(f.req_id)
            suggestion = (f.suggestion or "").strip()
            current = (stmt_by_id.get(f.req_id) or "").strip()
            # Aplicable SOLO si la suggestion es una reescritura completa del
            # enunciado (regla SEM): reemplazarla por el texto actual no cura
            # nada y las sugerencias de consejo no son texto de enunciado.
            if not suggestion or suggestion == current:
                skipped.append({
                    "req": code,
                    "reason": "suggestion no es reescritura del enunciado",
                })
                continue
            applicable.append({
                "code": code,
                "statement": suggestion,
                "reason": f"cura masiva {rule_id}",
            })
        if not applicable:
            return {
                "rule_id": rule_id,
                "total_open": len(target),
                "applicable": 0,
                "skipped": skipped[:20],
                "preview": [],
                "message": (
                    "Ninguno de los hallazgos de esta regla lleva sugerencia "
                    "de reescritura completa: la cura masiva no aplica; "
                    "decidir caso por caso o waive."
                ),
            }
        if dry_run:
            return {
                "rule_id": rule_id,
                "total_open": len(target),
                "applicable": len(applicable),
                "skipped": skipped[:20],
                "offset": offset,
                "more_after": offset + limit < len(target),
                "preview": [
                    {
                        "req": a["code"],
                        "actual": (stmt_by_id.get(
                            next(
                                f.req_id
                                for f in page
                                if code_map.get(f.req_id) == a["code"]
                            ),
                            "",
                        ) or "")[:200],
                        "propuesta": a["statement"][:200],
                    }
                    for a in applicable[:20]
                ],
                "message": (
                    f"{len(applicable)} de {len(target)} hallazgos OPEN de "
                    f"{rule_id} son aplicables. Presenta el preview al "
                    "usuario y re-invoca con dry_run=false para aplicar."
                ),
            }
        # Aplicar: mismo camino que apply_cures (auditoría + ola) sin
        # re-análisis: la ola se cierra aparte con close_curation_wave.
        chunk_size = 50
        applied: list[dict] = []
        errors: list[str] = []
        for i in range(0, len(applicable), chunk_size):
            res = await update_requirements_tool.ainvoke(
                {"updates": applicable[i : i + chunk_size]}
            )
            if res.get("error"):
                errors.append(res["error"])
            applied.extend(res.get("updated") or [])
        # La ola DEBE quedar registrada: sin holder el delta de la cura se
        # pierde. Se crea on demand (mismo arranque tolerante que analyze_quality).
        run = get_or_create_run(
            project_id,
            project_name=project_name,
            project_description=project_description,
        )
        run.mark_dirty(u.get("req_id") for u in applied)
        return {
            "rule_id": rule_id,
            "total_open": len(target),
            "edits_applied": len(applied),
            "edits_errors": errors,
            "skipped": skipped[:20],
            "wave_open": bool(run.dirty_req_ids),
            "message": (
                f"{len(applied)} enunciados reescritos por la cura masiva de "
                f"{rule_id}. Ola abierta: ciérrala con close_curation_wave "
                "y reporta el delta."
            ),
        }

    @tool
    async def check_coverage() -> dict:
        """Etapa 3/4: auditoría de cobertura (ISO 25010 + 29148 + goals).

        Mapea los requerimientos a las 8 características de ISO 25010, marca \
los gaps, verifica presencia de secciones 29148 y la cobertura de goals. \
Acumula hallazgos de cobertura en el holder. Emite ``coverage.report``.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("analyze_quality")
        try:
            run.bump(STAGE_COVERAGE)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, on_event = _make_emitters()
        await on_progress(STAGE_COVERAGE, "auditoría de cobertura (ISO 25010 + 29148 + goals)")
        async with AsyncSessionLocal() as session:
            coverage, cov_findings = await srs_coverage.compute_coverage(session, project_id)

        run.coverage = coverage
        run.findings.extend(cov_findings)
        run.stages_done.add(STAGE_COVERAGE)

        gaps = coverage.get("gaps_25010", [])
        totals = coverage.get("totals", {})
        goals_cov = coverage.get("goals", {})
        unlinked = goals_cov.get("unlinked_requirements", 0)
        await on_event("coverage.report", {
            "total": totals.get("live", 0),
            "functional": totals.get("functional", 0),
            "nfr": totals.get("nfr", 0),
            "gaps_25010": gaps,
            "unrealized_goals": goals_cov.get("unrealized", []),
            "unmitigated_obstacles": goals_cov.get("unmitigated_obstacles", []),
            "unlinked_requirements": unlinked,
        })

        return {
            "stage": STAGE_COVERAGE,
            "live": totals.get("live", 0),
            "gaps_25010": gaps,
            "unrealized_goals": goals_cov.get("unrealized", []),
            "unmitigated_obstacles": goals_cov.get(
                "unmitigated_obstacles", []
            ),
            # Cola de trazabilidad (dirección req->goal): si unlinked > 0,
            # ciérrala con infer_goal_links(req_codes=unlinked_codes) ANTES
            # del commit (regla TRAZABILIDAD COMPLETA del prompt).
            "unlinked_requirements": unlinked,
            "unlinked_codes": goals_cov.get("unlinked_codes", [])[:60],
            "goals_stale": goals_cov.get("stale", 0),
            "stages_done": sorted(run.stages_done),
        }

    @tool
    async def draft_narrative(instructions: str | None = None) -> dict:
        """Etapa 4/5: redacta con LLM las 7 subsecciones authored del SRS.

        Genera prosa para propósito, alcance, definiciones, perspectiva, \
perspectiva, usuarios, entorno operativo y supuestos, usando contexto RAG \
de los documentos fuente de captura y el catálogo de actores definidos en \
la captura (la prosa de usuarios se ancla a esos roles). La seccion 1.4 \
Referencias NO se redacta: es proyeccion determinista del catalogo de \
fuentes de captura (tabla siempre viva). Preserva las \
secciones deterministas \
(overview, references, features) y reapende el bloque de conteos a la perspectiva. \
Si el run viene sembrado (seed_from_last_srs), SIN instrucciones conserva \
la prosa authored previa VERBATIM (solo re-proyecta conteos y deterministas; \
no llama al redactor) y CON instrucciones la revisa sobre esa base. \
Emite ``narrative.drafted`` al terminar.

        Args:
            instructions: indicaciones narrativas del usuario (p. ej. \
"incorporar el carácter multi-industria en propósito y alcance, con los \
ejemplos de la visión"). Es el UNICO canal por el que las indicaciones \
conversacionales llegan al redactor: el contexto se arma desde el store + \
RAG, tu conversacion NO se le pasa. Pasa aca, verbatim, lo que el usuario \
pidio ajustar.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("analyze_quality")
        if STAGE_COVERAGE not in run.stages_done:
            return {
                "error": "missing_stages",
                "missing": [STAGE_COVERAGE],
                "message": (
                    "draft_narrative requiere que check_coverage haya corrido "
                    "antes (para usar las métricas de cobertura en el contexto)."
                ),
            }
        try:
            run.bump(STAGE_NARRATIVE)
        except StageLoopExceeded as exc:
            return _loop_err(exc)

        on_progress, on_event = _make_emitters()
        await on_progress(
            STAGE_NARRATIVE,
            "redacción narrativa asistida por LLM (7 secciones authored)",
        )
        try:
            async with AsyncSessionLocal() as session:
                items = await list_requirements(
                    session, project_id, include_deleted=True
                )
                from backend.services import actor_store

                actors_block_text = await actor_store.actors_block(
                    session, project_id
                )
                # §1.4 Referencias: proyección del catálogo de fuentes vivo
                # (cuenta los docs usados en captura y sus reqs respaldados).
                sources_summary = await _capture_sources(session, project_id)
                live = [it for it in items if it.status in _LIVE_STATUSES]
                # El bloque de conteos y las métricas del redactor salen del
                # run: tras un seed, re-proyectarlos al store vivo (sesión
                # 53). DENTRO del async with: _functional_goal_groups lee
                # Goal/GoalLink con esta sesión; usarla después del cierre
                # reabría una conexión implícita que el pool nunca recibe
                # (leak «non-checked-in connection», sesión 18).
                _refresh_run_metrics(run, live)
                goal_groups = await _functional_goal_groups(
                    session, project_id, live
                )

            # Narrativa determinista fresca del store vivo (bloque de conteos,
            # overview, features 2.2 agrupadas por goals actuales).
            det_narrative = _draft_narrative(
                run.project_name,
                run.project_description,
                run.quality_summary or {},
                run.coverage or {},
                run.goals_summary or {},
                len(live),
                live_items=live,
                goal_groups=goal_groups,
                actors_block_text=actors_block_text,
                sources_summary=sources_summary,
            )
            if run.narrative and not instructions:
                # Reuso verbatim: prosa authored de la versión sembrada +
                # deterministas frescos. Sin LLM (sesión 53 v11: el redactor
                # no tenía la base y marcaba el texto con notas provisionales).
                narrative = _carryover_narrative(run.narrative, det_narrative)
                mode = "reused_previous"
            else:
                # Con instrucciones sobre un run sembrado el redactor recibe
                # el texto previo como base a revisar; en pipeline completo
                # redacta desde cero. El fallback del LLM es la base pasada.
                base = (
                    _carryover_narrative(run.narrative, det_narrative)
                    if run.narrative
                    else det_narrative
                )
                narrative = await draft_narrative_llm(
                    base,
                    project_id=project_id,
                    project_name=run.project_name,
                    project_description=run.project_description,
                    live_items=live,
                    quality_summary=run.quality_summary or {},
                    coverage=run.coverage or {},
                    goals_summary=run.goals_summary or {},
                    instructions=instructions,
                    previous_narrative=run.narrative,
                )
                mode = (
                    "revised_previous" if run.narrative else "drafted_fresh"
                )
        except Exception as exc:  # noqa: BLE001 — surface al modelo
            logger.exception("draft_narrative failed")
            return {"error": f"draft_narrative failed: {exc}"}

        run.narrative = narrative
        run.stages_done.add(STAGE_NARRATIVE)

        # Contar cuántas subsecciones authored dejaron de tener placeholder.
        placeholders = sum(
            1 for v in narrative.values()
            if isinstance(v, str) and "Editor:" in v
        )
        await on_event("narrative.drafted", {
            "subsections": 7,
            "remaining_placeholders": placeholders,
        })

        # Materialización temprana (P2): persiste una versión DRAFT apenas la
        # prosa está lista — el usuario ve el documento en el visor sin
        # esperar el commit; commit_srs la promueve in-place a CANDIDATE.
        # Best-effort: si falla, la etapa sigue y el commit genera la versión.
        draft_version = None
        try:
            async with AsyncSessionLocal() as session:
                built = await build_srs(
                    session,
                    project_id,
                    project_name=run.project_name,
                    project_description=run.project_description,
                    narrative=narrative,
                    structure=SRS_STRUCTURE,
                )
                draft = await srs_store.create_srs_draft(
                    session,
                    project_id,
                    {
                        "structure": SRS_STRUCTURE,
                        "narrative": narrative,
                        "markdown": built["markdown"],
                        "quality_summary": run.quality_summary or {},
                        "coverage": run.coverage or {},
                        "requirement_codes": [it.code for it in live],
                        "requirement_count": len(live),
                        "generated_by": "agent",
                    },
                )
            run.draft_version = draft.version
            draft_version = draft.version
            await on_event("srs.draft", {"version": draft.version})
        except Exception:  # noqa: BLE001 — el DRAFT es un extra, no un gate
            logger.exception("draft_narrative: persistencia DRAFT falló")

        return {
            "stage": STAGE_NARRATIVE,
            "mode": mode,
            "subsections": 7,
            "remaining_placeholders": placeholders,
            "instructions_received": bool(instructions),
            "draft_version": draft_version,
            "stages_done": sorted(run.stages_done),
        }

    @tool
    async def commit_srs() -> dict:
        """Etapa 5/5: persiste el SrsDocument CANDIDATE.

        Combina narrativa (borrador) + secciones proyectadas (markdown) + \
hallazgos + goals + cobertura + matriz de trazabilidad. Es la ÚNICA etapa \
que escribe la DB (findings + SrsDocument). Emite ``srs.ready`` al terminar \
y limpia el holder.
        """
        run = get_run(project_id)
        if run is None:
            return _no_run("analyze_quality")
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
                    "Faltan etapas obligatorias antes de commit. Ejecuta: "
                    + ", ".join(missing)
                ),
            }

        on_progress, on_event = _make_emitters()
        # Guard de ola sucia: un commit con enunciados editados sin re-juzgar
        # persistiría hallazgos rancios (y los fingerprints de lo editado
        # quedarían sin sellar: la próxima corrida re-pagaría el delta). Si el
        # agente olvidó close_curation_wave, el cierre corre AQUÍ: UN
        # re-análisis delta, sin versión adicional.
        wave_delta: dict = {}
        if run.dirty_req_ids:
            await on_progress(
                STAGE_COMMIT,
                f"ola de curación abierta ({len(run.dirty_req_ids)} reqs "
                "editados): re-análisis delta previo al commit",
            )
            async with AsyncSessionLocal() as session:
                summary, findings = await srs_quality.analyze_quality(
                    session, project_id
                )
            wave_delta = run.wave_report(findings)
            run.dirty_req_ids.clear()
            run.quality_summary = summary
            run.findings = list(findings)
            run.stages_done.add(STAGE_QUALITY)
        await on_progress(STAGE_COMMIT, "persistiendo SRS candidato")
        try:
            async with AsyncSessionLocal() as session:
                # Hallazgos (calidad + cobertura) -> MERGE con memoria: lo
                # curado (fixed/waived) sobrevive mientras el enunciado no
                # cambie, y el sello de fingerprints de lo re-juzgado viaja
                # en la misma transacción (abort pre-commit = sin sello).
                merge_stats = await srs_store.merge_findings(
                    session,
                    project_id,
                    run.findings,
                    fresh_judged_req_ids=(
                        run.quality_summary or {}
                    ).get("fresh_judged_req_ids"),
                )
                # Trazabilidad + items vivos.
                traceability = await srs_store.build_traceability(session, project_id)
                items = await list_requirements(session, project_id, include_deleted=True)
                live = [it for it in items if it.status in _LIVE_STATUSES]
                # Mismo refresh: si el store cambió entre draft y commit, el
                # payload (quality_summary/coverage persistidos) y los números
                # del srs.ready no quedan del snapshot del seed.
                _refresh_run_metrics(run, live)
                # Narrativa editable: usa la del stage narrative si existe,
                # si no, genera el borrador determinista como fallback.
                if run.narrative is not None:
                    narrative = run.narrative
                else:
                    narrative = _draft_narrative(
                        run.project_name,
                        run.project_description,
                        run.quality_summary or {},
                        run.coverage or {},
                        run.goals_summary or {},
                        len(live),
                        live_items=live,
                        goal_groups=await _functional_goal_groups(
                            session, project_id, live
                        ),
                    )
                # Proyeccion Markdown (con narrative + structure 29148).
                built = await build_srs(
                    session,
                    project_id,
                    project_name=run.project_name,
                    project_description=run.project_description,
                    narrative=narrative,
                    structure=SRS_STRUCTURE,
                )
                payload = {
                    "structure": SRS_STRUCTURE,
                    "narrative": narrative,
                    "markdown": built["markdown"],
                    "quality_summary": {
                        **(run.quality_summary or {}),
                        "goals": run.goals_summary or {},
                    },
                    "coverage": run.coverage or {},
                    "traceability": traceability,
                    "requirement_codes": [it.code for it in live],
                    "requirement_count": len(live),
                    "generated_by": "agent",
                }
                # Anti-cascada: si el contenido es idéntico al de la versión
                # vigente (según la firma PERSISTIDA en su commit), NO crea
                # versión (sesión 17: refrescos sucesivos apilaban v18-v19 sin
                # cambios de fondo).
                signature = srs_store.srs_commit_signature(
                    narrative,
                    statements=[(it.code, it.statement) for it in live],
                    requirement_count=len(live),
                    goals_summary=run.goals_summary or {},
                    coverage_totals=(run.coverage or {}).get("totals"),
                )
                payload["review_flags"] = {
                    **(payload.get("review_flags") or {}),
                    "commit_signature": signature,
                }
                previous = await srs_store.get_latest_srs(session, project_id)

                if run.draft_version is not None:
                    # La versión DRAFT que el usuario vio durante el run se
                    # promueve in-place: mismo número, estado CANDIDATE. El
                    # guard de firma no aplica (la fila DRAFT ya existía).
                    try:
                        srs = await srs_store.promote_srs_draft(
                            session, project_id, run.draft_version, payload
                        )
                    except (KeyError, ValueError):
                        # El DRAFT se descartó a mano durante el run: crea la
                        # versión formal nueva en vez de fallar el commit.
                        srs = await srs_store.create_srs(
                            session, project_id, payload
                        )
                elif (
                    previous is not None
                    and previous.status == SrsStatus.CANDIDATE
                    and (previous.review_flags or {}).get("commit_signature")
                    == signature
                ):
                    no_op_result = {
                        "stage": STAGE_COMMIT,
                        "no_new_version": True,
                        "latest_version": previous.version,
                        "message": (
                            "Sin cambios respecto de la versión "
                            f"{previous.version}: no se genera una versión "
                            "nueva. Si el usuario quiere otro ajuste de "
                            "redacción, usa patch_narrative_section."
                        ),
                    }
                    await on_event("srs.ready", {
                        "version": previous.version,
                        "status": previous.status.value,
                        "no_new_version": True,
                        "requirement_count": previous.requirement_count,
                    })
                    clear_run(project_id)
                    return no_op_result
                else:
                    srs = await srs_store.create_srs(session, project_id, payload)
        except Exception as exc:  # noqa: BLE001 — surface al modelo
            logger.exception("commit_srs failed")
            return {"error": f"commit_srs failed: {exc}"}

        await on_event("srs.ready", {
            "version": srs.version,
            "status": srs.status.value,
            "requirement_count": srs.requirement_count,
            "findings": (run.quality_summary or {}).get("total_findings", 0),
            "blockers": (run.quality_summary or {}).get("blockers", 0),
            "blockers_detail": (run.quality_summary or {}).get(
                "blockers_detail", []
            ),
            "goals": (run.goals_summary or {}).get("goals", 0),
            "findings_merge": {
                k: merge_stats.get(k, 0)
                for k in ("preserved", "reopened", "inserted", "deleted")
            },
            "wave_delta": wave_delta,
        })

        clear_run(project_id)
        return {
            "stage": STAGE_COMMIT,
            "version": srs.version,
            "status": srs.status.value,
            "requirement_count": srs.requirement_count,
            "findings": (run.quality_summary or {}).get("total_findings", 0),
            "blockers": (run.quality_summary or {}).get("blockers", 0),
            # El inventario COMPLETO de blockers persistidos: repórtalo entero
            # en el informe final (nunca un subconjunto).
            "blockers_detail": (run.quality_summary or {}).get(
                "blockers_detail", []
            ),
            "goals": (run.goals_summary or {}).get("goals", 0),
            "findings_merge": {
                k: merge_stats.get(k, 0)
                for k in ("preserved", "reopened", "inserted", "deleted")
            },
            "wave_delta": wave_delta,
            "message": (
                f"SRS versión {srs.version} generado (CANDIDATE) con "
                f"{srs.requirement_count} requerimientos."
            ),
        }

    @tool
    async def patch_narrative_section(
        section_id: str, text: str, note: str | None = None
    ) -> dict:
        """Edita UNA subsección authored del SRS vigente en el lugar, sin
        generar una versión nueva.

        Es la ruta PREFERIDA para ajustes de redacción conversacionales (p.
        ej. «agregá esta nota en supuestos»): reemplaza el texto de la
        subsección, regenera el markdown del documento y queda auditado.
        Requiere ``section_id`` de subsección authored existente (p. ej.
        ``intro.purpose``, ``overall.assumptions``). Las secciones proyectadas
        (functional, nfr, constraints, overall.actors) NO se parchean: se
        derivan del store de requerimientos.

        Args:
            section_id: subsección authored a editar (clave del narrative).
            text: el texto COMPLETO nuevo de la subsección (reemplaza el
                anterior, no es un diff).
            note: motivo del cambio (auditoría, opcional).

        No altera requerimientos ni hallazgos: si el ajuste requiere cambiar
        enunciados, usa update_requirements y después refresca con
        seed_from_last_srs + draft_narrative + commit_srs.
        """
        try:
            async with AsyncSessionLocal() as session:
                latest = await srs_store.get_latest_srs(session, project_id)
                if latest is None:
                    return {
                        "error": "no_srs",
                        "message": "Aún no hay un SRS generado.",
                    }
                version = latest.version
                srs = await srs_store.patch_narrative_section(
                    session,
                    project_id,
                    version,
                    section_id=section_id,
                    text=text,
                    note=note,
                )
        except KeyError as exc:
            return {
                "error": "not_found",
                "message": f"No se pudo parchear: {exc.args[0]}",
            }
        except ValueError as exc:
            return {"error": "patch_rejected", "message": str(exc)}
        on_progress, on_event = _make_emitters()
        await on_event("srs.patched", {
            "version": srs.version,
            "section_id": section_id,
        })
        return {
            "version": srs.version,
            "status": srs.status.value,
            "patched_section": section_id,
            "message": (
                f"Subsección {section_id} actualizada in-place en la "
                f"versión {srs.version} (sin versión nueva)."
            ),
        }

    @tool
    async def discard_srs_version(version: int) -> dict:
        """Descarta una version del SRS (soft): la fila queda como DISCARDED.

        Usalo cuando el usuario pida descartar/eliminar una version (tipico:
        versiones con prosa en mal estado que no quiere como base). La fila NO
        se borra, la numeracion nunca se reutiliza, y la version descartada
        deja de ser la «ultima»: el proximo seed_from_last_srs siembra desde
        la version anterior. LOCKED no se puede descartar.
        """
        try:
            async with AsyncSessionLocal() as session:
                discarded = await srs_store.discard_srs_version(
                    session, project_id, version
                )
                latest = await srs_store.get_latest_srs(session, project_id)
        except KeyError:
            return {
                "error": "not_found",
                "message": f"No existe la version {version} del SRS.",
            }
        except ValueError as exc:
            return {"error": "discard_rejected", "message": str(exc)}
        return {
            "discarded_version": discarded.version,
            "status": discarded.status.value,
            "new_latest_version": latest.version if latest else None,
            "message": (
                f"Version {discarded.version} descartada. La «ultima» ahora "
                f"es la v{latest.version if latest else '-'}: el proximo "
                "seed_from_last_srs siembra desde ahi."
            ),
        }

    return [
        seed_from_last_srs,
        analyze_quality,
        infer_goals,
        infer_goal_links,
        link_goal,
        link_goals,
        unlink_goal,
        edit_goal,
        merge_goals,
        list_run_findings,
        update_requirements,
        resolve_findings,
        apply_cures,
        close_curation_wave,
        apply_cure_batch,
        check_coverage,
        draft_narrative,
        commit_srs,
        patch_narrative_section,
        discard_srs_version,
    ]


def make_srs_agent_subagent(
    *,
    project_id: int,
    profile: str,
    project_slug: str,
    project_name: str = "",
    project_description: str = "",
) -> dict[str, Any]:
    """Construye el subagente srs-agent como dict para ``create_deep_agent``.

    No llama create_deep_agent: devuelve el descriptor que el orquestador
    recibe en ``subagents=[...]`` y DeepAgents envuelve como tool ``task``.
    """
    stage_tools = _make_stage_tools(project_id, project_name, project_description)
    doc_tools = make_document_read_tools(project_id)
    srs_read_tools = make_srs_read_tools(project_id)
    from backend.agents.tools.project_rules_tools import make_project_rules_tools

    rules_tools = make_project_rules_tools(project_id)
    tools = stage_tools + doc_tools + srs_read_tools + rules_tools
    return {
        "name": "srs-agent",
        "description": (
            "Subagente AGENTICO de generación de SRS: analiza la calidad de los "
            "requerimientos (INCOSE/smells/EARS + LLM), infiere el modelo de "
            "goals (GORE), audita la cobertura (ISO 25010), redacta la "
            "narrativa del SRS con LLM y persiste un SrsDocument CANDIDATE "
            "estructurado, editable y trazable. Razona etapa por etapa "
            "(calidad -> goals -> cobertura -> narrativa -> commit)."
        ),
        "system_prompt": SRS_AGENT_PROMPT,
        "tools": tools,
        # deepagents NO propaga el middleware del orquestador a los
        # subagentes (pero SI les inyecta FilesystemMiddleware): cada spec
        # necesita su guarda de tamaño, su filtro de tools de filesystem y
        # su summarizer con techo (reemplaza al default de deepagents por
        # nombre; sesion 17). El backend es el mismo sandbox del proyecto.
        # Best-effort: sin LLM_API_KEY (smoke tests) queda el default.
        "middleware": [
            SizeGuardMiddleware(),
            EmptyResponseRetryMiddleware(),
            NoFilesystemToolsMiddleware(),
            make_summarization_middleware(
                DockerSandbox(profile=profile, project_slug=project_slug)
            ),
        ],
    }
