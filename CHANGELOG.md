# Changelog

All notable changes to the `computer-use` Agent Plugin are documented here.
The plugin version and the Cua version it projects are versioned
independently.

## [0.1.0] - 2026-09-08

### Added

- Agent Plugins 1.0.0 portable package: `plugin.json`, `mcp.json`
  (stdio → `cua-driver mcp`; validated as a deterministic generated
  declaration against a fixed template).
- **Official Skill projection** (no authored skill): the raw upstream pack
  pinned at tag `cua-driver-rs-v0.24.0` / commit
  `4b3396d9fe4bd3cf723b0eb8db83c18a8764b520` lives in
  `upstream/source/cua-driver/`; `scripts/project_cua_skill.py`
  deterministically generates `skills/cua-driver/` with a minimal
  registered transform set (`upstream/projection.json`): frontmatter
  normalization (official name/description verbatim), generated-notice
  insertion, one additive MCP-transport note, removal of the
  plugin-side auto-executable Windows installer one-liner, and exclusion
  of the pack README — everything else byte-exact, fail-closed on upstream
  drift, verified by offline regeneration (`verify_projection.py`) with a
  projection digest and reviewer report.
- Toolchain: `sync_cua.py` (download + pin only; git fallback fetches by
  resolved commit), `project_cua_skill.py`, `verify_upstream.py`,
  `verify_projection.py`, `validate_plugin.py` (pinned official Agent
  Plugins schemas + project policy), `mcp_client.py` (development-only
  harness), `mcp_probe.py` (L2 + contract snapshot),
  `e2e_calculator.py` (L3, dry-run unless `--yes`).
- Qualification receipt bound to `version + upstreamCommit + skillSource +
  projectionMode + projectionDigest + pluginCommit + driver artifact
  sha256`: Windows 11 (build 26200) x86_64 interactive desktop, DPI 100%,
  cua-driver 0.24.0 (sha256-verified release binary). L2: handshake
  2025-06-18, 57 tools, required subset 18/18. L3: Calculator `6 × 7 = 42`
  with launch-ownership proof, per-click element_token assertions,
  structured elements + image content, digit-bounded result match,
  owned-only cleanup with zero leftover windows.
- Tests (55) covering sync/projection/verification logic, receipt
  invalidation, Agent Skills frontmatter conformance, the mcp.json
  deterministic template, and the MCP client against a fake server;
  portable CI workflow.
