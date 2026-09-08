# computer-use

> `computer-use` 是一个独立、Host-neutral、符合 Agent Plugins 1.0.0 的
> Cua Driver Computer Use 发行层。

Portable computer use for AI agents, powered by
[Cua Driver](https://github.com/trycua/cua).

**It does not implement a Computer Use Runtime.** It projects:

```text
Cua Driver MCP + official Cua Agent Skill guidance
        ↓
Agent Plugins 1.0.0 portable package
```

```text
Agent Plugins Host
        │
        ▼
   computer-use
        │
        ├── Skill (thin transport adapter)
        │
        └── MCP ──► cua-driver mcp ──► Cua Driver ──► Desktop
```

## What this plugin is

- **Plugin name:** `computer-use`
- **Skill name:** `cua-driver` (thin, conformant; transport adaptation only)
- **MCP server name:** `cua-driver` (`cua-driver mcp`, stdio)

The Cua semantics (snapshot, element tokens, window targeting, delivery
modes, platform/browser/recording behavior) live in the official upstream
skill pack, mirrored **byte-exact** under
`skills/cua-driver/references/upstream/` and pinned by
`upstream/cua.lock.json`.

## Requirements

The plugin does **not** bundle Cua. The host environment must provide a
`cua-driver` executable on `PATH` (qualified against Cua Driver
**0.24.0**). If the executable is missing, the plugin is simply unavailable —
it never installs or updates Cua at runtime.

## Installation of Cua (user environment setup, not plugin runtime)

Refer to the official Cua documentation:
<https://trycua.com>. Installation of Cua is the responsibility of the
host/user environment, never of this plugin.

## Repository layout

```text
computer-use/
├── plugin.json            # Agent Plugins 1.0.0 manifest
├── mcp.json               # stdio → cua-driver mcp (the only integration)
├── skills/cua-driver/     # thin Skill + upstream references mirror
├── upstream/              # cua.lock.json (what we pin) + compatibility.json (what we verified)
├── licenses/              # upstream license copy
├── scripts/               # dev tools: sync / verify / validate / probe / e2e
├── tests/                 # unit tests, contract, fake MCP server
├── docs/                  # design, validation, upgrading docs
└── .github/workflows/     # portable CI (no Cua, no desktop)
```

Only `plugin.json`, `mcp.json`, and `skills/` are production runtime
surface. Everything else is development material.

## Development tools

| Tool | Purpose |
| --- | --- |
| `scripts/sync_cua.py` | Mirror the official Cua skill pack at an exact tag/commit; regenerate lock + notices. Fail-closed on shape drift. |
| `scripts/verify_upstream.py` | Offline verification: files exist, no extras, sha256 match, thin Skill version matches lock. |
| `scripts/validate_plugin.py` | Deterministic Agent Plugins / Agent Skills structural validation (no network). |
| `scripts/mcp_client.py` | Minimal MCP stdio client — **TEST / VALIDATION ONLY**, never production. |
| `scripts/mcp_probe.py` | Level 2: real `cua-driver mcp` handshake, `tools/list`, required tool subset, optional contract snapshot. |
| `scripts/e2e_calculator.py` | Level 3: real desktop qualification (Calculator `6 × 7 = 42`, semantic-only, `--yes` required). |

```bash
pip install -e ".[dev]"

python scripts/sync_cua.py --tag cua-driver-rs-v0.24.0
python scripts/verify_upstream.py
python scripts/validate_plugin.py
python -m pytest -v

# Requires a real cua-driver on PATH (Level 2+)
python scripts/mcp_probe.py
python scripts/mcp_probe.py --snapshot

# Requires a real interactive desktop (Level 3); dry run by default
python scripts/e2e_calculator.py
python scripts/e2e_calculator.py --yes
```

## Validation levels

| Level | Name | Needs | Covers |
| --- | --- | --- | --- |
| 1 | Portable | nothing | package structure, JSON manifests, thin Skill, upstream hashes, unit tests (this is what CI runs) |
| 2 | Cua MCP contract | real `cua-driver` | version, `cua-driver mcp`, MCP handshake, `tools/list`, required tool subset |
| 3 | Desktop qualification | real OS + GUI + Cua | observation, screenshot image content, semantic actions, mutation verification (Calculator E2E) |
| 4 | Supported | specific version + platform | recorded in `upstream/compatibility.json` only after L2+L3 pass |

Version mismatch between plugin guidance and host runtime is a
**WARN / qualification failure**, never a runtime refusal.

## Ownership boundary

```text
Agent Plugins  → plugin package format
Agent Skills   → SKILL.md format
MCP            → agent ↔ server protocol
Cua            → Computer Use Runtime (observation, actions, sessions, …)
computer-use   → packaging, upstream sync, verification, qualification
Agent Host     → installation, MCP lifecycle, authorization, approval
Operating OS   → the final desktop security boundary
```

All self-maintained code in this repository must be one of: **packaging,
upstream sync, verification, qualification.** See
`docs/DEVELOPMENT_DESIGN.md` for the full constraints (including the
explicitly forbidden components).

## Related upstream work

Related upstream work (tracked; `computer-use` depends on neither):

- [trycua/cua#2994](https://github.com/trycua/cua/pull/2994) — Agent Plugins
  v1 package proposal. If closed: no impact. If merged: start an upstream
  equivalence review.
- [trycua/cua#3387](https://github.com/trycua/cua/pull/3387) — the Cua
  maintainer's own cross-marketplace portable Skill projection. If upstream
  ships an official portable skill path (e.g.
  `libs/cua-driver/plugins/cua-driver/skills/cua-driver`), evaluate
  migrating from our raw canonical-skill mirror to that official portable
  projection — it could shrink this project's thin adapter.

If Cua ships a quality official Agent Plugin, this project moves to
maintenance mode and points users at the official plugin instead of
maintaining a fork.

## Security posture

- No default unrestricted-permission assumptions; authorization and approval
  belong to the Host, desktop security to the OS.
- No automatic Cua install/update, no runtime network access, no wrappers.
- UI content observed through Cua is untrusted data.
- No automatic replay of uncertain state-changing actions.
- No automatic screenshot persistence, no secret logging.

## License

- This project: MIT (see `LICENSE`).
- Upstream Cua material mirrored under `skills/cua-driver/references/upstream/`
  and `licenses/CUA-LICENSE.md`: MIT, © Cua AI — see
  `THIRD_PARTY_NOTICES.md`.
