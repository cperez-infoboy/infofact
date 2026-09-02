"""Tools de curación del catálogo de actores (ProjectActor) para el agente.

El catálogo se descubre solo (etapa ``identify_actors`` de la captura), pero
el humano lo corrige por chat: «el actor correcto es X», «R3 no es un actor».
Estas tools dan esa vía: listar, agregar/consolidar y retirar (soft). El
bloque PROJECT_ACTORS y los role_terms de los pre-checks leen SIEMPRE el
catálogo persistido, así que una corrección vale para esta captura y las
futuras. Espejo de ``project_rules_tools``.
"""
from __future__ import annotations

from langchain_core.tools import tool

from backend.database import AsyncSessionLocal
from backend.models.project_actor import ActorSource, ActorStatus
from backend.services import actor_store


def make_actor_tools(project_id: int) -> list:
    """Construye las tools de actores cerrando sobre project_id."""

    @tool
    async def list_project_actors() -> dict:
        """Lista el catálogo de actores del proyecto (código, rol, sinónimos).

        Incluye los retirados (marcados como tal) para que la curación vea el
        historial: re-agregar un rol retirado NO lo reactiva automáticamente.
        """
        async with AsyncSessionLocal() as session:
            actors = await actor_store.list_actors(
                session, project_id, include_retired=True
            )
        return {
            "actors": [actor_store.actor_to_dict(a) for a in actors],
            "count": len(actors),
        }

    @tool
    async def add_project_actors(actors: list[dict]) -> dict:
        """Agrega (o consolida) actores en el catálogo del proyecto.

        Idempotente: un actor cuyo nombre o sinónimo ya existe se CONSOLIDA
        (se suman sinónimos, se completa canal/rationale) en vez de duplicarse.
        El código R<n> lo asigna el store.

        Args:
            actors: lista de {name, synonyms?, channel?, rationale?}. name es
                el rol canónico en singular; channel: 'humano' |
                'sistema_externo'.
        """
        if not actors:
            return {"error": "empty", "message": "No hay actores para agregar."}
        async with AsyncSessionLocal() as session:
            res = await actor_store.upsert_actors(
                session,
                project_id,
                actors,
                source=ActorSource.AGENT,
            )
            await session.commit()
        return {
            "created": res["created"],
            "updated": res["updated"],
            "actors": res["actors"],
        }

    @tool
    async def retire_project_actor(code: str) -> dict:
        """Retira un actor del catálogo por su código (p. ej. 'R3'). Soft: la
        fila queda para auditoría y deja de alimentar el bloque PROJECT_ACTORS
        y los pre-checks de actor.

        Args:
            code: código del actor (R1, R2, ...).
        """
        wanted = (code or "").strip().upper()
        async with AsyncSessionLocal() as session:
            actors = await actor_store.list_actors(
                session, project_id, include_retired=True
            )
            target = next((a for a in actors if a.code.upper() == wanted), None)
            if target is None:
                return {
                    "error": "not_found",
                    "message": f"No existe un actor con código {code!r}.",
                }
            if target.status is ActorStatus.RETIRED:
                return {
                    "actor": actor_store.actor_to_dict(target),
                    "already_retired": True,
                }
            await actor_store.set_actor_status(
                session, project_id, target.id, ActorStatus.RETIRED
            )
            await session.commit()
            return {
                "actor": actor_store.actor_to_dict(target),
                "already_retired": False,
            }

    return [
        list_project_actors,
        add_project_actors,
        retire_project_actor,
    ]
