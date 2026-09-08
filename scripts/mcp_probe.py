#!/usr/bin/env python3
"""mcp_probe.py — Level 2 qualification: real Cua MCP contract check.

Requires a real `cua-driver` executable on PATH. Flow (design doc §41):

    locate cua-driver -> cua-driver --version -> compare with candidate
    -> spawn `cua-driver mcp` -> MCP handshake -> tools/list
    -> required tool subset check -> report

Contract rule (§44): required ⊆ actual. New upstream tools PASS; a missing
required tool FAILS. Tool count is never part of the contract (§45).

Version mismatch policy (§42): WARN / qualification failure — this probe
fails on mismatch by default (it produces qualification evidence); pass
--allow-version-mismatch to downgrade to a warning. The plugin runtime
itself never refuses to start over versions.

--snapshot writes tests/contract/cua-tools.snapshot.json (name, description,
inputSchema, outputSchema, annotations) for human diff review when
upgrading Cua (§46, §47).

Usage:
    python scripts/mcp_probe.py [--snapshot] [--allow-version-mismatch]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mcp_client

SNAPSHOT_FIELDS = ("name", "description", "inputSchema", "outputSchema", "annotations")
VERSION_OUTPUT_PATTERN = re.compile(r"(\d+\.\d+\.\d+[-0-9A-Za-z.+]*)")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--snapshot", action="store_true", help="write tests/contract/cua-tools.snapshot.json"
    )
    parser.add_argument(
        "--allow-version-mismatch",
        action="store_true",
        help="downgrade a candidate version mismatch from FAIL to WARN",
    )
    parser.add_argument(
        "--command", default="cua-driver", help="cua-driver executable (default: PATH lookup)"
    )
    parser.add_argument("--root", default=None, help="repository root")
    parser.add_argument("--timeout", type=float, default=60.0)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    required = json.loads(
        (root / "tests" / "contract" / "required-tools.json").read_text(encoding="utf-8")
    )["tools"]

    failures: list[str] = []
    warnings: list[str] = []

    # -- locate executable (§12: external runtime, never auto-installed) ----
    resolved = shutil.which(args.command)
    if not resolved:
        print(f"FAIL: executable {args.command!r} not found on PATH.")
        print(
            "      The plugin requires a user/host-installed cua-driver; "
            "it never installs Cua itself."
        )
        return 1
    print(f"found: {resolved}")

    # -- version ---------------------------------------------------------------
    try:
        version_output = subprocess.run(
            [resolved, "--version"], capture_output=True, text=True, timeout=30
        )
        version_text = (version_output.stdout + version_output.stderr).strip()
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"FAIL: cannot run `{resolved} --version` ({exc})")
        return 1
    match = VERSION_OUTPUT_PATTERN.search(version_text)
    runtime_version = match.group(1) if match else None
    print(f"cua-driver --version: {version_text!r} -> {runtime_version or 'unparsed'}")

    candidate = json.loads((root / "upstream" / "compatibility.json").read_text(encoding="utf-8"))[
        "candidate"
    ]
    print(
        f"candidate (from upstream/compatibility.json): {candidate['version']} ({candidate['tag']})"
    )
    if runtime_version is None:
        warnings.append("cua-driver --version output could not be parsed")
    elif runtime_version != candidate["version"]:
        message = (
            f"version mismatch: plugin guidance {candidate['version']} vs "
            f"host runtime {runtime_version}"
        )
        if args.allow_version_mismatch:
            warnings.append(message + " (allowed)")
        else:
            failures.append(message)

    # -- MCP handshake + tools/list ---------------------------------------------
    tools: list[dict] = []
    try:
        with mcp_client.McpStdioClient(resolved, ["mcp"]) as client:
            init = client.initialize(timeout=args.timeout)
            info = init.get("serverInfo", {})
            print(
                f"MCP handshake OK: serverInfo={info} protocol={client.negotiated_protocol_version}"
            )
            tools = client.tools_list(timeout=args.timeout)
            stderr_tail = client.stderr_text.strip().splitlines()[-3:]
            if stderr_tail:
                print(f"server stderr (tail): {stderr_tail}")
    except Exception as exc:  # qualification tool: report, don't traceback
        print(f"FAIL: MCP negotiation/tools-list failed: {exc}")
        failures.append(f"MCP negotiation failed: {exc}")

    if tools:
        actual = {tool.get("name", "") for tool in tools}
        print(f"tools/list: {len(tools)} tools")
        missing = sorted(set(required) - actual)
        if missing:
            failures.append(f"required tools missing from server: {missing}")
        else:
            print(f"required tool subset PASS ({len(required)}/{len(required)} present)")

    # -- optional contract snapshot (§46) --------------------------------------
    if args.snapshot and tools:
        snapshot = [
            {field: tool.get(field) for field in SNAPSHOT_FIELDS if field in tool}
            for tool in sorted(tools, key=lambda t: t.get("name", ""))
        ]
        snapshot_path = root / "tests" / "contract" / "cua-tools.snapshot.json"
        snapshot_path.write_text(
            json.dumps(
                {"serverInfo": info if tools else None, "tools": snapshot},
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"snapshot written: {snapshot_path} ({len(snapshot)} tools)")

    # -- report -------------------------------------------------------------------
    print()
    for warning in warnings:
        print(f"WARN: {warning}")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    if not tools:
        return 1
    print("MCP probe PASS (Level 2 contract).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
