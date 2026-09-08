# Validation Guide

This project validates in four levels. Levels build on each other; a
platform enters `upstream/compatibility.json`'s `verified` list only after
L2 + L3 pass on a real machine.

## Level 1 — Portable (no Cua, no GUI, no network)

Runs in ordinary CI (`.github/workflows/validate.yml`) and on any machine
with Python 3.12+:

```bash
pip install -e ".[dev]"

python scripts/validate_plugin.py   # plugin.json/mcp.json/thin Skill structure
python scripts/verify_upstream.py   # upstream mirror hashes, offline
python -m pytest -v                 # unit tests + fake MCP server tests
```

What each proves:

- `validate_plugin.py` — Agent Plugins closed-core manifest rules, the
  single `cua-driver mcp` stdio declaration (no wrappers/proxies), fixed
  skill paths, thin-skill frontmatter conformance (name/description/
  license/compatibility/metadata rules, no unknown fields), and absence of
  forbidden local runtime components (`bin/`, wrapper/server entry files).
- `verify_upstream.py` — lock well-formedness, expected file set, no
  unexpected mirror files, sha256 match, thin-skill version ↔ lock
  agreement, license presence, notice ↔ lock agreement.
- `pytest` — sync logic (fail-closed shape checks, source-path policy,
  full mocked sync run), upstream verification logic, and the MCP client
  against `tests/fixtures/fake_mcp_server.py` (handshake, legacy fallback,
  tools/list, tools/call, timeout, bad stdout, stderr noise, process exit).

skills-ref (`skills-ref validate skills/`) is the Agent Skills reference
implementation and is **demonstration software** — it is not part of CI and
is not reliably installable from PyPI. The deterministic validators above
are the gate; you may run a skills-ref cross-check manually on a machine
where it is available.

## Level 2 — Cua MCP contract (needs real `cua-driver`)

```bash
python scripts/mcp_probe.py
```

Checks, in order: `cua-driver` on PATH → `cua-driver --version` → version
comparison against `compatibility.json`'s candidate → spawn
`cua-driver mcp` → MCP handshake → `tools/list` → required subset
(`tests/contract/required-tools.json` ⊆ actual tools).

- Version mismatch → FAIL by default (this tool produces qualification
  evidence); `--allow-version-mismatch` downgrades it to a WARN. The plugin
  runtime itself never refuses over versions.
- Contract rule: required ⊆ actual. New upstream tools pass; a removed
  required tool fails. Tool count is never asserted.

Snapshot the full contract for upgrade diffs:

```bash
python scripts/mcp_probe.py --snapshot
# writes tests/contract/cua-tools.snapshot.json
# (name, description, inputSchema, outputSchema, annotations)
```

## Level 3 — Desktop qualification (real OS + GUI + Cua)

```bash
python scripts/e2e_calculator.py         # dry run: prints the plan only
python scripts/e2e_calculator.py --yes   # actually drives the desktop
```

The Calculator `6 × 7 = 42` flow: `start_session` → discover Calculator →
launch → select the exact window → `get_window_state` → assert structured
accessibility elements → assert MCP image content → semantic clicks on
`6`, `×`, `7`, `=` (each against a fresh window state) → verify the
display shows 42 → `end_session`.

Rules baked into the script:

- **Semantic-only.** Buttons are located by accessibility name/role and
  activated via their semantic token; coordinate-based clicks are never
  constructed, so a semantic-path failure is a qualification failure.
- **`--yes` required.** Default invocation is a dry run; CI, test
  discovery, or accidental execution can never touch a real desktop.
- Schema-aware argument building: tool arguments are filled only with keys
  the tool's `inputSchema` actually declares (resilient to harmless
  renames, fail-closed on real contract drift).

## Level 4 — Supported

After L2 + L3 pass on a concrete environment, add a receipt entry to
`upstream/compatibility.json` → `verified` (see `upstream/README.md` for
the entry shape). Never add an entry just because Cua published a release.

Windows qualification matrix (first supported platform): Windows 11 x86_64
interactive desktop, exact candidate version; Calculator, Notepad, Chrome,
an Electron app; DPI 100% and 150/200%; background action with foreground
fallback; multi-monitor.

## What CI never does

Ordinary CI installs no Cua and touches no desktop. `mcp_probe`,
Calculator E2E, and the desktop matrix run on a dedicated interactive
qualification host, and their evidence lands in
`upstream/compatibility.json`.
