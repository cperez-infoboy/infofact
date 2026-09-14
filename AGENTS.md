# InfoFact

> Contexto de proyecto para agentes ZCode y Claude Code. `CLAUDE.md` es un **stub** que importa este archivo (`@AGENTS.md`): la fuente única del contexto vive acá — actualiza solo este archivo. Este archivo refleja el estado del código verificado en 2026-09-02.

## Qué es

Web app donde un usuario ejecuta un **agente IA que asiste el desarrollo de software por fases**. El agente está implementado con **LangChain DeepAgents** (Python). La fase activa es **toma y definición de requerimientos**: captura de requerimientos desde documentos + síntesis de un SRS con calidad asegurada. Las siguientes (análisis, implementación, testing, deploy) se agregan después.

> **Estado:** implementación activa. El backend ya incluye el pipeline de captura (ingesta → extracción → agrupado → crítica → consolidación), el subagente de SRS por etapas (calidad → goals → cobertura → commit), persistencia tipada de requerimientos y versionado de SRS con hallazgos y cobertura. `docs/definiciones/` tiene guías de referencia.

## Stack

- **Frontend:** SvelteKit (Svelte 5 runes) + Tailwind v4, compilado como **SPA pura** (`adapter-static`, `ssr: false`). Servido por FastAPI desde el mismo origen → sin CORS.
- **Backend:** FastAPI (Python 3.13), async SQLAlchemy + SQLite (aiosqlite).
- **Auth:** Google OAuth 2.0 → JWT en cookies HttpOnly. `credentials: 'include'` en cada `fetch`.
- **Agente:** LangChain DeepAgents. El razonamiento LLM corre **en el proceso de FastAPI**; las tools de filesystem/shell del agente corren en un **container por usuario** (patrón Hermes, vía `docker exec`). Ver Decisión 1.
- **Procesamiento de documentos:** docling + OCR GLM (`backend/agents/parsers/`), embeddings locales con `sentence-transformers` para consolidación (dedup + contradicciones, sin salir a APIs).
- **Contenedor:** Docker multi-stage (Stage 1 build SvelteKit con `node:22-slim`, Stage 2 runtime `python:3.13-slim` + `docker-cli`).

## Arquitectura

```
┌──────────────────────────────────────────────────────────┐
│ NAVEGADOR — SPA SvelteKit                                 │
│  Chat UI + visor de documentos / SRS / análisis           │
└──────────────────────────┬───────────────────────────────┘
                           │ fetch (cookies JWT) + SSE
                           ▼
┌──────────────────────────────────────────────────────────┐
│ BACKEND — FastAPI (host o container infofact-webui)       │
│  /api/auth/*       Google OAuth → JWT                     │
│  /api/chat/*       Relay SSE del agente DeepAgents        │
│  /api/projects/*   CRUD proyectos / sesiones / workspaces │
│  /api/projects/*/srs|requirements|analysis|documents|...  │
│                                                           │
│  ┌───────────────────────────────┐  ┌───────────────────┐ │
│  │ AGENTE — DeepAgents           │  │ CONTAINER AGENTE  │ │
│  │ (en proceso FastAPI):         │  │ infofact-{slug}   │ │
│  │ orquestador + subagentes      │─▶│ sin HTTP; solo    │ │
│  │ capture (/captura) y SRS (/srs│  │ docker exec:      │ │
│  │ Pipelines de extracción en    │  │ shell + archivos  │ │
│  │ proceso (docling/embeddings)  │  │ del workspace     │ │
│  └───────────────────────────────┘  └───────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

**Tres capas:** SPA (estado + UI), Backend (auth + relay + orquestación del agente), Agente (lógica IA + sandbox). El backend **no conoce la lógica del agente**; solo lo construye (`agent_service.py`) y relayea su stream.

## Decisiones de diseño

1. **Razonamiento en proceso, efectos en container por usuario.** El agente DeepAgents se construye por request dentro de FastAPI, bound al container `infofact-{profile}` del usuario. Las tools de shell/archivos ejecutan vía `DockerSandbox` (`docker exec`); el container no sirve HTTP ni expone puertos. El estado persiste en (a) el filesystem del workspace montado y (b) el checkpointer SQLite (`AsyncSqliteSaver`, key = `ChatSession.id`). Lifecycle en `container_service.py`: reuse / start / create, con sweep de containers idle (default 30 min). Razón de la frontera: reemplazar el agente solo toca `agent_service.py`.
2. **SPA pura.** Sin `hooks.server.ts`, sin `load` server-side. Auth 100% en FastAPI; la SPA solo lee estado client-side vía `getMe()` en `onMount`.
3. **Cookie HttpOnly + `credentials: 'include'`.** Token nunca expuesto a JS. Cookie con `SameSite=Lax`, `Secure` automático si `APP_URL` es https, `path=/`.
4. **`google_sub` como identidad primaria**, no email (Google permite cambiar email).
5. **SSE con `fetch + ReadableStream`**, nunca `EventSource` (no soporta POST ni headers custom).
6. **Stores Svelte 5:** en archivos `.ts` plano → `writable`/`derived`. Runes (`$state`, `$derived`, `$effect`) **solo** en `.svelte` y `.svelte.ts`. El build no avisa; falla en runtime.
7. **Sessions DB en router de chat:** usar `AsyncSessionLocal()` directamente, no `Depends` (conflicto conocido con `Depends(get_current_user)`).
8. **Profile estable por usuario:** slug alfanumérico `^[a-z0-9_-]{2,48}$` capturado en `/register`. Es la clave del workspace y del container del agente.
9. **Requerimientos como filas tipadas, no Markdown.** La captura persiste `RequirementItem`; el SRS se versiona como `SrsDocument` con hallazgos de calidad, goals y matriz de cobertura en DB.
10. **Secrets NUNCA se inyectan al container del agente:** el agente corre adentro y podría exfiltrar lo que haya en su environment. LLM calls salen desde el proceso backend (`LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` en `.env`; cualquier endpoint OpenAI-compatible).

## Estructura real

```
InfoFact/
├── backend/
│   ├── main.py                  # entrypoint; lifespan del checkpointer; migraciones idempotentes
│   ├── config.py                # pydantic-settings desde .env
│   ├── database.py              # async engine + AsyncSessionLocal
│   ├── models/                  # User, Project, ChatSession, RequirementItem, SrsDocument, ProjectActor, ...
│   ├── routers/                 # auth, chat, projects, workspaces, requirements, srs, analysis, documents, project_rules
│   ├── services/                # dominio: agent_service, container_service, chat/file/document services
│   │   ├── requirement_store.py     # RequirementItem (captura)
│   │   ├── srs_store.py             # versiones SRS + hallazgos + goals
│   │   ├── goals_engine.py          # goals tipados y su cobertura
│   │   ├── srs_{assembler,builder,coverage,quality}.py
│   │   └── actor_store.py, grouping_store.py, analysis_store.py, ...
│   └── agents/
│       ├── llm.py, llm_retry_guard.py   # ChatOpenAI + retry ante respuestas vacías
│       ├── size_guard.py                # middleware de acote de salidas
│       ├── no_fs_tools.py               # middleware: subagentes sin tools de FS
│       ├── sandboxes/docker_sandbox.py  # BaseSandbox sobre docker exec
│       ├── pipelines/                   # ingestion, extraction, grouping, critique, consolidation,
│       │                                # classification, preflight, parse_cache, _quality_rules, _resilience
│       ├── subagents/                   # requirements_capture_agent, srs_agent + run_holders
│       ├── tools/                       # requirements_tools, srs_tools, documents_tools, rag_tools,
│       │                                # vision_tools, web_search, actor_tools, grouping_tools
│       └── parsers/                     # docling / GLM OCR + router de parsers
├── frontend/
│   ├── svelte.config.js         # adapter-static
│   └── src/
│       ├── routes/              # +layout.svelte, login/, register/
│       └── lib/
│           ├── api/             # client.ts, auth.ts, chat.ts, projects.ts, srs.ts
│           ├── stores/          # auth.ts, chat.ts, project.ts (.ts → writable)
│           ├── components/      # ChatPanel, SrsViewer, CaptureStatus, DocViewer, ...
│           └── utils/
├── docs/
│   ├── definiciones/            # guías de referencia (no tocar salvo actualización)
│   ├── planeaciones/            # fotos de sesión / planes
│   └── testing/                 # visión de producto, catálogo de requisitos, resultados E2E
├── prototypes/                  # prototipos sueltos
├── scripts/                     # utilidades operativas
├── Dockerfile                   # multi-stage: build SPA + runtime python con docker-cli
├── Dockerfile.agent             # imagen del container del agente (CMD sleep infinity)
├── docker-compose.yml           # infofact-webui, network_mode host, docker.sock bind-mount
├── .env                  # configuración (pydantic-settings); no commitear
├── CLAUDE.md                    # gemelo de este archivo (Claude Code)
└── AGENTS.md                    # este archivo
```

## Contrato del agente (DeepAgents)

El backend expone el agente vía `POST /api/chat/sessions/{id}/messages` → stream SSE. Internamente:

1. `agent_service` construye por request un agente orquestador con system prompt que fija la fase y **delega a subagentes** vía la tool `task`:
   - `requirements-capture-agent` (`/captura`): ingesta de documentos y extracción/curadura de requerimientos.
   - `srs-agent` (`/srs`): síntesis del SRS por etapas sobre los requerimientos vivos.
2. Los subagentes trabajan con **run-holders en proceso** keyed por `project_id` (uno activo por proyecto): cada tool de etapa acumula su salida tipada en el holder y emite eventos finos al SSE; solo la tool de commit persiste.
3. **Captura en 7 etapas** (`capture_run_holder.py`): guard de datos existentes → ingest → extract (con `verify_spans`, anclaje anti-alucinación) → agrupado → crítica → consolidación → commit. El agente razona ENTRE etapas. `commit_capture` lee SOLO del holder, y los ítems solo entran vía `extract_requirements` → todo requerimiento persistido está respaldado por un span verificado de fuente. La ingesta AUTO-REGISTRA cada documento descubierto en el catálogo `ProjectDocument` (`ensure_document_registered`, dedupe por `(project_id, sha256)`, corrige `rel_path` si el archivo se movió) y marca `used_in_capture` por id: el conteo de fuentes y el RAG del SRS pasan por el catálogo, así que un documento sin fila es invisible aunque sostenga requerimientos. `reconcile_document_catalog` (migración idempotente de arranque) repara catálogos viejos contra las fuentes citadas en `RequirementItem.source` (sha real del archivo host, o sha sintético keyed por ruta si desapareció).
4. **Actores: detección granular + consolidación semántica.** La etapa opcional `identify_actors` (entre conventions y extract) detecta actores POR DOCUMENTO (granular a propósito: «Google Maps», «OSRM») y después consolida semánticamente: agrupa los granulares que convergen en un rol general («Proveedor de cartografía»), crea/reusa el general absorbiendo los nombres de los miembros como sinónimos y los RETIRA (soft, `ActorStatus.RETIRED`). Los enunciados ya persistidos que citan actores absorbidos se reescriben vía `consolidate_project_actors` (cada cambio queda auditado en `RequirementRevision`, razón `actor_consolidation`). Best-effort: si la consolidación falla, queda el catálogo granular.
5. **SRS en etapas** (`srs_agent.py`): quality (INCOSE/smells/EARS) → goals → cobertura → narrativa → commit. El orden es una dependencia real: `compute_coverage` lee los goals persistidos. `seed_from_last_srs` siembra el holder desde el último SRS persistido para re-arranques de solo-narrativa sin re-juzgar todo. Anti-goteo (sesión 16 de Planitrack2.0): `analyze_quality` es DELTA (la Fase B LLM solo re-juzga ítems con `quality_fingerprint` cambiado; los intactos reutilizan el veredicto persistido) y devuelve `blockers_detail` — el inventario COMPLETO de blockers que el agente debe reportar en UNA tabla (nunca por partes). El commit persiste hallazgos con `merge_findings` (la curación fixed/waived sobrevive mientras el enunciado no cambie; el sello de fingerprints viaja en la misma transacción). El srs-agent puede curar: `update_requirements` (compartida con captura), `resolve_findings` (cierre formal auditable), `apply_cures` (ediciones + cierres), `apply_cure_batch` (cura masiva de una regla: preview `dry_run` + aplicación chunked de la suggestion cuando es reescritura completa del enunciado) y el catálogo de goals: `edit_goal` (statement/rationale/status — resucitar STALE, confirmar o retirar; CONFIRMED/REJECTED son decisión humana que el upsert de `infer_goals` preserva) y `merge_goals` (fusiona N goals en un keeper: re-apunta aristas in-place preservando `detected_by` humano, absorbe como REJECTED — soft — y re-parenta sub-goals; es la vía quirúrgica de consolidación sin re-inferencia y elimina la divergencia `goals.total` del anexo vs resumen). `update_requirements` autocierra SOLO hallazgos deterministas cuyo patrón dejó de disparar; los de juez LLM quedan `llm_findings_pending` en el retorno (el veredicto llega con el re-análisis delta — reportarlos como deuda, no como resueltos; sesión 18: la cura por capture-agent los dejó sin veredicto y el agente los infirió de revisiones). El requirements-capture-agent incluye las read tools de calidad (`get_quality` paginado + `get_requirement_findings`): ve el backlog OPEN antes de curar, y su prompt lo dirige a sugerir `/srs` para el veredicto en vez de re-analizar por su cuenta. Anti-cascada de curación (sesión 18): `apply_cures` NO re-analiza por defecto (`reanalyze=False`) — registra una OLA (`SrsRun.dirty_req_ids` + snapshot del inventario) y `close_curation_wave` la cierra con UN solo re-análisis delta cuyo retorno es el DELTA (resueltos/nuevos contra el snapshot), no el inventario; `commit_srs` cierra cualquier ola olvidada antes de persistir (jamás hallazgos rancios). Convergencia de identidad (post v21: 192 inserted/209 deleted por corrida): el juez LLM solo emite `rule_id` canónicos (`sem.*`, Literal cerrado de 6) con máx 2 hallazgos material por ítem y 1 por regla, y `merge_findings` matchea los hallazgos de conjunto por `(dimension, rule_id)` SIN el message — el waive de un agregado (p. ej. «reqs sin goal») sobrevive aunque cambie el conteo del mensaje. Los posesivos su/sus ya no disparan `smell.pronoun` (eran el 29% del backlog: ruido); sí el doble posesivo y «cuyo/la cual». `draft_narrative` materializa una versión DRAFT visible en el visor antes del commit, que la promueve in-place a CANDIDATE. La §1.4 Referencias es PROYECCIÓN determinista del catálogo de fuentes (`_capture_sources` + `_projected_references`): tabla viva con conteo de requerimientos por fuente, las imágenes embebidas de `.infofact-media/` agrupadas como «Anexo de imágenes (OCR)» y las normas fijas — el LLM redacta 7 subsecciones authored y el carry-forward jamás la congela.
5b. **Anti-cascada de versiones (sesión 17 de Planitrack2.0).** El commit ya no es el único escritor: `patch_narrative_section(section_id, text, note)` edita UNA subsección authored del SRS vigente en el lugar (UPDATE del narrative + re-render del markdown vía `build_srs`, auditoría en `review_flags.narrative_patches`, sin versión nueva, sin LLM) — es la ruta preferida para ajustes de redacción conversacionales. Además `commit_srs` tiene guard anti-no-op: persiste `srs_commit_signature` (SHA de prosa authored + enunciados+códigos de reqs vivos + conteos + goals + cobertura totals) en `review_flags.commit_signature` y, si la firma nueva coincide con la de la versión CANDIDATE vigente, responde `no_new_version` sin crear fila. El prompt del srs-agent enruta: redacción → patch; cambios de reqs → seed+draft+commit; y post-commit verifica LIVIANO (metadatos + `list_srs_sections` + conteos), nunca releyendo el documento (`get_latest_srs` full era la fuga de 2.9 MB).
5c. **Anti-overflow de ventana (misma sesión 17).** La caída (Anthropic 1261 «prompt is too long») vino de ~4.9 MB de tool_results en el subagente: `get_latest_srs` devolvía el documento entero (2.9 MB) y `get_quality` todos los hallazgos (1.9 MB); el summarizer de deepagents reenvió la historia SIN recortar (su fábrica pasa `trim_tokens_to_summarize=None`, y None = sin trim). Contramedidas: (1) `get_latest_srs` sin argumentos devuelve SOLO metadatos + índice de secciones con tamaños; la prosa se pide por subsección (`section_id`, cap 30K chars); (2) `get_quality` pagina hallazgos (40 por página, filtro `severity`, `more_after`); (3) `goal_coverage` acota `without_goal_codes` a 60 con flag de truncado; (4) `read_srs_section` acota secciones proyectadas a 120 items; (5) `make_summarization_middleware` (en `size_guard.py`) reconstruye el SummarizationMiddleware de deepagents con techo (`LLM_SUMMARIZE_TRIM_CHARS`, default 100K chars ≈ 25K tokens) — el middleware devuelto reporta name `SummarizationMiddleware`, así que el ensamblador de deepagents lo REEMPLAZA por nombre en orquestador y los 3 specs de subagentes; (6) el relay del chat detecta overflow (`ContextWindowExceededError`/«prompt is too long»/1261, envuelto en capas litellm/langchain) y compacta el thread con `RemoveMessage(REMOVE_ALL_MESSAGES)` + misma lista de mensajes con contenidos acotados (pares tool_call/ToolMessage intactos), avisando `recovered: context_compacted` al frontend — el reintento encuentra la sesión usable.
6. **Goals: exhaustivos y con memoria.** La vinculación exige el mejor goal para CADA req (el prompt viejo decía «skip the rest» y dejaba cientos sin link); `check_coverage` reporta `unlinked_requirements` y el prompt obliga a cerrar la cola con `infer_goal_links` antes del commit. El catálogo se acota POR INSTRUCCIÓN (consolidación LLM con rango objetivo 10-30); `_MAX_GOALS` es solo un techo de safety env-tunable (`INFOFACT_MAX_GOALS`, default 60) que re-consolida antes que cortar. `upsert_goals` no destruye: el PROPOSED ausente se marca `STALE` (revive si vuelve a detectarse) y los links `detected_by="human"` sobreviven a las re-inferencias. `infer_goals` es delta (`goals_fingerprint`): un re-run sin ediciones es no-op con cero llamadas LLM. Fallback de embeddings opt-in (`INFOFACT_GOAL_LINK_EMBEDDINGS=1`) marca links sugeridos.
7. Eventos internos se traducen a SSE (`token`, `tool_call`, `tool_result`, `completed`, `failed`).
8. Salidas acotadas por `SizeGuardMiddleware` (texto de cada mensaje) y concurrencia del pipeline configurada, para no exceder timeouts del gateway LLM. El summarizer de deepagents corre con techo propio (`make_summarization_middleware`); el overflow igual puede llegar de fuentes no medidas y el relay lo recupera compactando el thread (punto 5c).

## Flujo de auth

1. Login → `GET /api/auth/google/login` → 302 a Google.
2. Callback → intercambia code → userinfo. Si existe user → JWT cookie → redirect `/`. Si no → redirect `/register?state=base64(google_data)`.
3. `/register` captura `profile` (slug) → `POST /api/auth/register` → crea user + JWT cookie.
4. `GET /api/auth/me` valida cookie → datos del user. `Depends(get_current_user)` protege rutas.

## Fases del agente

| Fase | Estado | Qué produce | Runtime |
|---|---|---|---|
| 1. Requerimientos | **actual** | requerimientos tipados + SRS versionado con calidad, goals y cobertura | reasoning en proceso + sandbox container por usuario |
| 2. Análisis/Diseño | en curso (ver `docs/definiciones/fase2-*`) | diseño técnico, modelos de datos | igual que fase 1 |
| 3. Implementación | futuro | código | mismo patrón de sandbox |
| 4. Testing | futuro | planes + casos de test | mismo patrón de sandbox |
| 5. Deploy | futuro | runbooks + config | mismo patrón de sandbox |

## Docs de referencia (leer antes de tocar cada área)

- `docs/definiciones/google-oauth-sveltekit-guide.md` — auth Google + SPA + FastAPI, end-to-end con código.
- `docs/definiciones/webui-agent-architecture.md` — arquitectura relay + container-per-user (patrón objetivo para fases 3+).

## Comandos de desarrollo

```bash
# Backend (desde la raíz del repo; los imports son backend.*)
.venv/bin/python -m uvicorn backend.main:app --reload --port 8080

# Tests backend (pytest desde la raíz; pytest 9.x)
.venv/bin/python -m pytest backend/tests/test_algo.py -x
.venv/bin/python -m pytest backend/tests/ -x

# Frontend (desde frontend/)
cd frontend && npm run dev        # Vite dev
cd frontend && npm run check      # svelte-check (correr tras editar .svelte)
cd frontend && npm run test       # vitest

# Deploy (la imagen copia el código; docker restart NO aplica cambios)
docker compose up --build -d
```

SQLite y checkpoints viven en `data/` (bind-mount de compose). `INFOFACT_CONCURRENCY` regula la concurrencia de pipelines y etapas del SRS.

## Entorno y pins críticos

- Python 3.13 (venv en `.venv/`, requirements.txt en la raíz). Torch/torchvision se instalan ANTES desde el índice CPU de PyTorch (ver nota en `requirements.txt`).
- `deepagents==0.7.6` y `langchain==1.3.15` **pinados**: 1.3.15 introduce `lc_internal_call` para llamadas internas de middleware, que el relay SSE de `chat.py` filtra; versiones menores no marcan esas llamadas y el filtro no dispara.
- `INFOFACT_CONCURRENCY=4` (default bajado de 8): con 8, el run contra litellm/claude-opus-5 generó tormentas de 429 sostenidas y colas que superaban el request_timeout del gateway (408). No subir sin medir contra el upstream real de cada modelo.
- Compose: `network_mode: host` + bind-mount de `docker.sock`. La trampa DinD: el workspace debe montarse al MISMO path absoluto dentro del WebUI y en el host (`WORKSPACES_HOST_PATH`), o `docker run -v` del agente cae en el inodo equivocado. SQLite NUNCA sobre network FS.

## MCPs: dudas de LangChain/DeepAgents y Svelte

- **LangChain / LangGraph / DeepAgents:** no hay MCP dedicado en ZCode. Usar `context7` (resolve + query-docs) para APIs de `create_deep_agent`, `BaseSandbox`, `astream_events`, subagents, checkpointer, middleware. Fallback explícito: `WebFetch` contra `https://docs.langchain.com/oss/python/deepagents/...`. Los pins de `requirements.txt` mandan sobre cualquier doc: verificar la firma contra la versión instalada en `.venv` si hay duda.
- **SvelteKit / Svelte 5:** el MCP `svelte` está declarado en `.mcp.json` del proyecto (stdio, `@sveltejs/mcp`). Si está cargado en la sesión, preferirlo: `get-documentation`, `list-sections`, `svelte-autofixer` (**usarlo después de editar cualquier `.svelte`**) y `playground-link`. Si no aparece cargado, caer a `context7` + `npm run check`.

## Gortex (grafo de código)

Daemon systemd usuario `com.zzet.gortex`, índice auto-sincronizado. El mandato de preferir el grafo está reforzado MECÁNICAMENTE por el hook de usuario `gortex-guard` (`~/.config/gortex-guard/guard.sh` en la config de ZCode): niega Read/Grep/Glob sobre código fuente con el desvío a la tool equivalente, y Edit/Write directo (pide `read(editing_context)` antes). **Alcance del guard: SOLO el agente principal** — los hooks PreToolUse de ZCode no se evalúan para las tool calls de los subagentes (verificado empíricamente); los subagentes sí reciben estos AGENTS.md como contexto. **Default de delegación (no opcional):** toda exploración/consulta sobre código fuente indexado se delega en los agentes custom globales `gortex-search` (localizar/trazar/arquitectura + mem_search/mem_save de engram) e `gortex-impact` (blast radius/contratos/tests), definidos en `~/.zcode/agents/` con toolset exclusivamente `mcp__gortex__*` — NO en Explore. Explore queda para docs, configs, datos o repos no indexados. Si por excepción delegás en un agente sin tools MCP, pásale la sintaxis CLI exacta en el prompt: `gortex call search --arg operation=symbols --arg query='<nombre>'`, `gortex context --task '<desc>'`, `engram search '<query>' --project <p> --limit N` (NO existe `gortex search` directo; `gortex context` exige `--task`). Docs, `.md`, configs y datos pasan libres (ahí Read nativo es correcto). Kill-switch: `touch ~/.config/gortex-guard/off` si el daemon está caído o el repo no está indexado. Mapa de nombres (fachada ZCode = dominio + operación): `search(symbols)`, `relations(usages|callers|dependents|implementations)`, `read(source|editing_context)`, `explore(task|context|localize)` (`options.new_user_task:true` solo en la 1ª llamada de una petición nueva), `edit(file|symbol|batch)`, `refactor(rename|...)`, `change(verify|edit_plan|api_impact|receipt|tests)`, `review(run)`, `trace(call_chain|path|flow)`, `analyze(...)` (~78 kinds), `workspace(info|index)`, `workspace_admin(index|reindex)`.

Calibración: el grafo ENANGOSTA el alcance, NO reemplaza leer la implementación. Para el símbolo que vas a cambiar o del que dependes, `read(source)` del cuerpo completo. En código crítico de comportamiento (run-holders, guardas de stages, retry/fallback, `_resilience.py`, migraciones idempotentes de `main.py`, y sus tests) leer la implementación real; nunca `compress_bodies`. Pre-mutación: `read(editing_context)`; cambios de firma → `change(verify)`; refactors → `change(edit_plan)` + `edit(batch)`; rutas HTTP → `change(api_impact)`. Cierre: `change(receipt)`, `change(tests)` para cambios sustantivos, y el build/tests reales del proyecto. Protocolo completo en Engram (`config/gortex-usage-protocol`).

## Convenciones

- **Commits:** conventional commits (`feat:`, `fix:`, `docs:`…). Sin atribución AI.
- **Identificadores / código:** en inglés.
- **Comentarios y textos / UI / docs:** SIEMPRE español neutro. NO usar voseo (ni argentino ni uruguayo). NO usar spanglish ni palabras híbridas inglés-español; explicar en español normal.
- **CLI:** `bat`/`rg`/`fd`/`sd`/`eza`/`jq`, nunca `cat`/`grep`/`find`/`sed`/`ls`.
- **Deploy:** `docker compose up --build -d`. Un `docker restart` no aplica cambios de código.

## Trampas conocidas

- **Usar una AsyncSession DESPUÉS del cierre de su `async with` revive una conexión que nunca vuelve al pool** (`non-checked-in connection` en logs/SAWarning en tests). Ocurrió real en `draft_narrative` (`_functional_goal_groups` fuera del bloque, corregido en la sesión 18 con test de regresión `test_draft_narrative_returns_connection_to_pool`). Todo acceso DB va DENTRO del `async with AsyncSessionLocal()`.
- **`list_requirements` acota el enunciado a 500 chars por fila** (`statement_chars` marca el largo real; texto completo vía `get_requirement`): los summaries de listado no deben pesar cientos de KB en el thread del agente.

- Olvidar `credentials: 'include'` → cookie JWT no viaja → 401 en todo.
- Login button debe ser `<a href>` o `window.location`, **no** `fetch` (OAuth requiere redirect de navegador).
- `ssr: true` rompe el modelo de auth basado en cookie HttpOnly client-side.
- Cookie sin `path=/` solo se envía bajo el prefijo actual.
- Stores `.ts` con runes → falla silenciosa en runtime.
- Streaming LLM: usar pasadas estructuradas sobre streaming acumulado; el upstream non-streaming tiene stalls conocidos.
- Concurrencia de pipeline: ver pins de entorno — no subir `INFOFACT_CONCURRENCY` sin medir.
- **Librerías CSS globales (NES.css, Bootstrap, Bootstrap Reboot) cargadas vía `<link>` en `app.html`** tienen reglas **unlayered** que **pisan** cualquier cosa en `@layer` de Tailwind v4 (unlayered siempre le gana a layered). Síntomas: body blanco tapando el dark, secciones forzadas a `display: block` (rompe flex), form controls con estilo ajeno. Si se necesita una lib CSS externa, importarla dentro de una capa (`@import "..." layer(external)`) o usar solo partes scoped.
