#!/usr/bin/env python3
"""mcp_client.py — minimal MCP stdio client.

    ********************************************
    *   TEST / VALIDATION ONLY — NOT RUNTIME   *
    ********************************************

The MCP qualification client is development-only tooling (design doc v2,
§20/§38): prefer standards-current MCP tooling where practical; a small
custom harness is acceptable when it improves deterministic qualification
(see tests/fixtures/fake_mcp_server.py fault injection). Custom MCP code is
NEVER part of the production plugin — the production path is always:
Agent Host -> mcp.json -> `cua-driver mcp` (official Cua entrypoint).

Protocol scope: this harness performs a NEGOTIATED compatibility
qualification — it offers the newest protocol revision it knows and
downgrades automatically when the server negotiates an older one. It does
not claim an independent modern+legacy protocol matrix; when broader
protocol coverage is needed, adopt the official MCP SDK instead of
growing this harness.

Capabilities: spawn stdio server, MCP initialize handshake (newest-known
protocol first, automatic downgrade), requests, notifications,
tools/list, tools/call, per-request timeout, stderr capture, clean
shutdown.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

__all__ = ["McpConnectionError", "McpError", "McpStdioClient", "McpTimeout"]

CLIENT_NAME = "computer-use-dev"
CLIENT_VERSION = "0.1.0"
# Newest first; we downgrade only when the server explicitly rejects one.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")


class McpError(RuntimeError):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"MCP error {code}: {message}")
        self.code = code
        self.data = data


class McpTimeout(TimeoutError):
    pass


class McpConnectionError(ConnectionError):
    pass


class _Pending:
    __slots__ = ("event", "response")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.response: dict | None = None


class McpStdioClient:
    """Minimal newline-delimited JSON-RPC client for an MCP stdio server."""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        env: dict[str, str] | None = None,
        cwd: str | Path | None = None,
    ) -> None:
        self.command = command
        self.args = list(args or [])
        self._extra_env = env or {}
        self._cwd = cwd
        self._proc: subprocess.Popen | None = None
        self._write_lock = threading.Lock()
        self._pending: dict[int, _Pending] = {}
        self._next_id = 0
        self._reader: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_lines: list[str] = []
        self._stdout_bad_lines: list[str] = []
        self.negotiated_protocol_version: str | None = None
        self.server_info: dict | None = None

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        import os

        env = dict(os.environ)
        env.update({k: str(v) for k, v in self._extra_env.items()})
        self._proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(self._cwd) if self._cwd else None,
        )
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()

    def close(self, *, timeout: float = 10.0) -> None:
        if self._proc is None:
            return
        proc, self._proc = self._proc, None
        try:
            if proc.stdin and not proc.stdin.closed:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=timeout)
        finally:
            for pending in self._pending.values():
                pending.event.set()
            self._pending.clear()

    def __enter__(self) -> McpStdioClient:
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    @property
    def stderr_text(self) -> str:
        return "".join(self._stderr_lines)

    # ------------------------------------------------------------- transport

    def _read_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        for raw_line in self._proc.stdout:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self._stdout_bad_lines.append(line)
                continue
            if not isinstance(message, dict) or "id" not in message:
                continue  # notification from server; not needed by this client
            pending = self._pending.get(message["id"])
            if pending is not None:
                pending.response = message
                pending.event.set()
        # stdout EOF: the server is gone — wake every pending request so
        # they fail fast with McpConnectionError instead of waiting out.
        for pending in self._pending.values():
            pending.event.set()

    def _read_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        for raw_line in self._proc.stderr:
            self._stderr_lines.append(raw_line.decode("utf-8", "replace"))

    def _ensure_running(self) -> subprocess.Popen:
        if self._proc is None or self._proc.poll() is not None:
            code = self._proc.returncode if self._proc else "not started"
            raise McpConnectionError(f"server process is not running (exit={code})")
        return self._proc

    def _send(self, payload: dict) -> None:
        proc = self._ensure_running()
        assert proc.stdin
        data = json.dumps(payload, separators=(",", ":")) + "\n"
        with self._write_lock:
            try:
                proc.stdin.write(data.encode("utf-8"))
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise McpConnectionError(f"server stdin closed: {exc}") from exc

    # ------------------------------------------------------------- protocol

    def request(self, method: str, params: dict | None = None, *, timeout: float = 30.0) -> Any:
        self._next_id += 1
        request_id = self._next_id
        pending = _Pending()
        self._pending[request_id] = pending
        try:
            self._send(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
            )
            if not pending.event.wait(timeout):
                raise McpTimeout(f"timed out after {timeout}s waiting for {method!r}")
            if pending.response is None:
                raise McpConnectionError(f"connection closed while awaiting {method!r}")
            if "error" in pending.response:
                error = pending.response["error"]
                raise McpError(error.get("code", -32603), error.get("message", "unknown error"))
            return pending.response.get("result")
        finally:
            self._pending.pop(request_id, None)

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self, *, timeout: float = 30.0) -> dict:
        """Handshake: initialize -> validate negotiated version ->
        notifications/initialized (lifecycle MUST in MCP 2025-06-18).

        Newest-known protocol is offered first; a server that rejects a
        version gets the next older one. A server that negotiates a
        version this client does not know is a hard error (no guessing).
        """
        last_error: Exception | None = None
        for version in PROTOCOL_VERSIONS:
            try:
                result = self.request(
                    "initialize",
                    {
                        "protocolVersion": version,
                        "capabilities": {},
                        "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
                    },
                    timeout=timeout,
                )
            except McpError as exc:
                last_error = exc
                if self._proc is None or self._proc.poll() is not None:
                    raise McpConnectionError("server exited during initialize") from exc
                continue  # server rejected this version; try the next older one
            if not isinstance(result, dict):
                raise McpConnectionError("malformed initialize result")
            negotiated = result.get("protocolVersion", version)
            if negotiated not in PROTOCOL_VERSIONS:
                raise McpConnectionError(
                    f"server negotiated unsupported protocol version {negotiated!r} "
                    f"(client supports {PROTOCOL_VERSIONS})"
                )
            self.negotiated_protocol_version = negotiated
            self.server_info = result.get("serverInfo")
            # MCP lifecycle MUST: the client sends notifications/initialized
            # after receiving the initialize result, before normal operation.
            self.notify("notifications/initialized")
            return result
        raise McpConnectionError(
            f"server rejected every supported protocol version ({PROTOCOL_VERSIONS}); "
            f"last error: {last_error}"
        )

    # ------------------------------------------------------------- helpers

    def tools_list(self, *, timeout: float = 30.0) -> list[dict]:
        """List tools, following nextCursor pagination until exhausted."""
        tools: list[dict] = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self.request("tools/list", params, timeout=timeout)
            if not isinstance(result, dict):
                raise McpConnectionError(f"malformed tools/list result: {result!r}")
            page = result.get("tools")
            if not isinstance(page, list):
                raise McpConnectionError(f"malformed tools/list page: {result!r}")
            tools.extend(page)
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def tools_call(
        self, name: str, arguments: dict | None = None, *, timeout: float = 120.0
    ) -> dict:
        result = self.request(
            "tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout
        )
        if not isinstance(result, dict):
            raise McpConnectionError(f"malformed tools/call result: {result!r}")
        if result.get("isError"):
            raise McpError(-32000, f"tool {name!r} reported isError", result)
        return result


def wait_for_exit(proc: subprocess.Popen, timeout: float = 10.0) -> int:
    """Development helper: wait for a spawned process to exit."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = proc.poll()
        if code is not None:
            return code
        time.sleep(0.05)
    raise McpTimeout(f"process did not exit within {timeout}s")
