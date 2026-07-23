"""Host the real Omnigent relay with one slow and one fast tool."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from omnigent import claude_native_bridge as bridge

SLOW_S = 20.0

TOOLS = [
    {
        "name": "sys_slow",
        "description": (
            "A relay tool that takes a long time (simulates a slow harness-side operation)."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "sys_fast",
        "description": "A relay tool that returns immediately.",
        "parameters": {"type": "object", "properties": {}},
    },
]


async def tool_executor(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    del arguments
    if name == "sys_slow":
        await asyncio.sleep(SLOW_S)
        return {"content": [{"type": "text", "text": f"slow call finished after {SLOW_S:.0f}s"}]}
    if name == "sys_fast":
        return {"content": [{"type": "text", "text": "fast call finished"}]}
    raise ValueError(f"unknown demo tool: {name}")


async def main() -> None:
    bridge_dir = bridge.prepare_acp_mcp_bridge_dir()
    loop = asyncio.get_running_loop()
    relay = bridge.start_tool_relay(
        bridge_dir=bridge_dir, tools=TOOLS, tool_executor=tool_executor, loop=loop
    )
    # Machine-readable startup handshake for demo_client.py. The client keeps
    # the private temporary path out of the public terminal recording.
    print(str(bridge_dir), flush=True)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        relay.close()


if __name__ == "__main__":
    if len(sys.argv) != 1:
        raise SystemExit("relay_host.py takes no arguments")
    asyncio.run(main())
