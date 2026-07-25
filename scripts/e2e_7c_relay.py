"""Paso 7c manual e2e — SSE relay refactor validation.

Runs the requirements-capture subagent in-process against the test1 workspace
and reproduces the chat.py event_stream chunk parser to verify:
  - /captura → directive rewrite (_rewrite_command)
  - subagent delegation (DeepAgents subagent dispatch via namespaces)
  - run_requirements_capture tool invocation (tool_start / tool_end)
  - extraction.progress custom events (get_stream_writer → astream custom mode)
  - token deltas from the main agent (AIMessageChunk text → SSE token)
  - RequirementItem persistence (DB count before / after)

Bypasses HTTP/auth — the risk under test is the chunk parser + delegation +
custom events, NOT the HTTP layer (which already worked pre-7c).

Run:  .venv/bin/python scripts/e2e_7c_relay.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from collections import Counter

# Ensure repo root is importable when run as `python scripts/e2e_7c_relay.py`.
sys.path.insert(0, ".")

from sqlalchemy import text  # noqa: E402

from backend.database import AsyncSessionLocal, engine  # noqa: E402
from backend.models import Base  # noqa: E402  registers RequirementItem on metadata
from backend.routers.chat import (  # noqa: E402
    _rewrite_command,
    _safe_json,
    _sse,
    _stringify,
    _truncate,
)
from backend.services.agent_service import build_agent  # noqa: E402
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage  # noqa: E402

PROJECT_ID = 2
PROFILE = "perfil_claudio"
SLUG = "test1"
THREAD_ID = "e2e-7c-relay-1"
RECURSION_LIMIT = 100
RUN_TIMEOUT_S = 1200  # 20 min cap — critique batch es lento y varía (859s..>900s)


async def _ensure_tables() -> None:
    """Create any missing tables (idempotent). The dev DB predates the
    requirement models; create_all brings it up without a full migration."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _requirement_count() -> int:
    async with AsyncSessionLocal() as s:
        r = await s.execute(
            text("SELECT COUNT(*) FROM requirement_items WHERE project_id = :pid"),
            {"pid": PROJECT_ID},
        )
        return r.scalar_one()


async def main() -> int:
    await _ensure_tables()
    before = await _requirement_count()
    print(f"[db] requirement_items project_id={PROJECT_ID} BEFORE: {before}")

    agent = build_agent(
        profile=PROFILE,
        project_slug=SLUG,
        project_name=SLUG,
        project_description="",
        project_id=PROJECT_ID,
        thread_id=THREAD_ID,
        phase="requirements",
        checkpointer=None,
    )

    content = _rewrite_command("/captura")
    print("=" * 72)
    print("DIRECTIVE (rewritten /captura):")
    print(content)
    print("=" * 72)

    config = {
        "configurable": {"thread_id": THREAD_ID},
        "recursion_limit": RECURSION_LIMIT,
    }

    kinds: Counter = Counter()
    msg_types: Counter = Counter()  # (ns_key, msg_type) → count
    namespaces: set[str] = set()
    progress_events: list[dict] = []
    tool_starts: list[str] = []
    tool_ends: list[tuple[str, str]] = []  # (name, truncated_output)
    token_count = 0
    sample_tokens: list[str] = []
    # Diagnostic: raw shape of first few AIMessageChunks in the subagent ns,
    # to debug why tool_call_chunks were/ weren't caught.
    raw_chunk_dump: list[dict] = []
    seen_tc: set[str] = set()

    async def _consume() -> None:
        nonlocal token_count  # += rebinds; other counters mutate containers
        async for chunk in agent.astream(
            {"messages": [{"role": "user", "content": content}]},
            stream_mode=["messages", "custom"],
            subgraphs=True,
            version="v2",
            config=config,
        ):
            ctype = chunk.get("type")
            cdata = chunk.get("data")
            ns = chunk.get("ns") or ()
            # ns is a tuple of namespace strings; collapse to roots for readability.
            ns_key = "|".join(str(x).split(":")[0] for x in ns) or "(root)"
            namespaces.add(ns_key)
            kinds[(ctype, ns_key)] += 1

            # --- reproduce chat.event_stream chunk handling ---
            if ctype == "messages":
                if not isinstance(cdata, tuple) or len(cdata) != 2:
                    continue
                token, _meta = cdata
                msg_type = getattr(token, "type", "")
                msg_types[(ns_key, msg_type)] += 1
                if isinstance(token, (AIMessage, AIMessageChunk)):
                    tcc = getattr(token, "tool_call_chunks", None)
                    tc = getattr(token, "tool_calls", None)
                    calls = tcc or tc
                    text_payload = getattr(token, "content", "")
                    # Dump first few subagent chunks for parser debugging.
                    if ns_key != "(root)" and len(raw_chunk_dump) < 8:
                        raw_chunk_dump.append({
                            "ns": ns_key,
                            "msg_type": msg_type,
                            "has_calls": bool(calls),
                            "calls": (str(calls)[:200] if calls else None),
                            "content_type": type(text_payload).__name__,
                            "content_snippet": (
                                str(text_payload)[:120]
                                if not isinstance(text_payload, list)
                                else f"<list len={len(text_payload)}>"
                            ),
                        })
                    if isinstance(text_payload, str) and text_payload and not calls:
                        token_count += 1
                        if len(sample_tokens) < 12:
                            sample_tokens.append(text_payload[:80])
                        # frame generated (not stored to keep output lean)
                        _sse("token", {"delta": text_payload})
                    if calls:
                        for c in calls:
                            if not isinstance(c, dict):
                                continue
                            c_name = c.get("name")
                            c_id = c.get("id") or c_name
                            if c_name and c_id not in seen_tc:
                                seen_tc.add(c_id)
                                tool_starts.append(c_name)
                                _sse(
                                    "tool_start",
                                    {"name": c_name, "input": _safe_json(c.get("args"))},
                                )
                elif isinstance(token, ToolMessage):
                    name = getattr(token, "name", "") or "tool"
                    out = _truncate(_stringify(getattr(token, "content", "")))
                    tool_ends.append((name, out))
                    _sse("tool_end", {"name": name, "output": out})
            elif ctype == "custom":
                if isinstance(cdata, dict):
                    ev_name = cdata.get("event") or "custom"
                    ev_data = cdata.get("data", {})
                    progress_events.append({"event": ev_name, "data": ev_data})
                    _sse(ev_name, _safe_json(ev_data))

    t0 = time.monotonic()
    timed_out = False
    try:
        await asyncio.wait_for(_consume(), timeout=RUN_TIMEOUT_S)
    except asyncio.TimeoutError:
        timed_out = True
    elapsed = time.monotonic() - t0

    after = await _requirement_count()

    # ---- report ----
    print()
    print("=" * 72)
    print("CHUNK KINDS (stream_mode type | namespace roots → count):")
    for (ctype, ns_key), n in sorted(kinds.items()):
        print(f"  {ctype:<10} | {ns_key:<32} → {n}")
    print()
    print(f"NAMESPACES seen: {sorted(namespaces)}")
    print(f"msg_types (ns | type → count):")
    for (ns_key, mt), n in sorted(msg_types.items()):
        print(f"  {ns_key:<16} | {mt:<20} → {n}")
    if raw_chunk_dump:
        print("raw subagent AIMessageChunk samples (parser debugging):")
        for d in raw_chunk_dump:
            print(f"  · ns={d['ns']} type={d['msg_type']} has_calls={d['has_calls']} "
                  f"content={d['content_type']}: {d['content_snippet']!r}")
            if d["calls"]:
                print(f"      calls: {d['calls']}")
    print(f"tool_starts ({len(tool_starts)}): {tool_starts}")
    print(f"tool_ends   ({len(tool_ends)}):")
    for name, out in tool_ends:
        print(f"  · {name}: {out}")
    event_tally = Counter(p["event"] for p in progress_events)
    print(f"progress/custom events ({len(progress_events)}):")
    for ev_name, n in sorted(event_tally.items()):
        print(f"  · {ev_name}: {n}")
    # Detalle de eventos no-masivos (conflict.found, validation.report);
    # requirement.added y extraction.progress se muestran solo en el tally.
    for p in progress_events:
        if p["event"] not in ("extraction.progress", "requirement.added"):
            print(f"    [{p['event']}] {p['data']}")
    print()
    print(f"token chunks (AIMessageChunk text): {token_count}")
    if sample_tokens:
        print("  sample deltas:")
        for t in sample_tokens:
            print(f"    · {t!r}")
    print(f"wall clock: {elapsed:.1f}s" + ("  [TIMED OUT]" if timed_out else ""))
    print(f"[db] requirement_items project_id={PROJECT_ID} AFTER: {after} "
          f"(delta {after - before})")

    # ---- verdict ----
    print()
    print("=" * 72)
    print("VERDICT:")
    # Subagent namespace in DeepAgents is typically the subagent name slug.
    delegated = any(ns != "(root)" for ns in namespaces)
    capture_called = "run_requirements_capture" in tool_starts
    progress_ok = any(p["event"] == "extraction.progress" for p in progress_events)
    n_added = event_tally.get("requirement.added", 0)
    n_conflict = event_tally.get("conflict.found", 0)
    n_validation = event_tally.get("validation.report", 0)
    persisted = (after - before) > 0
    v_delegated = "PASS" if delegated else "FAIL"
    v_capture = "PASS" if capture_called else "FAIL"
    v_progress = "PASS" if progress_ok else "FAIL"
    v_added = "PASS" if n_added > 0 else "FAIL"
    v_validation = "PASS" if n_validation > 0 else "FAIL"
    v_persist = "PASS" if persisted else "FAIL"
    v_tokens = "PASS" if token_count > 0 else "FAIL"
    print(f"  subagent delegation   : {v_delegated}  (namespaces={sorted(namespaces)})")
    print(f"  run_requirements_capture : {v_capture}")
    print(f"  extraction.progress  : {v_progress}  ({event_tally.get('extraction.progress', 0)} events)")
    print(f"  requirement.added    : {v_added}  ({n_added} events)")
    print(f"  conflict.found       : INFO  ({n_conflict} events)")
    print(f"  validation.report    : {v_validation}  ({n_validation} events)")
    print(f"  RequirementItem persist : {v_persist}  (delta {after - before})")
    print(f"  token deltas         : {v_tokens}  ({token_count} chunks)")

    ok = (
        delegated and capture_called and progress_ok
        and n_added > 0 and n_validation > 0
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
