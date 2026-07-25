"""Smoke test for the requirements-capture subagent wiring.

Validates (without an LLM or the real pipeline):
- agent_service imports cleanly after the subagent wiring (no circular import,
  all edits syntactically valid).
- make_requirements_capture_subagent returns a DeepAgents-shaped dict with the
  capture tool + the 12 editing tools.
- _resolve_target accepts a valid subpath and rejects traversal / missing dirs.
- run_requirements_capture returns a compact report dict (pipeline mocked).

Run: .venv/bin/python scripts/smoke_requirements_capture.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from backend.agents.subagents import requirements_capture as rc
from backend.services.requirements_service import CaptureReport

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


async def main() -> None:
    print("== IMPORT: agent_service (no circular import) ==")
    try:
        from backend.services.agent_service import build_agent
        check("agent_service.build_agent imports", build_agent is not None)
    except Exception as exc:  # noqa: BLE001
        check("agent_service.build_agent imports", False, str(exc))
        print(f"\nRESULT: {PASS} passed, {FAIL} failed")
        sys.exit(1)

    print("\n== SUBAGENT factory ==")
    sub = rc.make_requirements_capture_subagent(
        project_id=1, profile="p", project_slug="s",
        project_name="N", project_description="D",
    )
    check("returns dict", isinstance(sub, dict))
    check("has DeepAgents keys",
          all(k in sub for k in ("name", "description", "system_prompt", "tools")))
    check("name is requirements-capture", sub.get("name") == "requirements-capture")
    check("description non-empty", len(sub.get("description", "")) > 20)
    check("system_prompt non-empty", len(sub.get("system_prompt", "")) > 50)
    check("13 tools (1 capture + 12 editing)", len(sub["tools"]) == 13,
          f"count={len(sub['tools'])}")
    check("capture tool is first",
          sub["tools"][0].name == "run_requirements_capture",
          f"first={sub['tools'][0].name}")

    print("\n== _resolve_target (traversal guard) ==")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "rfps").mkdir()
        (root / "rfps" / "a.pdf").write_text("x")

        valid = rc._resolve_target(root, "rfps")
        check("valid subpath resolved", valid == (root / "rfps").resolve(),
              f"{valid}")

        whole = rc._resolve_target(root, "")
        check("empty subpath -> workspace root", whole == root.resolve())

        try:
            rc._resolve_target(root, "../escape")
            check("traversal rejected", False, "no exception raised")
        except ValueError:
            check("traversal rejected", True)
        except Exception as exc:  # noqa: BLE001
            check("traversal rejected", False, f"wrong exc: {type(exc).__name__}")

        try:
            rc._resolve_target(root, "nope")
            check("missing target rejected", False, "no exception")
        except ValueError:
            check("missing target rejected", True)

        print("\n== run_requirements_capture (pipeline mocked) ==")
        fake_report = CaptureReport(
            documents=["rfps/a.pdf"],
            item_ids=[1, 2, 3],
            stats={
                "raw_extracted": 10,
                "after_consolidate": 5,
                "after_critique": 4,
                "sub_items": 1,
            },
            duplicates=["d1"],
            contradictions=["c1"],
            flagged=["f1"],
            rejected=[],
        )

        async def fake_pipeline(*args, **kwargs):
            return fake_report

        rc.run_requirements_pipeline = fake_pipeline  # patch module global

        capture_tool = rc._make_run_capture_tool(
            project_id=1, host_workspace=root,
            project_name="N", project_description="D",
        )
        result = await capture_tool.ainvoke({"target_subpath": "rfps"})
        check("returns persisted count", result.get("persisted") == 3,
              f"result={result}")
        check("returns compact stats",
              result.get("stats", {}).get("raw_extracted") == 10)
        check("returns duplicate/contradiction counts",
              result.get("duplicates_proposed") == 1
              and result.get("contradictions") == 1)
        check("lists documents", result.get("documents") == ["rfps/a.pdf"])

        # Error path: missing target subpath.
        err = await capture_tool.ainvoke({"target_subpath": "missing"})
        check("missing target -> error dict", "error" in err, f"result={err}")

    print(f"\n{'='*40}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
