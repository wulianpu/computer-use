# Development Design (computer-use v0.1)

> **This document is the authoritative implementation design for v0.1.**
> `docs/archive/INITIAL_DESIGN.zh-CN.md` is historical context only — it
> describes the superseded thin-skill/references architecture and MUST NOT
> override this document or the current architecture.

## 1. Project definition

`computer-use` is an independent, host-neutral, Agent Plugins 1.0.0
**distribution layer** for Cua Driver Computer Use. It does **not**
implement a Computer Use Runtime. It projects:

```text
Cua Driver MCP + official Cua Agent Skill guidance
        ↓
Agent Plugins 1.0.0 portable package
```

Final flow: Agent Plugins Host → plugin.json → (projected official Cua
skill) + (mcp.json → official `cua-driver mcp` → Cua Driver → Desktop) →
Agent → Cua MCP tools → Desktop.

## 2. Ownership boundary (the core architecture rule)

| Owner | Responsibility |
| --- | --- |
| Agent Plugins | plugin package format |
| Agent Skills | SKILL.md format |
| MCP | agent ↔ server protocol |
| Cua | Computer Use Runtime: observation, actions, session, window targeting, browser, platform semantics, permissions, recording |
| **computer-use** | **packaging, projection, upstream synchronization, verification, qualification** |
| Agent Host | installation, MCP lifecycle, agent execution, authorization, approval |
| Operating System | the final desktop security boundary |

Every piece of self-maintained code must belong to one of the five allowed
categories (packaging / projection / upstream synchronization / verification /
qualification). If it
does not, answer first: *why should this not be Cua's, the Host's, MCP's, or
the OS's job?*

## 3. Explicitly forbidden (v0.1)

No `ComputerUseRuntime`, `ComputerUseBackend`, `CuaBackend`, `CuaAdapter`,
`ComputerUseServer`, `ComputerUseSessionStore`, `CaptureStore`,
`ScreenshotManager`, `MouseDriver`, `KeyboardDriver`,
`AccessibilityDriver`, `BrowserDriver`, `ComputerUseProtocol`,
`CuaProtocolProxy`, or `MCP Proxy`. No `computer_use(action=...)` /
`computer_observe(...)` facade. The production path is always
Agent → Cua MCP tools → `cua-driver`.

No host dependencies (SWS Work, Codex, Claude Code, Cursor, OpenClaw,
Hermes, VS Code, Electron, Kiro) in production dependencies.

## 4. Runtime surface vs development material

Production runtime surface (all the Host needs):

```text
plugin.json   mcp.json   skills/
```

Everything else — `scripts/`, `tests/`, `upstream/`, `docs/`, `.github/`,
`licenses/` — is development material.

## 5. Identity and manifests

- Plugin `computer-use` provides Skill `cua-driver` and MCP server
  `cua-driver`.
- `plugin.json` uses the closed core schema. Forbidden keys:
  `cuaVersion`, `runtime`, `permissions`, `tools`, `platform`,
  `computerUse` (Skills/MCP are discovered from fixed directories).
- `mcp.json` declares exactly one stdio server invoking `cua-driver mcp`
  directly — never a shell/interpreter wrapper, proxy, or bundled server.
- External runtime profile: the plugin does not bundle Cua. If
  `cua-driver` is not on PATH the plugin is *unavailable*; it never
  installs, downloads, or updates Cua at runtime. No runtime network access.

## 6. Skill model: official Skill projection (no authored skill)

There is no "our own Skill". The production Agent Skill under
`skills/cua-driver/` is a **deterministic, auditable, minimal projection of
the official Cua Driver skill pack**:

```text
official upstream SKILL.md  →  deterministic projection  →  skills/cua-driver/SKILL.md
```

The raw upstream pack lives in `upstream/source/cua-driver/` (outside the
skill discovery tree); `scripts/project_cua_skill.py` generates the skill
tree; hand-editing is forbidden and detected (regeneration comparison).
Upstream's own frontmatter is not strictly conforming Agent Skills
metadata (nested `metadata.openclaw`, `version` keys), so the projection
normalizes it: the upstream **name is preserved verbatim**; the upstream
**description is transport-normalized only** ("via the cua-driver CLI
(default) or MCP server" → "via the Cua Driver MCP server") with its
**original SHA-256 retained in projected metadata**
(`upstream-description-sha256`).

The complete transform set is registered in `upstream/projection.json` and
kept minimal: frontmatter normalization, generated-notice insertion, the
fail-closed MCP transport normalization (the description phrase above;
the upstream CLI-default "GUI transport defaults" block replaced
byte-exactly by the Agent Plugin MCP transport contract; a boundary note
scoping the shell/management section to hosts that really provide a
shell), exclusion of the host-specific Claude Code MCP setup subsection,
removal of the plugin-side auto-executable Windows installer one-liner,
and exclusion of the pack README. Everything else is byte-exact.
Transforms fail closed: an expected upstream block that changed upstream
aborts the projection for manual review.
`scripts/verify_projection.py` re-proves offline that the committed tree
equals the regeneration, and invalidates qualification receipts when the
projection digest changes. Long-term goal (trycua/cua#3387):
`upstream-portable` mode with **zero transforms**.

`computer-use` MUST NOT author independent Computer Use operating
guidance. If a piece of production Computer Use behavior is authored here
instead of coming from Cua, the architecture has drifted.

## 7. Upstream pinning and sync

`upstream/cua.lock.json` pins repository, version, tag, **immutable
commit**, skill source path, and per-file sha256. First candidate:

```text
repository: trycua/cua
version:    0.24.0
tag:        cua-driver-rs-v0.24.0
commit:     4b3396d9fe4bd3cf723b0eb8db83c18a8764b520
skillSource: libs/cua-driver/rust/Skills/cua-driver
```

`scripts/sync_cua.py` downloads and pins ONLY (raw cache under
`upstream/source/cua-driver/`, license, lock, notices). It never touches
the skill tree (that is `project_cua_skill.py`) or receipts (that is
qualification). The git fallback fetches by the resolved commit SHA —
never HEAD, a branch, or the tag. A `skillSource` change requires
`--allow-source-path-change` (manual review, never a silent fallback).
Pipeline: `sync_cua.py` → `project_cua_skill.py` → `verify_upstream.py` /
`verify_projection.py` → `validate_plugin.py`.

## 8. Validation levels

| Level | Name | Requires | Proves |
| --- | --- | --- | --- |
| 1 | Portable | nothing | structure, manifests, projected skill + projection verification, hashes, unit + fake-MCP tests (CI) |
| 2 | Cua MCP contract | real `cua-driver` | version, `cua-driver mcp`, handshake, tools/list, required subset (`required ⊆ actual`; count is never a contract) |
| 3 | Desktop qualification | real OS + GUI + Cua | observation, screenshot/image content, accessibility, semantic action, mutation verification |
| 4 | Supported | concrete version+platform receipts in `compatibility.json` | support matrix entry |

Version mismatch (e.g. guidance 0.24.0 vs runtime 0.23.2) is WARN /
qualification failure — never a runtime refusal; the plugin runtime has no
version-check wrapper.

## 9. Tooling inventory

| Script | Category | Notes |
| --- | --- | --- |
| `scripts/sync_cua.py` | upstream sync | download + pin only (raw cache, license, lock, notices); git fallback fetches by resolved commit SHA, never HEAD |
| `scripts/project_cua_skill.py` | projection | deterministic skill projection; minimal registered transforms; fail-closed on upstream drift; writes `projection.json` + `projection-report.json` |
| `scripts/verify_upstream.py` | verification | offline raw-source hashes vs lock, license, notices |
| `scripts/verify_projection.py` | verification | offline: skill tree == regeneration, projection.json/report consistency, receipt validity (projection digest bound) |
| `scripts/validate_plugin.py` | verification | pinned official schemas + project policy, offline; mcp.json deterministic template; projected-skill Agent Skills conformance |
| `scripts/mcp_client.py` | verification | development-only MCP qualification harness (custom harness acceptable; official SDK acceptable; never production) |
| `scripts/mcp_probe.py` | qualification (L2) | handshake, tools/list, required subset, optional `--snapshot` contract snapshot for upgrade diffs |
| `scripts/e2e_calculator.py` | qualification (L3) | Calculator `6 × 7 = 42`, semantic-only with asserted element tokens, ownership-proven (`owned_window_ids = {selected window}`), exact digit-bounded result, owned-only cleanup |

Validation model: the **official Agent Plugins schemas, pinned as local
copies under `schemas/agent-plugins/1.0.0/`, are the authority** for
plugin.json / mcp.json field sets (validated with `jsonschema`, never
re-implemented by hand). `validate_plugin.py` adds only project policy
(name/server identity, direct `cua-driver mcp` invocation, no wrappers, no
bundled runtime, projected-skill conformance). skills-ref (Agent Skills
reference implementation) is demonstration software; it is not a CI gate
here — the deterministic validators are, and a skills-ref cross-check can
be run manually where available.

Qualification receipts in `upstream/compatibility.json` are bound to
`(version, upstreamCommit, skillSource, projectionMode, projectionDigest)`
— any same-version re-pin or projection change invalidates them — and
record the plugin commit plus the exact driver artifact digest the
evidence was produced against.

Toolchain: Python ≥3.12, pytest/jsonschema/PyYAML as *development*
dependencies only. The plugin runtime has no Python, no Node, no custom
process.

## 10. Qualification posture

- Windows **qualification target matrix** (what we intend to qualify:
  Windows 11, x86_64, interactive desktop, exact candidate Cua version;
  Calculator, Notepad, Chrome, an Electron app; DPI 100% and 150/200%;
  background action + foreground fallback; multi-monitor). Actual verified
  support is defined exclusively by the receipts in
  `upstream/compatibility.json` — nothing else confers "supported".
- macOS is handled separately (CuaDriver.app, Accessibility/Screen
  Recording TCC, responsible application identity) — the plugin never
  "solves" TCC itself.
- Linux records must name the display server/compositor (X11, GNOME
  Wayland, KDE Wayland, Hyprland, …).
- The Calculator E2E never falls back to blind pixel clicks: a semantic
  failure is a qualification failure, and it requires `--yes` so CI or
  accidental runs can never touch a real desktop.
- Live qualification runs on a dedicated interactive host, not in CI.

## 11. Upgrading Cua

Fixed sequence: discover release → update candidate only → `sync_cua.py`
→ check source-path/file-set drift → review official skill diff →
`project_cua_skill.py` + `verify_upstream.py` + `verify_projection.py` →
projection diff review → plugin/skill
validation → `mcp_probe.py --snapshot` → tool/schema diff (semantic fields:
element_token, window targeting, delivery_mode, coordinates,
structuredContent, image content, session lifecycle, desktop/browser
semantics) → real desktop qualification → update `verified` → release.
Automated upgrade bots and auto-merge are forbidden.

On every upgrade, additionally check whether upstream now ships its own
Agent Plugin (`plugin.json`/`mcp.json`/`skills/…`). If it does, trigger
`UPSTREAM_MIGRATION_REVIEW`; migrate when the official package satisfies
conformance, compatibility, platform behavior, and stable artifacts, and
move this project to maintenance mode pointing at the official plugin.

Related upstream work (this project depends on neither):
trycua/cua#3387 — the maintainer's cross-marketplace portable Skill
projection; if upstream ships
`libs/cua-driver/plugins/cua-driver/skills/cua-driver` in a release,
trigger **UPSTREAM_PORTABLE_PROJECTION_REVIEW** and switch `projectionMode`
from `raw-skill-normalized` to `upstream-portable`, targeting zero
transforms. trycua/cua#2994 — the Agent Plugins v1 package proposal; if
upstream ships `plugin.json`/`mcp.json`/`skills/cua-driver/`, trigger
**UPSTREAM_PLUGIN_MIGRATION_REVIEW** (maintenance mode if the official
plugin qualifies).

## 12. Versioning, security, release

- Plugin version and Cua version are independent (`0.1.0` → Cua 0.24.0;
  `0.1.1` packaging fix; `0.2.0` → Cua 0.25.x projection).
- Security checklist: no default unrestricted permissions; no Host approval
  bypass; no OS permission bypass; no default use of authenticated browser
  profiles; no automatic screenshot persistence; no secret logging; no
  auto-replay of uncertain mutations; UI content is untrusted data.
- Release 0.1.0 requires the full acceptance list of the design document
  (§72), including real `get_window_state` structured + image content PASS
  and Calculator `6 × 7 = 42` PASS on a Windows environment recorded in
  `verified`.

## 13. Frozen engineering principles

1. Runtime belongs to Cua.
2. Cua behavior belongs to Cua.
3. Portable packaging belongs to computer-use.
4. Authorization belongs to the Host.
5. Desktop security belongs to the OS.
