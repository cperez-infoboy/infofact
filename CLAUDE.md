# InfoFact

## Qué es

Web app donde un usuario ejecuta un **agente IA que asiste el desarrollo de software por fases**. El agente está implementado con **LangChain DeepAgents** (Python). La fase inicial es **toma y definición de requerimientos**; las siguientes (análisis, implementación, testing, deploy) se agregan después.

> **Estado:** implementación activa. Existen `backend/` (FastAPI + modelos + routers + services + agents) y `frontend/` (SvelteKit SPA con layout IDE, auth, chat relay, explorer + viewer). Las fases 2-5 del agente siguen siendo propuesta; la fase 1 (requerimientos) está en desarrollo. `docs/definiciones/` tiene guías de referencia.

## Stack

- **Frontend:** SvelteKit (Svelte 5 runes) + Tailwind v4, compilado como **SPA pura** (`adapter-static`, `ssr: false`). Servida por FastAPI desde el mismo origen → sin CORS.
- **Backend:** FastAPI (Python 3.13), async SQLAlchemy + SQLite (aiosqlite).
- **Auth:** Google OAuth 2.0 → JWT en cookies HttpOnly. `credentials: 'include'` en cada `fetch`.
- **Agente:** LangChain DeepAgents, **corriendo en proceso dentro del backend** (ver Decisión 1).
- **Contenedor:** Docker multi-stage (Stage 1 build SvelteKit con `node:22-slim`, Stage 2 runtime Python).

## Arquitectura

```
┌──────────────────────────────────────────────────────────┐
│ NAVEGADOR — SPA SvelteKit                                 │
│  Chat UI + visor de documentos generados por el agente    │
└──────────────────────────┬───────────────────────────────┘
                           │ fetch (cookies JWT) + SSE
                           ▼
┌──────────────────────────────────────────────────────────┐
│ BACKEND — FastAPI                                         │
│  /api/auth/*      Google OAuth → JWT                      │
│  /api/chat/*      Relay SSE del agente DeepAgents         │
│  /api/projects/*  CRUD proyectos / sesiones               │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ AGENTE — LangChain DeepAgents (en proceso)          │ │
│  │  System prompt fija la fase actual del agente.      │ │
│  │  Stream de eventos → SSE al frontend.               │ │
│  └─────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

**Tres capas:** SPA (estado + UI), Backend (auth + relay + orquestación del agente), Agente (lógica IA). El backend **no conoce la lógica del agente**; solo lo invoca y relayea su stream.

## Decisiones de diseño

1. **Agente en proceso, no container por usuario.** DeepAgents corre dentro del proceso FastAPI. Razón: la fase de requerimientos produce documentos, **no ejecuta shell ni código** → no necesita aislamiento de contenedor. Migrar a container-per-user (patrón Hermes) cuando una fase requiera ejecutar código del lado del agente. Ver `docs/definiciones/webui-agent-architecture.md` §4.
2. **SPA pura.** Sin `hooks.server.ts`, sin `load` server-side. Auth 100% en FastAPI; la SPA solo lee estado client-side vía `getMe()` en `onMount`.
3. **Cookie HttpOnly + `credentials: 'include'`.** Token nunca expuesto a JS. Cookie con `SameSite=Lax`, `Secure` automático si `APP_URL` es https, `path=/`.
4. **`google_sub` como identidad primaria**, no email (Google permite cambiar email).
5. **SSE con `fetch + ReadableStream`**, nunca `EventSource` (no soporta POST ni headers custom).
6. **Stores Svelte 5:** en archivos `.ts` plano → `writable`/`derived`. Runes (`$state`, `$derived`, `$effect`) **solo** en `.svelte` y `.svelte.ts`. El build no avisa; falla en runtime.
7. **Sessions DB en router de chat:** usar `AsyncSessionLocal()` directamente, no `Depends` (conflicto conocido con `Depends(get_current_user)`).
8. **Profile estable por usuario:** slug alfanumérico `^[a-z0-9_-]{2,48}$` capturado en `/register`. Será la clave del workspace y (futuro) del container.

## Estructura propuesta

```
InfoFact/
├── backend/
│   ├── main.py
│   ├── config.py              # pydantic-settings desde .env
│   ├── database.py            # async engine + AsyncSessionLocal
│   ├── models/                # User, Project, ChatSession, ChatMessage, RequirementDoc
│   ├── routers/               # auth.py, chat.py, projects.py
│   ├── services/
│   │   ├── agent_service.py   # wrapper DeepAgents → stream de eventos
│   │   ├── chat_service.py    # relay SSE + system prompt por fase
│   │   └── file_service.py    # safe_path() contra path traversal
│   └── agents/
│       └── requirements_agent.py  # config DeepAgents para fase 1
├── frontend/
│   ├── svelte.config.js       # adapter-static
│   └── src/
│       ├── routes/            # +layout.svelte, +page.svelte, login/, register/
│       └── lib/
│           ├── api/           # client.ts, auth.ts, chat.ts, projects.ts
│           ├── stores/        # auth.ts, chat.ts, project.ts (.ts → writable)
│           └── components/    # ChatPanel, DocViewer, etc.
├── docs/definiciones/         # guías de referencia (no tocar salvo actualización)
├── Dockerfile                 # multi-stage
├── docker-compose.yml
├── .env.example
└── CLAUDE.md
```

## Contrato del agente (DeepAgents)

El backend expone el agente vía `POST /api/chat/sessions/{id}/messages` → stream SSE. Internamente:

1. `agent_service.run(message, history, phase)` invoca DeepAgents con su stream.
2. Eventos de DeepAgents se traducen a eventos SSE del frontend (`token`, `tool_call`, `tool_result`, `completed`, `failed`).
3. `build_instructions(phase, project)` arma el system prompt según la fase. En fase 1 fija rol de analista de requerimientos y formato de salida (SRS, user stories, criterios de aceptación).
4. Documentos generados por el agente se persisten como `RequirementDoc` ligado al `Project`.

> Cuando se migre a container-per-user (fase de implementación), el wrapper queda igual; solo cambia el transporte (HTTP al container en vez de invocación en proceso). **Mantener esa frontera limpia** — es lo que permite el swap sin tocar el resto del backend.

## Flujo de auth

1. Login → `GET /api/auth/google/login` → 302 a Google.
2. Callback → intercambia code → userinfo. Si existe user → JWT cookie → redirect `/`. Si no → redirect `/register?state=base64(google_data)`.
3. `/register` captura `profile` (slug) → `POST /api/auth/register` → crea user + JWT cookie.
4. `GET /api/auth/me` valida cookie → datos del user. `Depends(get_current_user)` protege rutas.

## Fases del agente

| Fase | Estado | Qué produce | Runtime |
|---|---|---|---|
| 1. Requerimientos | **actual** | SRS, user stories, criterios de aceptación | en proceso |
| 2. Análisis/Diseño | futuro | diseño técnico, modelos de datos | en proceso |
| 3. Implementación | futuro | código | **container-per-user** |
| 4. Testing | futuro | planes + casos de test | container-per-user |
| 5. Deploy | futuro | runbooks + config | container-per-user |

## Docs de referencia (leer antes de tocar cada área)

- `docs/definiciones/google-oauth-sveltekit-guide.md` — auth Google + SPA + FastAPI, end-to-end con código.
- `docs/definiciones/webui-agent-architecture.md` — arquitectura relay + container-per-user (patrón objetivo para fases 3+).

## MANDATORIO: MCP `docs-langchain` para cualquier duda de LangChain / DeepAgents

El server MCP **`docs-langchain`** (`https://docs.langchain.com/mcp`, HTTP) da acceso autoritativo a la documentación oficial de LangChain, LangGraph y **DeepAgents**. Antes de usar `WebFetch` / `WebSearch` o contestar de memoria sobre cualquiera de estos temas, **consultar este MCP primero**:

- APIs de `create_deep_agent`, `BaseSandbox`, backends, `astream_events`, subagents, checkpointer, middleware, permissions.
- Versiones y firmas exactas (los docs cambian; la memoria y el web pueden estar desactualizados).
- Comportamiento de sandbox backends (`execute()`, `upload_files`/`download_files`, `SandboxExecutionResult`).

**Verificación de carga por sesión:** `ListMcpResourcesTool` (sin `server`) debe listar `docs-langchain`. Si no aparece, el server se instaló después del arranque de la sesión → reiniciar Claude Code para activarlo. Mientras tanto, caer a `WebFetch` contra `https://docs.langchain.com/oss/python/deepagents/...` como fallback explícito.

## MANDATORIO: MCP `svelte` para cualquier duda de SvelteKit / Svelte 5

El server MCP oficial **`svelte`** da acceso autoritativo a la documentación de SvelteKit y Svelte 5 (runes, routing, stores, adapters). Antes de usar `WebFetch` / `WebSearch` o contestar de memoria sobre estos temas, **consultar este MCP primero**:

- APIs de Svelte 5 runes (`$state`, `$derived`, `$effect`, `$props`, `$bindable`), snippets, context.
- Routing de SvelteKit (`+page.svelte`, `+layout.svelte`, `+layout.server.ts`, load functions, actions).
- `adapter-static` y configuración SPA pura (`ssr: false`, fallback).
- Stores en `.ts` plano (cuándo usar `writable`/`derived` vs runes en `.svelte.ts`).

**Herramientas destacadas:**

- `get-documentation` — fetch de la doc oficial por tema/sección.
- `list-sections` — enumera las secciones disponibles para navegar la doc.
- `svelte-autofixer` — **usar después de editar componentes Svelte**; corrige patrones inválidos (rune misuse, reactivity bugs) contra el AST oficial. Tras tocar un `.svelte`, correr el autofixer para confirmar que no quedaron issues.
- `playground-link` — genera un link al REPL oficial para reproducir un caso.

**Verificación de carga por sesión:** `ListMcpResourcesTool` (sin `server`) debe listar `svelte`. Si no aparece, el server se instaló después del arranque → reiniciar Claude Code para activarlo.

## Comandos de desarrollo

> Pendiente hasta tener `backend/` y `frontend/`. Cuando existan:
>
> ```bash
> # Backend
> cd backend && uvicorn main:app --reload --port 8080
>
> # Frontend
> cd frontend && npm run dev
>
> # Build SPA dentro de la imagen
> docker compose up --build -d
> ```

## Convenciones

- **Commits:** conventional commits (`feat:`, `fix:`, `docs:`…). Sin atribución AI.
- **Identificadores / código:** en inglés.
- **Comentarios y textos / UI / docs:** Usa SIEMPRE español neutro (los docs existentes están en español). NO Uses "voseo" de ningún tipo como el argentino o uruguayo.
- **NO USES spanglish en los textos de ningún tipo** No uses palabras técnicas que mexcalan palabras en inglés con palabras en español. Da explicaciones en español normal.
- **CLI:** `bat`/`rg`/`fd`/`sd`/`eza` /`jq`, nunca `cat`/`grep`/`find`/`sed`/`ls`.
- **Deploy:** `docker compose up --build -d`. Un `docker restart` **no** aplica cambios de código (están copiados en la imagen).

## Trampas conocidas (de los docs)

- Olvidar `credentials: 'include'` → cookie JWT no viaja → 401 en todo.
- Login button debe ser `<a href>` o `window.location`, **no** `fetch` (OAuth requiere redirect de navegador).
- `ssr: true` rompe el modelo de auth basado en cookie HttpOnly client-side.
- Cookie sin `path=/` solo se envía bajo el prefijo actual.
- Stores `.ts` con runes → falla silenciosa en runtime.
- **Librerías CSS globales (NES.css, Bootstrap, Bootstrap Reboot) cargadas vía `<link>` en `app.html`** tienen reglas **unlayered** que **pisan** cualquier cosa en `@layer base`/`@layer utilities` de Tailwind v4. En cascade CSS, **unlayered siempre le gana a layered**. Síntomas: body blanco tapando el dark, `main`/`section`/`aside`/`header`/`footer` forzados a `display: block` (rompe flex → paneles verticales), form controls con estilo ajeno. Si se necesita una lib CSS externa, importarla **dentro de una capa** (`@import "..." layer(external)`) o usar solo partes scoped — nunca el bundle global completo.

---

<!-- gortex:communities:start -->
## Codebase Overview (generated by Gortex)

- **Languages:** markdown (primary)
- **Graph size:** 151 nodes, 128 edges
- **Breakdown:** 57 docs, 2 files, 92 variables

## MANDATORY: Use Gortex MCP tools instead of Read/Grep/Glob

Gortex is running as an MCP server. You **MUST** prefer graph queries over file reads on every task in this repo — `search_symbols`, `find_usages`, `get_symbol_source`, `get_editing_context`, `smart_context`, `edit_symbol` / `edit_file` / `rename_symbol` / `batch_edit`. PreToolUse hooks deny `Read` / `Grep` / `Glob` against indexed source; the deny message names the right tool. The full per-tool catalog loads via `tools/list` — not restated here.

### Calibration: the graph narrows scope, source confirms behavior

The mandate above stands — but graph queries *narrow scope*, they do not *replace reading the implementation*. The graph tells you **where** the logic lives and **what** connects to it; the source tells you **how** it behaves. For the symbol you are about to change or depend on, read its full body with `get_symbol_source` — do not act on a one-line summary alone.

Be especially deliberate with **behavior-critical code** — database migrations, retry / fallback / error-recovery paths, compatibility shims, concurrency-sensitive sections, and the tests that pin them. For these, call `get_symbol_source` and read the real implementation; never pass `compress_bodies:true`, which elides exactly the branches that carry the risk. Reserve compressed bodies and graph summaries for breadth (surveying many symbols); use full source for the few you are about to commit to.

## Required workflow (every task on this repo)

These are not suggestions — run each step at the trigger.

1. Confirm the daemon is up with `index_health` (cheap liveness + scope). Call `graph_stats` only when you actually need node/edge counts or `per_repo` orientation — it returns a large payload and can block during warmup.
2. If `total_nodes` is 0, **call** `index_repository` with `"."` before anything else.
3. In multi-repo mode, **call** `get_active_project` to check scope; use `set_active_project` to switch.
4. Open a non-trivial task with `smart_context` for orientation. For a single known symbol or file, go straight to `search_symbols` / `get_symbol_source` — don't front-load `smart_context` before every read.
5. Before editing a file, **call** `get_editing_context` on it first.
6. Before changing any function signature, **call** `verify_change` to catch broken callers and interface implementors (cross-repo).
7. For any refactor, **call** `get_edit_plan` then `batch_edit` to apply atomically.
8. Verify with the project's real build/test. Reserve `check_guards` for guard-relevant changes and `get_test_targets` to find the tests covering a substantive change — not mechanically after every edit.

<!-- gortex:communities:end -->
