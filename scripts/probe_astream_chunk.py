"""Fast probe: dump the actual class of AI messages yielded by astream v2.

The full e2e showed `ai`-type messages aren't caught by isinstance(AIMessageChunk).
This probe sends a trivial message and prints type()/isinstance() for each
message chunk, so we can see the real class and where tool_calls live.
~30s instead of a 12-min pipeline run.
"""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage  # noqa: E402

from backend.services.agent_service import build_agent  # noqa: E402


async def main() -> None:
    agent = build_agent(
        profile="perfil_claudio",
        project_slug="test1",
        project_name="test1",
        project_description="",
        project_id=2,
        thread_id="probe-astream-1",
        phase="requirements",
        checkpointer=None,
    )
    config = {"configurable": {"thread_id": "probe-astream-1"}, "recursion_limit": 25}

    n = 0
    async for chunk in agent.astream(
        {"messages": [{"role": "user", "content": "hola, contame en una linea que podes hacer"}]},
        stream_mode=["messages", "custom"],
        subgraphs=True,
        version="v2",
        config=config,
    ):
        if chunk.get("type") != "messages":
            continue
        cdata = chunk.get("data")
        if not isinstance(cdata, tuple) or len(cdata) != 2:
            continue
        token, _meta = cdata
        cls = f"{type(token).__module__}.{type(token).__name__}"
        is_ai = isinstance(token, AIMessage)
        is_aic = isinstance(token, AIMessageChunk)
        is_tm = isinstance(token, ToolMessage)
        ns = "|".join(str(x).split(":")[0] for x in (chunk.get("ns") or ())) or "(root)"
        content = getattr(token, "content", "")
        tcc = getattr(token, "tool_call_chunks", None)
        tc = getattr(token, "tool_calls", None)
        print(
            f"ns={ns:<12} class={cls:<45} AIMsg={is_ai!s:<5} AIChunk={is_aic!s:<5} "
            f"ToolMsg={is_tm!s:<5} tcc={bool(tcc)!s:<5} tc={bool(tc)!s:<5} "
            f"content={str(content)[:50]!r}"
        )
        if tc:
            print(f"    tool_calls: {tc}")
        n += 1
        if n >= 30:
            print("... (capped at 30 chunks)")
            break


if __name__ == "__main__":
    asyncio.run(main())
