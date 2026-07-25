"""End-to-end pipeline test against the real RFP xlsx (steps 3-7 combined).

Runs the FULL capture pipeline via run_requirements_pipeline:
  ingest -> extract -> consolidate -> critique -> classify -> persist

and measures recall against the workbook's Descripción column (ground truth)
over the RequirementItem rows that SURVIVED consolidation + critique. The
earlier test_rfp_extraction.py measured extraction only; this one shows whether
dedup / critic / classify hurt recall and how they reshape the set.

Reports per-stage deltas (extracted -> after_consolidate -> after_critique ->
persisted), the classification distribution (type / priority / sub-items), and
dumps a human-readable markdown with type + priority per item.
"""
import asyncio
import logging
import sys
from pathlib import Path

# Surface the critique module's progress log ("critique progress: N/total") and
# retry warnings during the long LLM run. critique_all does not use on_progress;
# this is the only visibility into that stage.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))               # so test_rfp_extraction is importable
sys.path.insert(0, str(HERE.parent))        # so backend.* is importable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.models.project       # register tables on Base.metadata
import backend.models.requirement
from backend.agents.pipelines.consolidation import embed_texts
from backend.models.base import Base
from backend.models.project import Project
from backend.models.requirement import RequirementItem
from backend.services.requirements_service import run_requirements_pipeline

from test_rfp_extraction import (  # reuse GT loader + constants
    PATH, PROJECT_NAME, PROJECT_DESC, THRESH, load_ground_truth,
)


async def main():
    # --- in-memory DB + Project stub (SQLite FKs off by default) ---
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        proj = Project(
            user_id=1, name=PROJECT_NAME, slug="tcc-test",
            description=PROJECT_DESC, phase="requirements",
        )
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        project_id = proj.id

    # --- progress hook so the long LLM run is visible ---
    async def on_progress(stage, msg):
        print(f"  [{stage}] {msg}")

    print(f"running FULL pipeline on {PATH.name} (LLM real) ...")
    async with Session() as session:
        report = await run_requirements_pipeline(
            project_id, PATH,
            project_name=PROJECT_NAME,
            project_description=PROJECT_DESC,
            on_progress=on_progress,
            session=session,
        )

    st = report.stats
    print()
    print("=== STAGE DELTAS ===")
    print(f"  raw extracted:      {st['raw_extracted']}")
    print(f"  after consolidate:  {st['after_consolidate']}  "
          f"({st['consolidation'].get('duplicate_groups', 0)} dup groups, "
          f"{len(report.duplicates)} proposed merges)")
    print(f"  after critique:     {st['after_critique']}  "
          f"({len(report.rejected)} rejected, {len(report.flagged)} flagged)")
    print(f"  persisted (parents):{st['persisted']}  (+{st['sub_items']} derived sub-items)")
    print(f"  contradictions:     {len(report.contradictions)}")

    # --- read persisted rows + measure recall vs GT ---
    async with Session() as session:
        rows = (await session.execute(
            select(RequirementItem).where(
                RequirementItem.project_id == project_id,
                RequirementItem.derived.is_(False),
            )
        )).scalars().all()

    print(f"\n=== CLASSIFICATION ===")
    type_dist, prio_dist = {}, {}
    for r in rows:
        type_dist[r.type.value] = type_dist.get(r.type.value, 0) + 1
        prio_dist[r.priority.value] = prio_dist.get(r.priority.value, 0) + 1
    print(f"  type:     {type_dist}")
    print(f"  priority: {prio_dist}")

    print("\nloading ground truth + measuring recall ...")
    gt = load_ground_truth()
    print(f"  GT descriptions: {len(gt)}")

    gt_vecs = embed_texts(gt)
    ex_vecs = embed_texts([r.statement for r in rows]) if rows else None
    if ex_vecs is None:
        print("  NO items survived; recall=0")
        return
    sim = ex_vecs @ gt_vecs.T
    best_per_gt = sim.max(axis=0)
    matched = int((best_per_gt >= THRESH).sum())
    recall = matched / len(gt) if gt else 0.0
    best_per_ex = sim.max(axis=1)
    ex_with_gt = int((best_per_ex >= THRESH).sum())
    precision = ex_with_gt / len(rows) if rows else 0.0

    print()
    print("=== RECALL (over pipeline survivors) ===")
    print(f"  survivors:              {len(rows)}")
    print(f"  GT descriptions:        {len(gt)}")
    print(f"  recall@{THRESH}:          {matched}/{len(gt)} = {recall:.1%}")
    print(f"  precision@{THRESH}:       {ex_with_gt}/{len(rows)} = {precision:.1%}")

    misses = [gt[j] for j in range(len(gt)) if best_per_gt[j] < THRESH]
    print(f"  --- {len(misses)} GT misses (first 8) ---")
    for m in misses[:8]:
        print(f"    MISS: {m[:110]!r}")

    noise = [(r.code, r.statement, float(best_per_ex[k]))
             for k, r in enumerate(rows) if best_per_ex[k] < THRESH]
    print(f"  --- {len(noise)} survivors without GT match (first 8) ---")
    for code, stmt, score in noise[:8]:
        print(f"    NOISE(best={score:.2f}) {code}: {stmt[:80]!r}")

    _dump(rows, report, gt, best_per_ex)

    await engine.dispose()


def _dump(rows, report, gt, best_per_ex) -> None:
    """Human-readable markdown: every survivor with type + priority + verdict."""
    out = Path("docs/testing/pipeline_e2e_resultado.md")
    cons = report.stats["consolidation"]
    crit = report.stats["critique"]
    cls = report.stats["classification"]

    by_section: dict[str, list] = {}
    for k, r in enumerate(rows):
        section = (r.source or {}).get("section", "(sin sección)")
        by_section.setdefault(section, []).append((k, r))

    lines = [
        f"# Pipeline end-to-end — {PATH.name}",
        "",
        "Cadena completa: ingest → extract → consolidate → critique → classify → persist.",
        "",
        "## Deltas por etapa",
        "",
        f"- Extraídos (crudos): **{report.stats['raw_extracted']}**",
        f"- Tras consolidate: **{report.stats['after_consolidate']}** "
        f"({cons.get('duplicate_groups', 0)} grupos duplicados, "
        f"{len(report.duplicates)} fusiones propuestas)",
        f"- Tras critique: **{report.stats['after_critique']}** "
        f"({len(report.rejected)} rechazados por alucinación, "
        f"{len(report.flagged)} marcados)",
        f"- Persistidos (padres): **{report.stats['persisted']}** "
        f"+ **{report.stats['sub_items']}** sub-items derivados",
        f"- Contradicciones detectadas: **{len(report.contradictions)}**",
        "",
        "## Distribución de clasificación",
        "",
        f"- type: `{cls.get('type_dist')}`",
        f"- priority: `{cls.get('priority_dist')}`",
        f"- GT (col Descripción): **{len(gt)}**",
        "",
        "Leyenda: ✓ span verificado · ⚠ span NO verificado · NOISE = sin match contra GT.",
        "",
    ]
    for section in sorted(by_section):
        entries = by_section[section]
        lines.append(f"## {section}  ({len(entries)})")
        lines.append("")
        for n, (k, r) in enumerate(entries, 1):
            badge = "✓" if r.span_verified else "⚠"
            noise_tag = " · NOISE" if best_per_ex[k] < THRESH else ""
            src = r.source or {}
            page = f" p.{src.get('page')}" if src.get("page") else ""
            lines.append(
                f"### {badge} {r.code} · {r.type.value}/{r.priority.value}"
                f"{page}{noise_tag}"
            )
            lines.append(f"**Statement:** {r.statement}")
            lines.append("")
            lines.append(f"> **Source:** {src.get('quote', '')}")
            lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  dump: {out}")


if __name__ == "__main__":
    asyncio.run(main())
