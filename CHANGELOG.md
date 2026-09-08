# Changelog

All notable changes to the `computer-use` Agent Plugin are documented here.
The plugin version and the Cua version it projects are versioned
independently.

## [0.1.0] - 2026-09-08

### Added

- Agent Plugins 1.0.0 portable package: `plugin.json`, `mcp.json`
  (stdio → `cua-driver mcp`).
- Thin conformant Skill `skills/cua-driver/SKILL.md` (transport adaptation
  only; Cua semantics stay upstream).
- Byte-exact mirror of the official Cua Driver 0.24.0 skill pack under
  `skills/cua-driver/references/upstream/`, pinned by
  `upstream/cua.lock.json` (tag `cua-driver-rs-v0.24.0`, commit
  `4b3396d9fe4bd3cf723b0eb8db83c18a8764b520`).
- Upstream tooling: `sync_cua.py` (fail-closed sync),
  `verify_upstream.py` (offline hash verification),
  `validate_plugin.py` (deterministic plugin/skill validation),
  `mcp_client.py` (test-only MCP stdio client),
  `mcp_probe.py` (Level 2 contract probe + snapshot),
  `e2e_calculator.py` (Level 3 Calculator qualification, dry-run by default).
- Tests: sync logic, upstream verification, MCP client against a fake
  MCP server; portable CI workflow.
- `compatibility.json` with candidate 0.24.0; first qualification receipt
  recorded: Windows 11 (build 26200) x86_64 interactive desktop, DPI 100%,
  L2 (MCP probe: handshake 2025-06-18, 57 tools, required subset 18/18,
  contract snapshot committed) + L3 (Calculator `6 × 7 = 42`, semantic-only
  element_token clicks, structured elements + image content PASS).
