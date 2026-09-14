"""Tools de curación del catálogo de actores (ProjectActor) para el agente.

El catálogo se descubre solo (etapa ``identify_actors`` de la captura), pero
el humano lo corrige por chat: «el actor correcto es X», «R3 no es un actor».
Estas tools dan esa vía: listar, agregar/consolidar, retirar (soft) y
consolidar semánticamente (agrupar granulares en un rol general). El
bloque PROJECT_ACTORS y los role_terms de los pre-checks leen SIEMPRE el
catálogo persistido, así que una corrección vale para esta captura y las
futuras. Espejo de ``project_rules_tools``.
"""
from __future__ import annotations

import logging

from langchain_core.tools import tool

from backend.agents.pipelines.extraction import (
    consolidate_actors,
    rewrite_actor_mentions,
)
from backend.database import AsyncSessionLocal
from backend.models.project_actor import ActorSource, ActorStatus
from backend.services import actor_store, requirement_store

logger = logging.getLogger(__name__)


def make_actor_tools(
    project_id: int,
    *,
    project_name: str = "",
    project_description: str = "",
) -> list:
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

    @tool
    async def consolidate_project_actors(instructions: str = "") -> dict:
        """Consolida SEMANTICAMENTE el catalogo activo de actores.

        Agrupa actores granulares (productos concretos) que convergen en un
        rol general: crea (o reusa) el actor general, absorbe los nombres de
        los miembros como sinonimos y los RETIRA (soft). Reescribe ademas los
        enunciados de requerimientos que citan los actores absorbidos para
        usar el rol general (cada cambio queda auditado en revisiones).
        Idempotente: sobre un catalogo ya general devuelve consolidated=False
        y no toca nada.

        Args:
            instructions: guia opcional del usuario (p. ej. 'agrupa los
                servicios de mapa').
        """
        try:
            return await consolidate_actor_catalog(
                project_id,
                project_name=project_name,
                project_description=project_description,
                instructions=instructions or "",
            )
        except Exception:
            logger.exception("consolidate_project_actors failed")
            return {
                "error": "consolidation_failed",
                "message": "La consolidacion fallo; el catalogo queda como "
                "estaba. Reintenta o revise los actores manualmente.",
            }

    return [
        list_project_actors,
        add_project_actors,
        retire_project_actor,
        consolidate_project_actors,
    ]


async def consolidate_actor_catalog(
    project_id: int,
    *,
    project_name: str = "",
    project_description: str = "",
    instructions: str = "",
) -> dict:
    """Consolida el catálogo activo y reescribe enunciados en UNA transacción.

    1) pasada LLM ``consolidate_actors`` sobre el catálogo ACTIVE;
    2) ``apply_actor_consolidation`` crea el rol general, absorbe sinónimos y
       retira los miembros (soft);
    3) ``rewrite_statements_for_actor_mapping`` reescribe los enunciados
       vivos que citan los términos absorbidos (con revisión por ítem).

    Degradación grácil: sin grupos propuestos (o LLM caído) no hay
    transacción que abrir y se devuelve ``consolidated`` False. El catálogo
    y la reescritura comparten sesión: si algo falla, nada queda a medias.
    """
    async with AsyncSessionLocal() as session:
        actors = await actor_store.list_actors(session, project_id)
        catalog = [actor_store.actor_to_dict(a) for a in actors]
        cons = await consolidate_actors(
            catalog,
            project_name=project_name,
            project_description=project_description,
            instructions=instructions,
        )
        groups = [g.model_dump() for g in cons.groups]
        if not groups:
            return {"consolidated": False, "groups": 0}
        applied = await actor_store.apply_actor_consolidation(
            session,
            project_id,
            groups,
            source=ActorSource.AGENT,
        )
        rewritten = await requirement_store.rewrite_statements_for_actor_mapping(
            session,
            project_id,
            applied["mapping"],
            rewrite_actor_mentions,
        )
        await session.commit()
    return {
        "consolidated": True,
        "groups": len(groups),
        "created": applied["created"],
        "retired": applied["retired"],
        "rewritten": rewritten,
        "actors": applied["actors"],
    }
