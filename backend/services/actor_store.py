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


async def apply_actor_consolidation(
    session,
    project_id: int,
    groups: list[dict[str, Any]],
    *,
    source: ActorSource = ActorSource.ACTORS_STAGE,
) -> dict[str, Any]:
    """Aplica grupos de consolidación: rol general absorbe a los miembros.

    ``groups``: [{general_name, channel?, rationale?, member_codes}] (salida
    ``model_dump`` de ``ActorGroup``). Por grupo: resuelve los códigos contra
    actores ACTIVE (desconocidos o retirados se saltan; menos de 2 válidos →
    grupo ignorado), crea o consolida el actor general con los nombres y
    sinónimos de los miembros como SINÓNIMOS, y retira (soft) los miembros —
    salvo el caso borde en que el nombre general matchea a un MIEMBRO: ese
    miembro se vuelve el general (no se retira) y absorbe a los demás.

    Flush sin commit (contrato del caller): así la consolidación del catálogo
    y la reescritura de enunciados comparten una transacción. Idempotente:
    re-aplicar no encuentra miembros activos y no hace nada. Devuelve
    ``{created, retired, mapping, actors}``: ``mapping`` lleva cada término
    retirado (nombre/sinónimo) al nombre del rol general, excluyendo los
    términos que otro actor activo reclama — es la entrada de la reescritura
    de enunciados.
    """
    existing = list(
        (await session.scalars(
            select(ProjectActor).where(ProjectActor.project_id == project_id)
        )).all()
    )
    by_code = {a.code.strip().upper(): a for a in existing if a.code}
    by_term: dict[str, ProjectActor] = {}
    for a in existing:
        by_term[_norm(a.name)] = a
        for syn in a.synonyms or []:
            by_term[_norm(syn)] = a

    created = 0
    retired: list[str] = []
    mapping: dict[str, str] = {}
    general_ids: set[int] = set()

    for g in groups:
        general_name = (g.get("general_name") or "").strip()
        if not general_name:
            continue
        members: list[ProjectActor] = []
        seen: set[int] = set()
        for raw in g.get("member_codes") or []:
            a = by_code.get((raw or "").strip().upper())
            # La comprobación de status va sobre la instancia viva: un actor
            # absorbido por un grupo anterior del mismo lote ya figura RETIRED.
            if a is None or a.id in seen or a.status is not ActorStatus.ACTIVE:
                continue
            members.append(a)
            seen.add(a.id)
        if len(members) < 2:
            continue

        # Sinónimos absorbidos: nombres + sinónimos de los miembros, sin
        # duplicados y sin el nombre canónico del general.
        synonyms: list[str] = []
        for m in members:
            for term in [m.name, *(m.synonyms or [])]:
                t = term.strip()
                if t and _norm(t) != _norm(general_name) and t not in synonyms:
                    synonyms.append(t)
        channel = (g.get("channel") or None) or None
        if not channel:
            channels = {m.channel for m in members if m.channel}
            channel = channels.pop() if len(channels) == 1 else None
        rationale = (g.get("rationale") or None) or (
            "Rol general que agrupa: " + ", ".join(m.name for m in members)
        )

        entry = {
            "name": general_name,
            "synonyms": synonyms,
            "channel": channel,
            "rationale": rationale,
        }
        target = by_term.get(_norm(general_name))
        if target is not None and target in members:
            # Caso borde: el general es uno de los miembros (identidad por
            # match de nombre/sinónimo). Se queda como general y absorbe.
            merged = {s for s in (target.synonyms or [])}
            merged.add(target.name)
            merged.update(synonyms)
            target.synonyms = sorted(merged - {target.name})
            if channel and not target.channel:
                target.channel = channel
            if not target.rationale:
                target.rationale = rationale
            await session.flush()
            general = target
            general_ids.add(general.id)
        elif target is not None:
            # El general ya existe y no es miembro: absorbe los sinónimos.
            # Sin upsert_actors: su matching POR SINÓNIMOS interpretaría los
            # nombres de los miembros como "este entry ya existe" y
            # consolidaría el general DENTRO de un miembro.
            merged = {s for s in (target.synonyms or [])}
            merged.add(target.name)
            merged.update(synonyms)
            target.synonyms = sorted(merged - {target.name})
            if channel and not target.channel:
                target.channel = channel
            if not target.rationale:
                target.rationale = rationale
            await session.flush()
            general = target
            general_ids.add(general.id)
        else:
            code = await next_actor_code(session, project_id)
            general = ProjectActor(
                project_id=project_id,
                code=code,
                name=general_name,
                synonyms=synonyms,
                channel=channel,
                rationale=rationale,
                source=source,
                status=ActorStatus.ACTIVE,
            )
            session.add(general)
            await session.flush()
            created += 1
            general_ids.add(general.id)
            by_code[code.strip().upper()] = general
            by_term[_norm(general_name)] = general
            for syn in synonyms:
                by_term.setdefault(_norm(syn), general)

        for m in members:
            if m is general or m.status is not ActorStatus.ACTIVE:
                continue
            await set_actor_status(
                session, project_id, m.id, ActorStatus.RETIRED
            )
            retired.append(m.code)
            mapping[m.name] = general.name
            for syn in m.synonyms or []:
                mapping[syn] = general.name

    # Los términos que OTRO actor ACTIVO sigue reclamando no van al mapping:
    # reescribirlos cambiaría enunciados ajenos a la consolidación. Los
    # términos que el propio general absorbió como sinónimos SÍ van: son
    # exactamente los que la reescritura debe reemplazar por el rol general.
    actors = await list_actors(session, project_id, include_retired=True)
    live_terms: set[str] = set()
    for a in actors:
        if a.status is ActorStatus.ACTIVE and a.id not in general_ids:
            live_terms.add(_norm(a.name))
            live_terms.update(_norm(s) for s in a.synonyms or [])
    mapping = {
        t: g for t, g in mapping.items() if _norm(t) not in live_terms
    }
    return {
        "created": created,
        "retired": retired,
        "mapping": mapping,
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
