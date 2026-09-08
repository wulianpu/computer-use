# Development Design (computer-use v0.1)

> Authoritative full design (Chinese): `docs/archive/INITIAL_DESIGN.zh-CN.md`
> (the original frozen design document, archived verbatim). This file is the
> English implementation-facing summary; when the two disagree, the archived
> full design document wins.

## 1. Project definition

`computer-use` is an independent, host-neutral, Agent Plugins 1.0.0
**distribution layer** for Cua Driver Computer Use. It does **not**
implement a Computer Use Runtime. It projects:

```text
Cua Driver MCP + official Cua Agent Skill guidance
        ↓
Agent Plugins 1.0.0 portable package
```

Final flow: Agent Plugins Host → plugin.json → (Skill → thin adapter →
official Cua guidance) + (mcp.json → `cua-driver mcp` → Cua Driver →
Desktop) → Agent → Cua MCP tools → Desktop.

## 2. Ownership boundary (the core architecture rule)

| Owner | Responsibility |
| --- | --- |
| Agent Plugins | plugin package format |
| Agent Skills | SKILL.md format |
| MCP | agent ↔ server protocol |
| Cua | Computer Use Runtime: observation, actions, session, window targeting, browser, platform semantics, permissions, recording |
| **computer-use** | **packaging, upstream sync, verification, qualification** |
| Agent Host | installation, MCP lifecycle, agent execution, authorization, approval |
| Operating System | the final desktop security boundary |

Every piece of self-maintained code must belong to one of the four allowed
categories (packaging / upstream sync / verification / qualification). If it
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

## 6. Skill strategy: conformant thin skill + official mirror

Upstream Cua's own `SKILL.md` frontmatter is not strictly conforming Agent
Skills metadata, so it cannot be shipped as the plugin skill directly.
Strategy:

```text
skills/cua-driver/SKILL.md          thin, conforming (transport adaptation)
skills/cua-driver/references/upstream/*   byte-exact official Cua skill pack
```

Hosts only discover skills in direct subdirectories of `skills/`, so the
nested upstream `SKILL.md` is reference material, not a second skill.

Priority rule: **Cua semantics** (snapshot, element_token, browser,
delivery, platform, recording) belong to `references/upstream/*`;
**plugin transport semantics** belong to the thin `SKILL.md`. Where
upstream guidance demonstrates a `cua-driver` CLI call, the thin skill
directs the agent to invoke the corresponding Cua MCP tool instead of
requiring a shell — transport adaptation, not a behavior fork.

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

`scripts/sync_cua.py --tag cua-driver-rs-v0.24.0` resolves the tag to a
commit, verifies the skill-pack file set (fail closed on any shape drift —
a new upstream file such as a future `HISTORY.md` aborts with "manual
review required"), downloads the exact-commit files byte-for-byte, saves
the upstream license to `licenses/CUA-LICENSE.md`, writes the lock,
updates `compatibility.json`'s candidate and the thin skill's version
metadata, and regenerates `THIRD_PARTY_NOTICES.md`. A `skillSource` change
requires `--allow-source-path-change` (manual review; never a silent
fallback). The mirror is marked `linguist-generated` via `.gitattributes`
and `-text` so checkout never rewrites the bytes the hashes cover.

`scripts/verify_upstream.py` re-checks everything offline: file set, no
extras, sha256 match, thin-skill/lock version agreement, license presence,
notice/lock agreement. Any manual edit of generated files FAILS.

## 8. Validation levels

| Level | Name | Requires | Proves |
| --- | --- | --- | --- |
| 1 | Portable | nothing | structure, manifests, thin skill, hashes, unit + fake-MCP tests (CI) |
| 2 | Cua MCP contract | real `cua-driver` | version, `cua-driver mcp`, handshake, tools/list, required subset (`required ⊆ actual`; count is never a contract) |
| 3 | Desktop qualification | real OS + GUI + Cua | observation, screenshot/image content, accessibility, semantic action, mutation verification |
| 4 | Supported | concrete version+platform receipts in `compatibility.json` | support matrix entry |

Version mismatch (e.g. guidance 0.24.0 vs runtime 0.23.2) is WARN /
qualification failure — never a runtime refusal; the plugin runtime has no
version-check wrapper.

## 9. Tooling inventory

| Script | Category | Notes |
| --- | --- | --- |
| `scripts/sync_cua.py` | upstream sync | fail-closed mirror generator (GitHub API with git/raw fallbacks; the git fallback fetches by the resolved commit SHA, never HEAD) |
| `scripts/verify_upstream.py` | verification | offline, no network |
| `scripts/validate_plugin.py` | verification | pinned official schemas + project policy, offline |
| `scripts/mcp_client.py` | verification | **TEST / VALIDATION ONLY** minimal MCP stdio client |
| `scripts/mcp_probe.py` | qualification (L2) | handshake, tools/list, required subset, optional `--snapshot` contract snapshot for upgrade diffs |
| `scripts/e2e_calculator.py` | qualification (L3) | Calculator `6 × 7 = 42`, semantic-only, dry-run unless `--yes`; proves launch ownership and closes only what it launched |

Validation model: the **official Agent Plugins schemas, pinned as local
copies under `schemas/agent-plugins/1.0.0/`, are the authority** for
plugin.json / mcp.json field sets (validated with `jsonschema`, never
re-implemented by hand). `validate_plugin.py` adds only project policy
(name/server identity, direct `cua-driver mcp` invocation, no wrappers, no
bundled runtime, thin-skill conformance). skills-ref (Agent Skills
reference implementation) is demonstration software; it is not a CI gate
here — the deterministic validators are, and a skills-ref cross-check can
be run manually where available.

Qualification receipts in `upstream/compatibility.json` are bound to
`(version, upstreamCommit, skillSource)` — a same-version re-pin
invalidates them — and record the plugin commit plus the exact driver
artifact digest the evidence was produced against.

Toolchain: Python ≥3.12, pytest/jsonschema/PyYAML as *development*
dependencies only. The plugin runtime has no Python, no Node, no custom
process.

## 10. Qualification posture

- First supported platform: **Windows** (Windows 11, x86_64, interactive
  desktop, exact candidate Cua version; Calculator, Notepad, Chrome, an
  Electron app; DPI 100% and 150/200%; background action + foreground
  fallback; multi-monitor).
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
`verify_upstream.py` → thin-skill compatibility review → plugin/skill
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

Related upstream work: trycua/cua#2994 (Agent Plugins v1 package proposal —
closure is a no-op, merge starts an equivalence review) and trycua/cua#3387
(the maintainer's cross-marketplace portable Skill projection — if upstream
ships an official portable skill path such as
`libs/cua-driver/plugins/cua-driver/skills/cua-driver`, evaluate migrating
from the raw canonical-skill mirror to that projection to shrink the thin
adapter). This project depends on neither.

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
