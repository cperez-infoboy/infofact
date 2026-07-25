# Guía de Arquitectura: WebUI + Contenedores por Usuario

> **Propósito.** Este documento describe cómo está construido Hermes WebUI para que un agente de desarrollo pueda replicar el sistema en un proyecto nuevo, manteniendo:
>
> 1. Una **interfaz web tipo IDE** (VSCode-like) con explorador de archivos, visor con tabs y terminal.
> 2. **Aislamiento por contenedor Docker por usuario**, donde cada usuario obtiene su propio agente corriendo en la nube.
> 3. Un **punto de integración claro para el agente** (en este proyecto es Hermes Agent; en el proyecto nuevo puede ser cualquier agente que cumpla el contrato HTTP/SSE descrito en §9).
>
> El WebUI es un **relay**, no ejecuta el agente. El agente corre dentro de su propio contenedor, y el WebUI habla con él por HTTP/SSE y por `docker exec` para la terminal.

---

## 1. Visión general del sistema

```
┌─────────────────────────────────────────────────────────────────────┐
│                          NAVEGADOR (SPA)                             │
│  SvelteKit + Tailwind  →  adaptador estático (adapter-static)        │
└───────────────┬─────────────────────────────────────────────────────┘
                │  fetch (cookies JWT HttpOnly)  +  WebSocket (terminal)
                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  CONTENEDOR DEL WebUI  (FastAPI)                     │
│  - Auth (Google OAuth → JWT)                                         │
│  - CRUD workspaces / archivos                                        │
│  - Relay SSE del chat  →  contenedor del agente                      │
│  - WebSocket PTY       →  docker exec en contenedor del agente       │
│  - Gestión del ciclo de vida del contenedor del agente (hermes_service)│
└───────┬──────────────────────────────────────┬──────────────────────┘
        │ docker run / docker exec / docker rm │ HTTP/SSE
        ▼                                       ▼
┌────────────────────────────┐   ┌──────────────────────────────────────┐
│   CONTENEDOR DEL AGENTE    │   │  /workspaces/{profile}/  (en el host) │
│   (uno por usuario)        │◄──┤  montado como /workspaces/ dentro     │
│   hermes-{profile}         │   │  del contenedor del agente            │
│   puerto: {BASE_PORT + N}  │   └──────────────────────────────────────┘
└────────────────────────────┘
```

**Tres procesos distintos que el agente nuevo debe entender:**

| Proceso | Dónde corre | Responsabilidad |
|---|---|---|
| **SPA** | En el navegador del usuario | UI, estado, llamada a la API del WebUI |
| **WebUI (FastAPI)** | En un contenedor Docker propio | Auth, archivos, relay, orquestación de contenedores del agente |
| **Agente** | En **un contenedor Docker por usuario** | El trabajo de IA real. Expone HTTP/SSE y un shell interactivo |

> **El WebUI NO conoce la lógica del agente.** Solo sabe: (a) levantar su contenedor, (b) enviarle mensajes por HTTP, (c) abrirle una terminal por `docker exec`. Esto es exactamente lo que permite **reemplazar Hermes Agent por cualquier agente** sin tocar el WebUI.

---

## 2. La pieza que se reemplaza: EL AGENTE

Este es el punto clave para el proyecto nuevo. En Hermes WebUI, el agente es `nousresearch/hermes-agent`. Para tu proyecto, puedes usar **cualquier agente** siempre que cumpla estos tres requisitos:

1. **Arrancar como proceso dentro de un contenedor Docker**, exponiendo un servicio HTTP en un puerto.
2. **Exponer una API HTTP de chat con streaming SSE** (ver contrato en §9).
3. **Poder invocarse como comando interactivo** (`docker exec -it ... <comando>`) para la terminal PTY.

Los lugares del código donde el WebUI acopla el agente son **pocos y explícitos**:

| Lugar | Qué cambia | Dónde |
|---|---|---|
| `HERMES_IMAGE` | Imagen Docker del agente | `docker-compose.yml`, `config.py` |
| Comando de arranque del contenedor | `gateway run --no-supervise` | `hermes_service._create_container` |
| `agent_command` | Comando que abre la terminal TUI | campo por proyecto en DB |
| Endpoints del agente | `/v1/responses`, `/v1/models`, `/health` | `chat_service.py` |
| Variables de entorno del contenedor | `API_SERVER_*`, `HERMES_UID/GID` | `hermes_service._create_container` |

Si el agente nuevo expone un contrato distinto, se ajustan esas cinco cosas. **El resto del WebUI no se toca.**

---

## 3. Stack tecnológico

- **Frontend:** SvelteKit (Svelte 5 con runes) + Tailwind CSS v4, compilado como SPA con `adapter-static` → salida en `static/`.
- **Backend:** FastAPI (Python 3.13), async SQLAlchemy + SQLite (aiosqlite).
- **Terminal:** xterm.js (frontend) + WebSocket + `ptyprocess` (backend) → `docker exec` al contenedor del agente.
- **Auth:** Google OAuth 2.0 → JWT en cookies HttpOnly.
- **Orquestación de contenedores:** el WebUI usa el CLI de Docker (`docker run`, `docker exec`, `docker rm`) vía `subprocess`. No usa la API de Docker ni un SDK; habla con el Docker socket montado en el contenedor del WebUI.

> **Decisión importante:** el WebUI maneja contenedores llamando al binario `docker`. Por eso en el `Dockerfile` del WebUI se instala `docker-cli` y se monta `/var/run/docker.sock`. El WebUI es, en efecto, un cliente Docker que crea contenedores "hermanos" en el mismo host.

---

## 4. Arquitectura de contenedores (LO CRÍTICO)

### 4.1 Dos imágenes Docker distintas

**Imagen del WebUI** (`Dockerfile` en la raíz):
- Multi-stage: Stage 1 construye el frontend SvelteKit (`node:22-slim`); Stage 2 es runtime Python (`python:3.13-slim`).
- Instala `docker-cli` para poder crear contenedores del agente.
- Crea un usuario `appuser` con UID/GID 1000 y lo añade al grupo `docker` (GID configurable vía `DOCKER_GID`) para que pueda hablar con el Docker socket.
- Runtime: `uvicorn backend.main:app --host 0.0.0.0 --port 8080`.

**Imagen del agente** (`hermes-gateway/Dockerfile.agent`, solo 2 líneas en este proyecto):
```dockerfile
FROM nousresearch/hermes-agent:latest
RUN npx agent-browser install
```
Para el proyecto nuevo, esto se reemplaza por la imagen del agente propio. El único requisito: debe exponer HTTP en un puerto conocido y aceptar el comando de arranque que el WebUI le pase en `docker run`.

### 4.2 docker-compose.yml del WebUI

```yaml
services:
  hermes-webui:
    build:
      context: .
      dockerfile: Dockerfile
      args:
        DOCKER_GID: ${DOCKER_GID:-998}
    restart: unless-stopped
    network_mode: host          # ← clave: red host para alcanzar contenedores del agente por localhost:PUERTO
    environment:
      # ... auth, jwt, etc ...
      - HERMES_IMAGE=hermes-webui-agent
      - HERMES_BASE_PORT=8642
      - HOST_HOME=/home/claudio
      - WORKSPACES_ROOT=/workspaces           # como lo ve el WebUI (file ops)
      - WORKSPACES_HOST_ROOT=/workspaces      # como lo ve el host (para montar en el agente)
      - HERMES_IDLE_TIMEOUT=1800              # 30 min sin actividad → mata el contenedor
      - HERMES_IDLE_CHECK_INTERVAL=300
    volumes:
      - /workspaces:/workspaces                                              # workspaces de usuarios
      - ./data:/app/data                                                     # SQLite
      - /var/run/docker.sock:/var/run/docker.sock                            # ← el WebUI controla Docker
      - /home/claudio/.hermes:/home/claudio/.hermes                          # config del agente
      - /home/claudio/.hermes/hermes-agent:/opt/hermes/hermes-agent          # código del agente
```

**Por qué `network_mode: host`:** el WebUI levanta contenedores del agente en puertos dinámicos del host. Con red host, el WebUI los alcanza como `http://localhost:{puerto}`. Si usara red bridge, necesitaría descubrimiento de IPs o publicar puertos con mapeo complejo. Red host simplifica todo.

### 4.3 El servicio que orquesta los contenedores: `hermes_service.py`

Este es el corazón del sistema de contenedores. Funciones clave:

#### Registro en disco (`data/container_registry.json`)
El WebUI mantiene un JSON con `{profile: {port, container_id, last_activity}}` para no perder el estado entre reinicios. Al arrancar, llama a `_discover_containers()` que hace `docker ps -a --filter name=hermes-` y reconstruye el registro.

#### Resolución de URL perezosa (`get_hermes_url(profile)`)
```python
def get_hermes_url(profile: str) -> str:
    if profile not in _registry:
        _discover_containers()
    if profile not in _registry or not _container_exists(profile):
        provision_hermes(profile)          # ← auto-provisiona si no existe
    return f"http://localhost:{_registry[profile]['port']}"
```
**Patrón lazy:** el primer request del usuario (chat, terminal) dispara el provisioning automáticamente. No hay paso explícito de "crear contenedor".

#### Las tres ramas del provisioning (`_provision_hermes_inner`)
1. **Contenedor existe y está corriendo** + healthy → se reutiliza.
2. **Contenedor existe pero está parado** → `docker start`, espera a que esté healthy (timeout 60s).
3. **No hay contenedor** → `_create_container()` (fresh).

Si en cualquier rama falla el health check, se hace `docker rm -f` y se recrea.

#### Creación del contenedor (`_create_container`)
Este es el comando `docker run` que define el aislamiento. Lo importante para el proyecto nuevo:

```python
def _create_container(profile: str) -> int:
    name = _container_name(profile)                       # "hermes-{profile}"
    port = _port_for_profile(profile)                     # BASE_PORT + offset estable por perfil
    workspace_host = os.path.join(WORKSPACES_HOST_ROOT, profile)
    os.makedirs(workspace, exist_ok=True)

    cmd = [
        "docker", "run", "-d",
        "--name", name,
        "--restart", "unless-stopped",
        "-p", f"{port}:8642",                             # el puerto interno del agente (8642) → puerto del host
        # Mounts restrictivos: solo el perfil del usuario + config
        "-v", f"{HERMES_HOME}/.hermes/profiles/{profile}:/home/claudio/.hermes/profiles/{profile}",
        "-v", f"{HERMES_AGENT_DIR}:/opt/hermes/hermes-agent",
        "-v", f"{workspace_host}:/workspaces",            # ← el workspace del usuario se monta como /workspaces
        "-e", "HOME=/home/claudio",
        "-e", "API_SERVER_ENABLED=true",
        "-e", "API_SERVER_PORT=8642",
        "-e", f"API_SERVER_KEY={HERMES_API_KEY}",
        "-e", "API_SERVER_HOST=0.0.0.0",
        "-e", f"HERMES_UID={os.getuid()}",                # ← remap UID para que los volúmenes montados sean accesibles
        "-e", f"HERMES_GID={os.getgid()}",
        HERMES_IMAGE,                                      # imagen del agente
        "gateway", "run", "--no-supervise",                # comando de arranque del agente
    ]
    subprocess.run(cmd, ...)
    _registry[profile] = {"port": port, "container_id": ..., "last_activity": time.time()}
    _save_registry()
    return port
```

**Puntos críticos que el agente nuevo debe respetar:**

1. **Puerto interno del agente fijo (8642)** y mapeado a un puerto del host que depende del perfil. El WebUI siempre habla a `localhost:{port_del_host}`.
2. **Mount restrictivo:** solo el directorio del perfil del usuario entra al contenedor. **El agente NO ve los archivos de otros usuarios.**
3. **El workspace del usuario se monta como `/workspaces`** (root interior). El agente trata `/workspaces` como su límite superior.
4. **Remap de UID:** el contenedor del WebUI corre como UID 1000. Los volúmenes (`/workspaces`) están creados por el host con ese UID. El agente **debe** correr también como UID 1000 para poder leer/escribir esos volúmenes. En Hermes, esto se logra pasando `HERMES_UID`/`HERMES_GID` y un hook del base image hace `usermod`. Para el agente nuevo, asegurar lo mismo (sea por `usermod`, `--user`, o construir la imagen con el UID correcto).
5. **Nombre estable:** `hermes-{profile}`. El `profile` viene del campo `hermes_profile` del usuario en DB (alfanumérico, 2-48 chars).

#### Asignación de puertos (`_port_for_profile`)
Cada perfil recibe un puerto estable derivado de su nombre (hash → offset desde `HERMES_BASE_PORT`). Así, reinicios no cambian el puerto y el registro en disco sigue siendo válido.

#### Limpieza por inactividad (`check_idle_containers`)
Un background task periódico recorre el registro y, si `last_activity` supera `HERMES_IDLE_TIMEOUT` (default 1800s), hace `docker rm -f` y elimina la entrada. Cada interacción (chat, terminal) llama a `touch_activity(profile)` para refrescar el timestamp.

> **Para el proyecto nuevo:** este patrón de "un contenedor por usuario + limpieza por idle" es lo que da la ilusión de "agente en la nube siempre disponible" sin pagar por contenedores idle infinitos. Esencial para escalar.

---

## 5. Frontend IDE

### 5.1 Layout

```
┌─────────────────────── MenuBar ──────────────────────────────────┐
│ FileExplorer │ Resize │  FileViewer (tabs) │ Resize │ Terminal   │
│   (280px)    │ Handle │   (flex-1)         │ Handle │ (33%→px)  │
├─────────────────────── StatusBar ────────────────────────────────┤
```

- **MenuBar** — Selector de proyecto + usuario + logout.
- **FileExplorer** — Árbol de archivos con breadcrumb. Polling cada 5s para detectar archivos creados por el agente. Click en archivo → `openTab(path)`.
- **FileViewer** — Tabs con LRU eviction (máx 15). Markdown rendering (marked) + syntax highlight (Prism).
- **HermesTerminal** — xterm.js + WebSocket PTY. Se reconecta vía `{#key activeProjectId}` al cambiar de proyecto.
- **StatusBar** — Estado del terminal y proyecto activo.

Los paneles laterales son **redimensionables** vía drag handles (stores `explorerWidth` y `terminalWidth` con persistencia).

### 5.2 Estructura de rutas SvelteKit

- `+layout.svelte` — Auth check + provisioning no bloqueante del agente en background.
- `+page.svelte` — Vista principal IDE. Compone `MenuBar > [FileExplorer | FileViewer | HermesTerminal] > StatusBar`.
- `login/+page.svelte` — Login con Google.
- `register/+page.svelte` — Registro de perfil la primera vez (captura `hermes_profile`).

### 5.3 Stores (estado global)

> **Regla crítica de Svelte 5:** los stores en archivos `.ts` plano DEBEN usar `writable`/`derived` de `svelte/store`. Los runes (`$state`, `$derived`, `$effect`) SOLO funcionan en `.svelte` y `.svelte.ts`. El build NO avisa; falla en runtime.

| Store | Tipo | Responsabilidad |
|---|---|---|
| `auth.ts` | writable | `user`, `isAuthenticated`, `authLoaded` |
| `project.ts` | writable + localStorage | Proyecto activo, lista de proyectos |
| `workspace.ts` | writable | `currentPath`, `explorerWidth`, `terminalWidth` |
| `tabs.ts` | writable + derived | Tabs del FileViewer, `openTab`/`closeTab` con LRU eviction |
| `terminal.ts` | writable | Estado del WebSocket del terminal |
| `provisioning.ts` | writable | Estado del provisioning (`checking`/`provisioning`/`ready`/`failed`) |
| `chat.ts` | writable | Sesiones, mensajes, streaming state |

### 5.4 API client (`frontend/src/lib/api/`)

- `client.ts` — Wrapper de `fetch` con `credentials: 'include'` (cookies JWT).
- `auth.ts`, `workspaces.ts`, `chat.ts`, `terminal.ts` — Un archivo por dominio.

El chat streaming se consume con `fetch + ReadableStream` (NO `EventSource`, porque `EventSource` no soporta POST ni headers custom).

---

## 6. Backend FastAPI

### 6.1 Routers

| Router | Prefix | Responsabilidad |
|---|---|---|
| `auth.py` | `/api/auth/*` | Google OAuth, cookies JWT, dependencia `get_current_user` |
| `chat.py` | `/api/chat/*` | CRUD sesiones + relay SSE al agente |
| `workspaces.py` | `/api/workspaces/*` | CRUD workspaces + operaciones de archivos |
| `terminal.py` | `/api/terminal/ws` | WebSocket PTY al contenedor del agente |

> **Trampa conocida:** el router de chat usa `AsyncSessionLocal()` directamente (no `Depends` de FastAPI) por un conflicto al combinarlo con `Depends(get_current_user)`. Mantener este patrón.

### 6.2 Servicios

| Servicio | Rol |
|---|---|
| `hermes_service.py` | **Ciclo de vida de contenedores del agente.** El más importante para este documento (§4.3). |
| `chat_service.py` | Relay SSE hacia el contenedor del agente. |
| `file_service.py` | Operaciones de archivos con `safe_path()` contra path traversal. |
| `agent.py` | Carga configuración de modelos del agente. |
| `stream_manager.py` | **Código muerto.** No tocar. |

### 6.3 Modelos SQLAlchemy

`User` (con `hermes_profile`), `Workspace`, `ChatSession`, `ChatMessage`. SQLite vía aiosqlite, engine async.

### 6.4 Configuración (`backend/config.py`)

`pydantic-settings` cargando desde `.env`. Variables clave para el sistema de contenedores:

```python
class Settings(BaseSettings):
    hermes_image: str            # nombre de la imagen del agente
    hermes_base_port: int = 8642 # puerto inicial para contenedores
    workspaces_root: str         # path dentro del contenedor WebUI (file ops)
    workspaces_host_root: str    # path como lo ve el host (para montar en el agente)
    hermes_idle_timeout: int = 1800
    hermes_idle_check_interval: int = 300
    hermes_api_key: str          # API_SERVER_KEY que se pasa al agente
```

---

## 7. Flujo: provisioning no bloqueante

1. Usuario carga la SPA → `+layout.svelte` llama a `getMe()` para chequear auth.
2. Si autenticado, dispara `waitForHermesReady()` que hace polling a `/api/chat/health`.
3. `/api/chat/health` → resuelve el gateway con `get_hermes_url(profile)` → **auto-provisiona** el contenedor si no existe (§4.3).
4. El layout IDE (`+page.svelte`) **NO espera** el provisioning: muestra el explorer y el viewer inmediatamente. Solo la terminal y el chat dependen del contenedor del agente.
5. Cuando el health responde OK, el store `provisioning` pasa a `ready` y se habilitan chat + terminal.

> **UX clave:** el usuario ve la interfaz al instante. El contenedor del agente arranca en paralelo. Si el usuario abre la terminal antes de que esté listo, la terminal misma dispara el provisioning al conectar el WebSocket.

---

## 8. Flujo: Terminal PTY (WebSocket → docker exec)

```
Browser (xterm.js) ──WebSocket──► /api/terminal/ws?project_id=...
                                         │
                                         ▼
                          terminal.py::terminal_ws
                                         │
                          1. resolve user + project
                          2. provision_hermes(profile) si no corre
                          3. _spawn_exec_pty():
                                 docker exec -it \
                                   -w /workspaces/{project.path} \
                                   -e TERM=xterm-256color \
                                   hermes-{profile} \
                                   {agent_command}        # ej: "hermes --tui"
                          4. ptyprocess.PtyProcess.spawn(docker_cmd)
                                         │
              ┌──────────────────────────┴───────────────────────────┐
              ▼                                                       ▼
   pump_pty_to_ws(): lee PTY → ws.send_bytes()            recv loop: ws → _write_pty()
   ping_loop(): keepalive cada N segundos                  resize: regex match → _resize_pty
```

**Detalles importantes:**

- **`agent_command` es por proyecto** (campo en DB). Permite que cada proyecto abra la terminal con un comando distinto (ej: `hermes --tui` vs `hermes` en modo CLI).
- **Resize del terminal:** el frontend envía bytes con un marker tipo `\x1b[8;{rows};{cols}t` que el backend parsea con regex y redimensiona el PTY.
- **Keepalive ping/pong binario:** el backend envía `\x00ping`, el frontend responde `\x00pong`. El frontend intercepta estos markers con `TextDecoder` para no escribirlos en xterm.
- **Throttle de `touch_activity`:** cada N segundos de actividad en la terminal refresca `last_activity` para que el contenedor no sea limpiado por idle.
- **Reconexión al cambiar de proyecto:** `{#key activeProjectId}` en `+page.svelte` destruye y recrea el componente del terminal, posicionándolo en el proyecto correcto.

---

## 9. Contrato HTTP/SSE que el agente debe cumplir

Para que el WebUI funcione con un agente nuevo, este debe exponer:

### 9.1 `POST /v1/responses` (streaming SSE)

Request body:
```json
{
  "input": "mensaje del usuario",
  "conversation": "nombre-unico-de-sesion",
  "store": true,
  "stream": true,
  "model": "opcional",
  "instructions": "texto del system prompt",
  "conversation_history": [...]  // opcional, para migrar contexto
}
```

Response: stream SSE con eventos `event: <tipo>\ndata: <json>\n\n`. El WebUI relayea todos los eventos tal cual al frontend. Eventos terminales: `response.completed`, `response.failed`.

Header de auth: `Authorization: Bearer {API_SERVER_KEY}` (configurable).

### 9.2 `GET /v1/models`

Devuelve la lista de modelos disponibles. El WebUI lo usa para el selector de modelo del chat.

### 9.3 `GET /health`

Health check. El WebUI lo consulta durante el provisioning para saber cuándo el agente está listo (timeout 60s).

### 9.4 Comando de terminal

El agente debe tener un comando ejecutable interactivo (TUI o REPL) invocable como `docker exec -it ... <comando>`. En Hermes es `hermes` o `hermes --tui`.

> **Si tu agente no cumple este contrato exacto**, se ajustan `chat_service.py` (endpoints), `hermes_service._create_container` (comando de arranque), y `agent_command` (comando de terminal). El resto del WebUI permanece intacto.

---

## 10. Flujo: Chat relay (SSE)

```
Frontend (fetch + ReadableStream)
    │ POST /api/chat/sessions/{id}/messages
    ▼
chat.py::send_message
    │ 1. resolve gateway_url = asyncio.to_thread(resolve_gateway_url, profile)
    │ 2. build_instructions(workspace_path)   ← system prompt que fija el cwd y el límite /workspaces
    │ 3. event_generator() →
    ▼
chat_service.stream_message(gateway_url, conversation, message, instructions, model, history)
    │ 4. POST {gateway_url}/v1/responses (httpx.AsyncClient, stream=True, timeout 300s)
    │ 5. relayea cada línea SSE al frontend
    │ 6. acumula texto + usage para guardar en DB al final
    ▼
Frontend parsea eventos SSE
```

**System prompt que aísla al agente** (`build_instructions`):
```
DIRECTORIO DE TRABAJO ACTUAL: /workspaces/{proyecto}
- Usa rutas relativas desde ese directorio.
LÍMITE DE ACCESO (OBLIGATORIO):
Tu directorio raíz asignado es: /workspaces
NO puedes acceder a NADA por encima de /workspaces.
- NUNCA uses 'cd ..' para navegar por encima.
- NUNCA ejecutes sudo, su, o comandos de privilegios.
- NUNCA uses rm -rf fuera de /workspaces.
- Si el usuario pide algo fuera de /workspaces, REHUSA.
```

> Esta defensa es **defense in depth**: el montaje restrictivo del contenedor ya impide el acceso físico a otros perfiles; las instrucciones refuerzan el límite semántico para que el agente no intente escapar.

### 10.1 Traducción de paths

El contenedor del agente monta `/workspaces/{profile}` como `/workspaces/`. El WebUI debe traducir los paths entre la vista del host y la vista del agente:

- Host: `/workspaces/{profile}/noticias`
- Agente: `/workspaces/noticias`

`_translate_path()` en `chat.py` strippea el prefijo del host. Las instrucciones le dicen al agente que su cwd es `/workspaces/{proyecto}` (sin el perfil).

### 10.2 Concurrencia

`_active_streams` (set de session IDs en memoria) previene que dos mensajes simultáneos se envíen al agente para la misma sesión. Si llega uno mientras hay un stream activo → HTTP 409.

### 10.3 Contexto de conversación

El agente maneja el historial internamente vía el parámetro `conversation` (ID único por sesión). En el **primer mensaje** de una sesión existente, el WebUI envía el historial de DB como `conversation_history` para migrar contexto. Mensajes subsecuentes usan el estado interno del agente (`store: true`).

---

## 11. Auth: Google OAuth → JWT

1. Login con Google → callback.
2. Si usuario nuevo → redirige a `/register` para capturar `hermes_profile` (alfanumérico, 2-48 chars). Ese campo determina el nombre del directorio del workspace del usuario y el nombre del contenedor.
3. Se setea cookie JWT HttpOnly.
4. Redirige a `/`.

La dependencia `get_current_user` decodifica el JWT y carga el usuario desde DB. El `hermes_profile` del usuario viaja con cada request y se usa para resolver el contenedor.

> **Para el proyecto nuevo:** si no necesitas Google OAuth, se puede reemplazar por cualquier flujo que termine en un usuario con un identificador estable de "perfil" (ese ID es la clave del contenedor). Lo importante es que el `profile` sea único, alfanumérico y acotado en longitud.

---

## 12. Plan para replicar el sistema (proyecto nuevo)

Orden sugerido para construir un sistema equivalente con tu propio agente:

1. **Definir el contrato del agente.** ¿Qué endpoints HTTP expone? ¿Qué comando abre su TUI? ¿En qué puerto interno corre? (§9)
2. **Construir la imagen del agente.** Debe correr como UID 1000 (o el que uses), exponer HTTP en un puerto fijo, y tener el comando interactivo disponible en el PATH.
3. **Levantar el WebUI.** Réplica el `Dockerfile` multi-stage (frontend build + Python runtime + docker-cli + grupo docker) y el `docker-compose.yml` con `network_mode: host` y el Docker socket montado.
4. **Implementar el orquestador de contenedores.** Puerto al `hermes_service.py`: registro en disco, `get_hermes_url` lazy, las tres ramas de provisioning, limpieza por idle. Ajusta los mounts y variables de entorno al contrato de tu agente.
5. **Implementar el relay del chat.** Puerto al `chat_service.py`: SSE relay + `build_instructions` (system prompt de aislamiento).
6. **Implementar la terminal PTY.** Puerto al `terminal.py`: WebSocket + `ptyprocess` + `docker exec -it`.
7. **Construir el frontend IDE.** Layout de tres paneles (explorer | viewer | terminal) con resize handles, tabs con LRU, xterm.js. Stores en `.ts` con `writable`/`derived`.
8. **Auth.** Google OAuth o equivalente. Asegura un `profile` estable por usuario.
9. **Probar el aislamiento.** Verifica que un usuario no pueda ver los archivos de otro (montaje restrictivo) ni escapar de `/workspaces` (instrucciones + el agente corre sin privilegios).

---

## 13. Decisiones de diseño y trampas conocidas

### 13.1 Por qué `network_mode: host`
Simplifica el descubrimiento de contenedores del agente. El WebUI siempre habla a `localhost:{port}`. Alternativa (bridge + publicación de puertos) requiere mapeo manual o discovery service.

### 13.2 Por qué CLI de Docker y no la API
Menos dependencias, más portátil. El único costo es parsear la salida de `docker ps`. Para escala mayor, migrar a la API de Docker o a un orquestador (k8s, Nomad).

### 13.3 Por qué un contenedor POR USUARIO y no uno compartido
Aislamiento total: el agente de un usuario no puede interferir con el de otro. Cada uno tiene su filesystem, su proceso, sus variables. Costo: más contenedores corriendo → mitigado con la limpieza por idle.

### 13.4 Remap de UID (trampa crítica)
El base image del agente puede correr como un UID distinto al del WebUI. Si los UIDs no coinciden, los volúmenes montados (`/workspaces`) tendrán problemas de permisos. Solución en Hermes: pasar `HERMES_UID`/`HERMES_GID` y un hook del base image hace `usermod`. **Para el agente nuevo, verifica esto desde el día 1.**

### 13.5 Stores `.ts` vs runes
Regla inquebrantable de Svelte 5: `.ts` plano → `writable`/`derived`. Runes solo en `.svelte`/`.svelte.ts`. El build no avisa; falla en runtime de formas confusas.

### 13.6 Sessions de DB en el router de chat
Usar `AsyncSessionLocal()` directamente, no `Depends`. Hay un conflicto conocido con `Depends(get_current_user)`.

### 13.7 SSE en el frontend
Usar `fetch + ReadableStream`, no `EventSource`. `EventSource` no soporta POST ni headers custom (necesarios para cookies y body).

### 13.8 Deploy: reconstruir siempre
Los archivos del backend y frontend se copian DENTRO de la imagen Docker durante el build. Un `docker restart` **NO** aplica cambios de código. Siempre `docker compose up --build -d`. La única excepción: copiar `static/_app/` directamente si solo cambió el frontend (sin cambios de backend).

---

## 14. Resumen ejecutivo para el agente nuevo

- **El WebUI es un relay.** No corre lógica del agente.
- **Un contenedor por usuario**, nombrado `{prefijo}-{profile}`, puerto estable por perfil, limpieza por idle.
- **El agente expone HTTP/SSE + un comando de terminal.** Ese es el único contrato.
- **Aislamiento doble:** montaje restrictivo (un usuario no ve archivos de otro) + system prompt que fija el cwd en `/workspaces/{proyecto}` y prohíbe escapar de `/workspaces`.
- **Provisioning perezoso:** el primer request del usuario crea el contenedor. No hay paso explícito.
- **Frontend IDE:** tres paneles redimensionables, tabs con LRU, terminal xterm.js con WebSocket PTY.
- **Para reemplazar Hermes por tu agente:** toca solo `HERMES_IMAGE`, el comando de arranque en `_create_container`, `agent_command`, los endpoints en `chat_service.py`, y las variables de entorno del contenedor. El resto del WebUI no se mueve.
