# Plan: foto de sesión — persistir la línea de tiempo en vivo y redesplegarla

> Estado: IMPLEMENTADO (fases 1-4; limpieza fase 2 pendiente de período de
> validación). Verificado: 423 tests backend, build frontend, migración
> idempotente sobre tabla legacy.
> Fecha: 2026-08-19. Contexto: bugs de historial resueltos con heurísticas
> (commits posteriores a dfdc5c6); este plan los reemplaza por diseño.

## Objetivo

Al recargar una sesión, desplegar exactamente lo que el usuario vio en vivo,
leyéndolo de una fuente única ya persistida — sin reconstruir ni aparear.
Hoy el historial se arma desde tres fuentes con semánticas distintas
(checkpointer del agente, tabla `chat_messages`, rutas directas del router) y
cada bug corregido fue una heurística de sincronización entre ellas.

## Principio de diseño

- **`chat_messages` pasa a ser la línea de tiempo de despliegue**: se persiste
  cada elemento mientras se streamea (segmentos de texto del agente, eventos
  de tool), con los mismos truncados que aplica el relay.
- **El checkpointer queda como memoria del agente** (contexto del próximo
  turno), no como fuente de render. Dos responsabilidades distintas; dejar de
  forzar que coincidan.
- La reconstrucción actual (`reconstruct_history` + `_restore_user_content` +
  `_merge_direct_route_turns`) queda como **fallback legacy** para sesiones
  creadas antes de la migración.

## Cambios

### 1. Modelo (`backend/models/chat_message.py`)

Extender `ChatMessage` (misma tabla, sin tabla nueva):

| Columna | Tipo | Uso |
|---|---|---|
| `kind` | TEXT NULL, valores `'text' \| 'tool'` | Discriminador de fila; NULL = legacy |
| `tool_name` | TEXT NULL | Filas tool |
| `tool_args` | JSON NULL | Filas tool (input truncado como el relay) |
| `is_intermediate` | BOOLEAN NULL | Segmento assistant cerrado por tool_start (razonamiento) vs cerrado por completed (respuesta) |

`role` se mantiene (`user | assistant | tool`) para reutilizar el parser del
frontend. Migración según el mecanismo existente del repo.

### 2. Relay (`backend/routers/chat.py`, `event_stream`)

- Fila user: ya se persiste; setear `kind='text'`.
- Segmentos assistant: persistir **al cerrarse** (hoy solo se persiste el
  acumulado final):
  - cierre por `tool_start` → fila `role='assistant'`, `kind='text'`,
    `is_intermediate=True`, contenido = segmento tal como se streameó.
  - cierre por `completed` → fila final con `is_intermediate=False`
    (comportamiento actual, más el flag).
- Tool events: una fila por tool call — `role='tool'`, `kind='tool'`,
  `tool_name`, `tool_args` (truncado), contenido = output truncado
  (`_truncate`), actualizada/creada al `tool_end`. Marcar `running` no es
  necesario en la tabla: una fila tool sin output al recargar se muestra
  como interrumpida.
- Rutas directas (`/agrupar` puro) y gate de captura: ya persisten filas;
  setear `kind='text'` — el redespliegue las lee uniformemente.

### 3. Lectura (`backend/routers/projects.py`, `get_session_detail`)

- Si la sesión tiene filas con `kind` no-NULL → **redespliegue**: leer la
  tabla ordenada y devolverla tal cual (mapear a `MessageOut`).
- Si todas las filas son NULL-kind (sesión legacy) → ruta de reconstrucción
  actual (sin cambios; ya corregida y probada).
- Sesión híbrida (activa durante la migración): tratarla como legacy; caso
  raro y aceptable, documentado aquí.

### 4. Frontend

- `loadHistoryFromDetail` ya arma la timeline desde `MessageOut` con roles
  user/assistant/tool; con la tabla completa, la inferencia posicional de
  `isFinal` en `ChatPanel` se vuelve correcta **por construcción** (un
  assistant seguido de tool es intermedio porque así se persistió).
- Único ajuste fino: pasar `is_intermediate` en `MessageOut` y usarlo como
  override del inferido, para no depender del orden si algún día cambia el
  render.

### 5. Limpieza posterior (fase 2, opcional)

- Tras un período de validación: deprecar `reconstruct_history`,
  `_restore_user_content`, `_merge_direct_route_turns` y la heurística
  `/agrupar` documentada.

## Orden de implementación

1. Modelo + migración.
2. Relay escribe la línea de tiempo completa (con tests de persistencia por
   segmento/tool).
3. `get_session_detail`: redespliegue con detección legacy.
4. Frontend: override `is_intermediate` (opcional, puede seguir inferido).
5. Validación: sesión nueva end-to-end (turnos con tools, `/agrupar`, gate)
   → recargar y comparar 1:1 contra lo visto en vivo; sesión vieja (p. ej. la
   46) sigue saliendo por la ruta legacy correcta.

## Riesgos y mitigaciones

- **Storage**: outputs de tool crecen la tabla → persistir solo el output
  truncado que el relay ya envía (límite actual ~500 chars por tool).
- **Fila intermedia vacía tras limpieza**: segmentos cuyo texto limpio es
  vacío → no persistir (igual que hoy no se renderizan).
- **Turno abortado** (`failed`): cerrar y persistir el segmento parcial con
  `is_intermediate=True`; la recarga muestra fielmente el corte.
- **Fallos de persistencia**: nunca romper el stream (ya es el patrón: try/
  except con log, como la persistencia actual del assistant final).

## Fuera de alcance

- Cambiar el checkpointer o la memoria del agente.
- Retochar el historial de sesiones legacy (la ruta legacy las sirve tal cual).
