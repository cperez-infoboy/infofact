"""Store del catálogo de actores (ProjectActor): lectura, upsert y bloque.

Espejo de ``project_rules_store``: funciones async que toman AsyncSession +
project_id, hacen flush (el commit lo decide el caller, que suele abrir un
``AsyncSessionLocal`` fresco) y formatean el bloque ``PROJECT_ACTORS`` que se
inyecta en los USER messages de los pipelines de captura.

Deduplicación del upsert: un actor ya existe si su nombre normalizado o uno de
sus sinónimos coincide (case-insensitive) con el nombre o un sinónimo de un
actor del proyecto. El match no distingue activos de retirados (re-descubrir
un rol retirado NO lo reactiva: es una decisión humana).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from backend.models.project_actor import (
    ActorSource,
    ActorStatus,
    ProjectActor,
    actor_code_sort_key,
)


def _norm(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


async def list_actors(
    session,
    project_id: int,
    *,
    include_retired: bool = False,
) -> list[ProjectActor]:
    """Actores del proyecto, orden estable por código numérico (R1, R2, ...)."""
    q = select(ProjectActor).where(ProjectActor.project_id == project_id)
    if not include_retired:
        q = q.where(ProjectActor.status == ActorStatus.ACTIVE)
    rows = list((await session.scalars(q)).all())
    rows.sort(key=lambda a: actor_code_sort_key(a.code))
    return rows


async def _existing_codes(session, project_id: int) -> set[str]:
    rows = await session.execute(
        select(ProjectActor.code).where(ProjectActor.project_id == project_id)
    )
    return {c for (c,) in rows.all() if c}


async def next_actor_code(session, project_id: int) -> str:
    """Siguiente código R<n> libre del proyecto (max existente + 1)."""
    codes = await _existing_codes(session, project_id)
    n = max((actor_code_sort_key(c) for c in codes), default=0) + 1
    return f"R{n}"


def actor_to_dict(a: ProjectActor) -> dict[str, Any]:
    return {
        "id": a.id,
        "code": a.code,
        "name": a.name,
        "synonyms": list(a.synonyms or []),
        "channel": a.channel,
        "rationale": a.rationale,
        "source": a.source.value,
        "status": a.status.value,
    }


async def upsert_actors(
    session,
    project_id: int,
    entries: list[dict[str, Any]],
    *,
    source: ActorSource = ActorSource.ACTORS_STAGE,
) -> dict[str, Any]:
    """Upsert idempotente del catálogo a partir de entradas descubiertas.

    ``entries``: [{name, synonyms?, channel?, rationale?}]. Matchea por nombre
    o sinónimo normalizado contra TODO el catálogo (activo y retirado); el
    match consolida sinónimos y refresca canal/rationale, pero nunca cambia el
    status (retirar/reactivar es decisión humana). Los nuevos entran con el
    siguiente código R<n>. Flush sin commit (contrato del caller).
    """
    existing = list(
        (await session.scalars(
            select(ProjectActor).where(ProjectActor.project_id == project_id)
        )).all()
    )
    by_term: dict[str, ProjectActor] = {}
    for a in existing:
        by_term[_norm(a.name)] = a
        for syn in a.synonyms or []:
            by_term[_norm(syn)] = a

    created = 0
    updated = 0
    for entry in entries:
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        synonyms = [
            s.strip() for s in (entry.get("synonyms") or []) if s and s.strip()
        ]
        channel = (entry.get("channel") or None) or None
        rationale = (entry.get("rationale") or None) or None

        target = by_term.get(_norm(name))
        if target is None:
            for syn in synonyms:
                target = by_term.get(_norm(syn))
                if target is not None:
                    break
        if target is None:
            code = await next_actor_code(session, project_id)
            target = ProjectActor(
                project_id=project_id,
                code=code,
                name=name,
                synonyms=synonyms,
                channel=channel,
                rationale=rationale,
                source=source,
                status=ActorStatus.ACTIVE,
            )
            session.add(target)
            await session.flush()
            by_term[_norm(name)] = target
            for syn in synonyms:
                by_term[_norm(syn)] = target
            created += 1
        else:
            merged = {s for s in (target.synonyms or [])}
            merged.add(target.name)
            fresh = False
            for syn in synonyms:
                if _norm(syn) not in {_norm(m) for m in merged}:
                    merged.add(syn)
                    fresh = True
            if fresh:
                target.synonyms = sorted(merged - {target.name})
            if channel and not target.channel:
                target.channel = channel
            if rationale and not target.rationale:
                target.rationale = rationale
            for syn in list(target.synonyms or []):
                by_term[_norm(syn)] = target
            updated += 1

    actors = await list_actors(session, project_id, include_retired=True)
    return {
        "created": created,
        "updated": updated,
        "actors": [actor_to_dict(a) for a in actors],
    }


async def set_actor_status(
    session,
    project_id: int,
    actor_id: int,
    actor_status: ActorStatus,
) -> ProjectActor:
    """Retira o reactiva un actor (soft; nunca delete físico)."""
    actor = await session.get(ProjectActor, actor_id)
    if actor is None or actor.project_id != project_id:
        raise KeyError(f"actor {actor_id} no existe en el proyecto {project_id}")
    actor.status = actor_status
    await session.flush()
    return actor


ACTORS_BLOCK_HEADER = (
    "PROJECT_ACTORS (canonical actors of this system — functional requirement "
    "statements must start from these roles; never the generic 'usuario'/'user' "
    "for a role the catalog already names):"
)


def format_actors_block(actors: list[ProjectActor]) -> str:
    """Bloque ``PROJECT_ACTORS`` para inyectar; string vacío si no hay activos."""
    active = [a for a in actors if a.status is ActorStatus.ACTIVE and a.name]
    if not active:
        return ""
    lines = [ACTORS_BLOCK_HEADER]
    for a in active:
        line = f"- {a.code} {a.name}"
        if a.channel:
            line += f" ({a.channel})"
        syns = [s for s in (a.synonyms or []) if s]
        if syns:
            line += f" [aka: {', '.join(syns)}]"
        lines.append(line)
    return "\n".join(lines)


async def actors_block(session, project_id: int) -> str:
    """Bloque ``PROJECT_ACTORS`` listo para inyectar (vacío si no hay catálogo)."""
    actors = await list_actors(session, project_id)
    return format_actors_block(actors)


async def actor_role_terms(session, project_id: int) -> list[str]:
    """Nombres + sinónimos (en minúsculas) para ``detect_actor(role_terms=...)``.

    Con el catálogo, ``smell.actor_missing``/``actor.generic_user`` reconocen
    «Coordinador de terreno» como actor nombrado aunque el léxico base no lo
    traiga; sin catálogo devuelve lista vacía (léxico base only).
    """
    actors = await list_actors(session, project_id)
    terms: list[str] = []
    for a in actors:
        if a.name:
            terms.append(a.name.lower())
        terms.extend(s.lower() for s in (a.synonyms or []) if s)
    # Los términos largos primero: el match por palabra completa de
    # detect_actor es insensible al orden, pero así el diagnóstico es estable.
    terms.sort(key=len, reverse=True)
    return terms
