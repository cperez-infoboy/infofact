"""Application settings loaded from environment / .env.

Secrets are NOT placed inside the agent container; only the backend reads them.
"""
from __future__ import annotations

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


settings = Settings()
