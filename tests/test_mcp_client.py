"""Tests for scripts/mcp_client.py against the fake MCP server (no Cua, no GUI)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "fake_mcp_server.py"
CONTRACT = Path(__file__).resolve().parents[1] / "tests" / "contract" / "required-tools.json"
sys.path.insert(0, str(SCRIPTS))
import mcp_client  # noqa: E402


def spawn(mode: str = "ok") -> mcp_client.McpStdioClient:
    return mcp_client.McpStdioClient(sys.executable, [str(FIXTURE)], env={"FAKE_MODE": mode})


class TestHappyPath:
    def test_handshake_tools_list_and_call(self):
        with spawn() as client:
            result = client.initialize(timeout=10)
            assert result["serverInfo"]["name"] == "fake-cua-driver"
            assert client.negotiated_protocol_version == "2025-06-18"

            tools = client.tools_list(timeout=10)
            required = set(json.loads(CONTRACT.read_text(encoding="utf-8"))["tools"])
            assert required <= {tool["name"] for tool in tools}

            call = client.tools_call("click", {"token": "abc"}, timeout=10)
            assert call["structuredContent"] == {"ok": True, "tool": "click"}

    def test_notification_is_accepted(self):
        with spawn() as client:
            client.initialize(timeout=10)
            client.notify("notifications/initialized")  # already sent by initialize()
            client.notify("some/other-notification", {"x": 1})
            # The connection stays usable afterwards.
            assert client.tools_list(timeout=10)


class TestFailureModes:
    def test_lifecycle_requires_initialized_notification(self):
        """initialize result alone must not unlock normal operation; the
        notifications/initialized notification completes the lifecycle."""
        with spawn() as client:
            client.start()
            client.request(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
                timeout=10,
            )
            with pytest.raises(mcp_client.McpError, match="initialized"):
                client.tools_list(timeout=10)
            client.notify("notifications/initialized")
            assert client.tools_list(timeout=10)  # now unlocked

    def test_tools_list_follows_pagination(self):
        with spawn("paginated-tools") as client:
            client.initialize(timeout=10)
            tools = client.tools_list(timeout=10)
            required = json.loads(CONTRACT.read_text(encoding="utf-8"))["tools"]
            assert {t["name"] for t in tools} == set(required)  # all pages aggregated

    def test_unsupported_negotiated_protocol_rejected(self):
        with spawn("bad-protocol") as client:
            client.start()
            with pytest.raises(mcp_client.McpConnectionError, match="unsupported protocol"):
                client.initialize(timeout=10)
            client.close()

    def test_legacy_protocol_fallback(self):
        with spawn("legacy") as client:
            result = client.initialize(timeout=10)
            assert client.negotiated_protocol_version == "2024-11-05"
            assert result["serverInfo"]["name"] == "fake-cua-driver"

    def test_empty_tools_list(self):
        with spawn("no-tools") as client:
            client.initialize(timeout=10)
            assert client.tools_list(timeout=10) == []

    def test_missing_required_tool_is_detectable(self):
        with spawn("missing-tool") as client:
            client.initialize(timeout=10)
            tools = client.tools_list(timeout=10)
            required = set(json.loads(CONTRACT.read_text(encoding="utf-8"))["tools"])
            missing = required - {tool["name"] for tool in tools}
            assert missing == {"drag"}  # exactly what the fixture removed

    def test_request_timeout(self):
        with spawn("slow") as client:
            client.initialize(timeout=10)
            started = time.monotonic()
            with pytest.raises(mcp_client.McpTimeout):
                client.tools_list(timeout=0.5)
            assert time.monotonic() - started < 4

    def test_server_exit_during_initialize(self):
        client = spawn("exit")
        client.start()
        with pytest.raises(mcp_client.McpConnectionError):
            client.initialize(timeout=10)
        client.close()

    def test_bad_stdout_json_is_recorded_but_stream_survives(self):
        with spawn("bad-json") as client:
            client.initialize(timeout=10)
            tools = client.tools_list(timeout=10)
            assert tools  # the well-formed line after the garbage still parsed
            assert client._stdout_bad_lines

    def test_stderr_noise_does_not_break_protocol(self):
        with spawn("stderr-noise") as client:
            client.initialize(timeout=10)
            assert client.tools_call("click", {}, timeout=10)["structuredContent"]["ok"]
            assert "stderr noise" in client.stderr_text

    def test_request_before_initialize_is_rejected_by_server(self):
        with spawn() as client:
            client.start()
            with pytest.raises(mcp_client.McpError) as excinfo:
                client.tools_list(timeout=10)
            assert "not initialized" in str(excinfo.value)
            client.close()


class TestRequiredToolsContract:
    def test_fake_server_satisfies_required_subset_by_default(self):
        """The §44 rule applied to the fixture: required ⊆ actual passes."""
        with spawn() as client:
            client.initialize(timeout=10)
            tools = client.tools_list(timeout=10)
            required = json.loads(CONTRACT.read_text(encoding="utf-8"))["tools"]
            actual = {tool["name"] for tool in tools}
            assert set(required) <= actual
