# Changelog

All notable changes to the `computer-use` Agent Plugin are documented here.
The plugin version and the Cua version it projects are versioned
independently.

## [0.1.0] - 2026-09-08

First stable release. `computer-use` is an independent Agent Plugins 1.0.0
distribution of Cua Driver: it mechanically projects the official Cua
Driver Agent Skill into a conforming Agent Skill and directly exposes the
official `cua-driver mcp` runtime. It implements no Computer Use behavior.

### Package

- Agent Plugins 1.0.0 portable package: `plugin.json`, `mcp.json`
  (stdio → the official `cua-driver mcp` entrypoint; validated against a
  pinned official schema plus a deterministic generator template).
- Production surface: `plugin.json` + `mcp.json` + `skills/cua-driver/`.
  Zero authored production code: no MCP server, no runtime, no wrapper,
  no independently authored operating guidance.

### Official Cua Skill projection (Cua 0.24.0)

- Raw upstream pack pinned at tag `cua-driver-rs-v0.24.0`, commit
  `4b3396d9fe4bd3cf723b0eb8db83c18a8764b520` (sha256 per file in
  `upstream/cua.lock.json`), cached under `upstream/source/cua-driver/`.
- `scripts/project_cua_skill.py` deterministically generates
  `skills/cua-driver/` with six registered, fail-closed transforms
  (`upstream/projection.json`): frontmatter normalization (name verbatim;
  description transport-normalized with its original SHA-256 retained),
  generated-notice insertion, the MCP transport normalization (description
  phrase + CLI-default "GUI transport defaults" block replacement +
  shell-section boundary note), exclusion of the host-specific Claude Code
  MCP setup guidance, removal of the plugin-side auto-executable Windows
  installer one-liner, and exclusion of the pack README. Everything else
  is byte-exact; any upstream drift fails closed.
- `scripts/verify_projection.py` proves offline that the committed skill
  tree equals a fresh regeneration (projection digest
  `sha256:b27ac9cbf6dae01eb428898490be66cd01f0c85192547cc0d5213c0a6fe7bc29`).

### Toolchain

- `sync_cua.py` (download + pin only; git fallback fetches by resolved
  commit; directory-aware non-recursive shape check), `project_cua_skill.py`,
  `verify_upstream.py`, `verify_projection.py`, `validate_plugin.py`
  (pinned official schemas + project policy), `mcp_client.py`
  (development-only negotiated-protocol qualification harness),
  `mcp_probe.py` (L2 + contract snapshot), `e2e_calculator.py` (L3,
  `--yes`-gated).

### Qualification (Windows)

- Receipt in `upstream/compatibility.json` bound to: Cua 0.24.0,
  upstream commit, skill source, projection mode + digest, plugin commit,
  and the official cua-driver Windows artifact sha256.
- L2: negotiated-protocol compatibility qualification (2025-06-18 as
  negotiated by the driver; 57 tools, required subset 18/18).
- L3: Calculator `6 × 7 = 42` semantic-only E2E — launch-ownership proof,
  per-click snapshot-bound element_token assertions, structured elements +
  MCP image content, digit-bounded exact result, owned-only cleanup.

### Tests & CI

- 63 tests: projection determinism and fail-closed drift, receipt
  invalidation, Agent Skills frontmatter conformance, mcp.json template
  determinism, supply-chain shape checks, and the MCP client against a
  fake server. Portable CI workflow (no Cua, no desktop).
