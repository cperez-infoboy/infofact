# InfoFact — contexto específico de Claude Code

> El contexto completo del proyecto vive en `AGENTS.md` (fuente única, compartida con ZCode) y se importa abajo. Actualiza solo `AGENTS.md`; este archivo reserva únicamente lo específico de Claude Code.

@AGENTS.md

---

## MANDATORIO: MCP `docs-langchain` para cualquier duda de LangChain / DeepAgents

El server MCP **`docs-langchain`** (`https://docs.langchain.com/mcp`, HTTP) da acceso autoritativo a la documentación oficial de LangChain, LangGraph y **DeepAgents**. Antes de usar `WebFetch` / `WebSearch` o contestar de memoria sobre cualquiera de estos temas, **consultar este MCP primero**:

- APIs de `create_deep_agent`, `BaseSandbox`, backends, `astream_events`, subagents, checkpointer, middleware, permissions.
- Versiones y firmas exactas (los docs cambian; la memoria y el web pueden estar desactualizados).
- Comportamiento de sandbox backends (`execute()`, `upload_files`/`download_files`, `SandboxExecutionResult`).

**Verificación de carga por sesión:** `ListMcpResourcesTool` (sin `server`) debe listar `docs-langchain`. Si no aparece, el server se instaló después del arranque de la sesión → reiniciar Claude Code para activarlo. Mientras tanto, caer a `WebFetch` contra `https://docs.langchain.com/oss/python/deepagents/...` como fallback explícito.

## MCP `svelte` en Claude Code

El mandato del MCP `svelte` de `AGENTS.md` aplica igual acá, con una variante de verificación de carga: `ListMcpResourcesTool` (sin `server`) debe listar `svelte`. Si no aparece, el server se instaló después del arranque → reiniciar Claude Code para activarlo.
