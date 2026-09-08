#!/usr/bin/env python3
"""fake_mcp_server.py — fake Cua MCP server for portable tests.

Test fixture ONLY. Speaks newline-delimited JSON-RPC over stdio exactly like
`cua-driver mcp` would, with a REAL initialization lifecycle state machine:

    initialize request        -> initialize result (server picks a version)
    notifications/initialized -> normal operation allowed

Calls issued before the lifecycle completes are rejected with -32002,
mirroring the MCP 2025-06-18 initialization requirements.

Injectable failure modes via the FAKE_MODE environment variable
(design doc §40):

    ok                valid handshake, tools/list, tools/call (default)
    legacy            only supports protocol 2024-11-05 (handshake fallback)
    bad-protocol      negotiates an unknown protocol version (client must refuse)
    paginated-tools   tools/list paginates via nextCursor (7 per page)
    no-tools          tools/list returns an empty list
    missing-tool      tools/list omits one required tool (contract check)
    slow              every response is delayed 5s (client timeout)
    exit              exits non-zero right after initialize
    bad-json          emits a malformed stdout line before each response
    stderr-noise      writes continuous noise to stderr

Ordinary CI therefore needs no Cua and no desktop.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REQUIRED_TOOLS_PATH = Path(__file__).resolve().parents[1] / "contract" / "required-tools.json"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
LEGACY_PROTOCOLS = ("2024-11-05",)
PAGE_SIZE = 7


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def reply(request_id, result=None, error=None) -> None:
    message = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result
    send(message)


def load_tools() -> list[dict]:
    names = json.loads(REQUIRED_TOOLS_PATH.read_text(encoding="utf-8"))["tools"]
    return [
        {
            "name": name,
            "description": f"fake tool {name}",
            "inputSchema": {"type": "object", "properties": {}},
        }
        for name in names
    ]


def main() -> int:
    mode = os.environ.get("FAKE_MODE", "ok")
    initialize_done = False
    initialized_notification_seen = False

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue

        method = message.get("method", "")
        request_id = message.get("id")

        if request_id is None:  # notification
            if method == "notifications/initialized" and initialize_done:
                initialized_notification_seen = True
            continue

        if mode == "exit" and method == "initialize":
            sys.stderr.write("fake server: injected failure (exit)\n")
            sys.stderr.flush()
            return 1

        if mode == "stderr-noise":
            sys.stderr.write(f"fake server stderr noise while handling {method}\n")
            sys.stderr.flush()

        if mode == "slow":
            time.sleep(5.0)

        if mode == "bad-json":
            sys.stdout.write("this line is definitively not json\n")
            sys.stdout.flush()

        if method == "initialize":
            if initialize_done:
                reply(request_id, error={"code": -32600, "message": "already initialized"})
                continue
            protocols = LEGACY_PROTOCOLS if mode == "legacy" else SUPPORTED_PROTOCOLS
            requested = (message.get("params") or {}).get("protocolVersion", "")
            if mode == "bad-protocol":
                reply(
                    request_id,
                    {
                        "protocolVersion": "1999-01-01",
                        "capabilities": {},
                        "serverInfo": {"name": "fake-cua-driver", "version": "0.24.0"},
                    },
                )
                continue
            if requested not in protocols:
                reply(
                    request_id,
                    error={"code": -32602, "message": f"unsupported protocol {requested}"},
                )
                continue
            initialize_done = True
            reply(
                request_id,
                {
                    "protocolVersion": requested,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "fake-cua-driver", "version": "0.24.0"},
                },
            )
        elif not initialize_done:
            reply(request_id, error={"code": -32002, "message": "server not initialized"})
        elif not initialized_notification_seen:
            reply(
                request_id,
                error={"code": -32002, "message": "notifications/initialized not received"},
            )
        elif method == "tools/list":
            tools = load_tools()
            if mode == "missing-tool":
                tools = [tool for tool in tools if tool["name"] != "drag"]
            elif mode == "no-tools":
                tools = []
            if mode == "paginated-tools":
                cursor = (message.get("params") or {}).get("cursor")
                start = 0
                if cursor is not None:
                    try:
                        start = int(cursor)
                    except (TypeError, ValueError):
                        reply(request_id, error={"code": -32602, "message": "bad cursor"})
                        continue
                page = tools[start : start + PAGE_SIZE]
                result = {"tools": page}
                if start + PAGE_SIZE < len(tools):
                    result["nextCursor"] = str(start + PAGE_SIZE)
                reply(request_id, result)
            else:
                reply(request_id, {"tools": tools})
        elif method == "tools/call":
            name = (message.get("params") or {}).get("name", "")
            reply(
                request_id,
                {
                    "content": [{"type": "text", "text": f"called {name}"}],
                    "structuredContent": {"ok": True, "tool": name},
                    "isError": False,
                },
            )
        else:
            reply(request_id, error={"code": -32601, "message": f"unknown method {method}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
