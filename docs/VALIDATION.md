# Validation Guide

Four levels. Levels build on each other; a platform enters
`upstream/compatibility.json`'s `verified` list only after L2 + L3 pass on
a real machine.

## Level 1 — Portable (no Cua, no GUI, no network)

Runs in ordinary CI (`.github/workflows/validate.yml`) and on any machine
with Python 3.12+:

```bash
uv sync --locked --extra dev

uv run ruff check scripts tests && uv run ruff format --check scripts tests
uv run python scripts/validate_plugin.py    # official pinned schemas + project policy
uv run python scripts/verify_upstream.py    # raw source hashes vs lock, offline
uv run python scripts/verify_projection.py  # skill tree == regeneration, offline
uv run python -m pytest -v                  # unit tests + fake MCP server tests
```

Python dependencies come from the committed `uv.lock` (`--locked`) and
are reproducibly locked; the GitHub-hosted runner image itself is not
bit-reproducible (pinned to ubuntu-24.04 to bound the drift). Before tagging a
release, `uv run python scripts/release_check.py` machine-proves the
evidence chain (see the release acceptance list in DEVELOPMENT_DESIGN §14).

What each proves:

- `validate_plugin.py` — Agent Plugins closed-core manifest rules (from the
  pinned official schemas, never hand-reimplemented), mcp.json equals the
  deterministic generator template (one stdio server directly invoking
  `cua-driver mcp`), the projected skill tree layout (no legacy
  `references/` model), full Agent Skills frontmatter conformance, the
  generated-marker, and absence of forbidden local runtime components.
- `verify_upstream.py` — lock well-formedness, raw source file set, sha256
  match, license presence, notice/lock agreement.
- `verify_projection.py` — the committed skill tree equals a fresh
  regeneration from the raw source (no unexplained diff can exist), the
  current projection + production surface + qualification harness digests
  match the receipts,
  `projection.json`/`projection-report.json` consistency, projection-digest
  stability, and that every qualification receipt still matches the pinned
  source + current projection digest (invalidated receipts FAIL until
  re-qualification).
- `pytest` — sync/projection/verification logic (fail-closed shape checks,
  transform drift, digest stability, receipt invalidation) and the MCP
  client against the fake MCP server (handshake, legacy fallback,
  tools/list, tools/call, timeout, bad stdout, stderr noise, process exit).

skills-ref is the Agent Skills reference implementation and is
**demonstration software** — it is not part of CI. The deterministic
validators are the gate; a skills-ref cross-check can be run manually.

## Level 2 — Cua MCP contract (needs real `cua-driver`)

```bash
uv run python scripts/mcp_probe.py
uv run python scripts/mcp_probe.py --snapshot   # writes the contract snapshot
```

Checks `cua-driver` on PATH → `--version` vs candidate → spawn
`cua-driver mcp` → handshake → `tools/list` → required subset
(`tests/contract/required-tools.json` ⊆ actual). Version mismatch FAILs by
default (this tool produces qualification evidence);
`--allow-version-mismatch` downgrades to a WARN. The plugin runtime never
refuses over versions. Contract rule: required ⊆ actual; tool count is
never asserted.

Protocol scope (deliberate): this is a **negotiated Cua MCP compatibility
qualification** — the harness validates the protocol revision negotiated
with the server under test (2025-06-18 with Cua 0.24.0, the newest the
driver supports; the client downgrades automatically if a server
negotiates older). It does NOT claim an independent modern+legacy
protocol matrix. When MCP protocol coverage needs to grow, the plan is to
adopt the official MCP SDK for the harness rather than expand this custom
one.

## Level 3 — Desktop qualification (real OS + GUI + Cua)

```bash
uv run python scripts/e2e_calculator.py          # dry run: prints the plan only
uv run python scripts/e2e_calculator.py --yes      # actually drives the desktop
```

The Calculator `6 × 7 = 42` flow: `start_session` → discover Calculator →
launch with **ownership tracking** (pre-launch window baseline; only the
selected new window enters `owned_window_ids`) → `get_window_state` →
assert structured accessibility elements → assert MCP image content →
semantic clicks on `6`, `×`, `7`, `=` with the **element_token asserted on
every click** (fresh state before each action) → verify the display shows
exactly 42 (digit-bounded) → **owned-only cleanup** → `end_session`.

Rules baked into the script: semantic-only (no pixel coordinates, ever);
`--yes` required (CI/test discovery can never touch a desktop); cleanup
verifies only against owned window ids — a pre-existing calculator
elsewhere is not this run's concern.

## Level 4 — Supported

After L2 + L3 pass on a concrete environment, add a receipt to
`upstream/compatibility.json` → `verified` (see `upstream/README.md`).
Receipts bind `version + upstreamCommit + skillSource + projectionMode +
projectionDigest + pluginSurfaceDigest + qualificationHarnessDigest +
testedPluginCommit + driver artifact sha256`; any change
invalidates them (enforced by `verify_projection.py`).

Windows qualification target matrix (intended scope): Windows 11 x86_64
interactive desktop, exact candidate version; Calculator, Notepad, Chrome,
an Electron app; DPI 100% and 150/200%; background action with foreground
fallback; multi-monitor. Actual verified support is defined exclusively by
the receipts in `upstream/compatibility.json`.

## What CI never does

Ordinary CI installs no Cua and touches no desktop. `mcp_probe`,
Calculator E2E, and the desktop matrix run on a dedicated interactive
qualification host.
