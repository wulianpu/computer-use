# Upgrading Cua

The upgrade path is fixed and manual — automated upgrade bots and
auto-merge are forbidden because a Cua release simultaneously changes the
runtime, the Agent Tool contract, the agent behavior documentation, and
desktop security behavior.

## Fixed sequence

```text
1.  discover new release (exact tag)
2.  update candidate only (upstream/compatibility.json)
3.  python scripts/sync_cua.py --tag cua-driver-rs-vX.Y.Z
4.  check source-path / file-set drift reported by the sync (fail-closed)
5.  review the official skill diff under skills/cua-driver/references/upstream/
6.  python scripts/verify_upstream.py
7.  thin Skill compatibility review (frontmatter version, guidance wording)
8.  python scripts/validate_plugin.py
9.  python scripts/mcp_probe.py --snapshot
10. review tool/schema diff of tests/contract/cua-tools.snapshot.json
11. real desktop qualification (mcp_probe + e2e_calculator --yes)
12. update `verified` in upstream/compatibility.json with receipts
13. release a new computer-use version
```

Versioning stays independent: plugin `0.1.0` → Cua 0.24.0; plugin `0.1.1`
→ packaging fix; plugin `0.2.0` → Cua 0.25.x projection.

## Upgrade checklist

- exact tag confirmed
- exact commit confirmed (lock records it)
- skill source path confirmed (a change needs `--allow-source-path-change`
  and an explicit review — never a silent fallback)
- skill file set confirmed (new files abort the sync on purpose)
- official skill diff reviewed
- upstream license confirmed
- all hashes regenerated
- thin skill metadata synchronized (`upstream-version`, `compatibility`)
- Agent Skills validation PASS (`validate_plugin.py`, optional skills-ref)
- MCP handshake PASS (`mcp_probe.py`)
- required tools PASS
- schema diff reviewed — look at semantics, not names:
  `element_token`, window targeting, `delivery_mode`, coordinates,
  `structuredContent`, image content, session lifecycle, desktop and
  browser semantics
- image content PASS
- desktop E2E PASS (Calculator `6 × 7 = 42`, semantic-only)
- platform support receipt updated

## Sync details

- `sync_cua.py` pins repository + version + tag + immutable commit +
  source path + per-file sha256. It never rewrites mirrored Markdown; the
  mirror is byte-equivalent upstream content.
- The upstream file set must match exactly. If upstream adds a file (e.g. a
  future `HISTORY.md`) the sync fails with
  *"Upstream skill-pack shape changed. Manual review required."* — decide
  deliberately, then update `EXPECTED_FILES` in both `sync_cua.py` and
  `verify_upstream.py` together.
- GitHub API rate limits are bypassed via git fallbacks (`ls-remote`,
  blobless partial clone) and raw file downloads; everything still pins
  the exact immutable commit. Set `--github-token` (or run
  `sync_cua.py --github-token …`) when the API is available and you want
  primary-resolution.
- After any sync, `compatibility.json`'s `verified` entries belonging to
  the previous candidate version are dropped automatically — re-qualify.

## Official plugin detection (every upgrade)

Check whether the upstream release already ships its own Agent Plugin:

```text
plugin.json
mcp.json
skills/cua-driver/
```

If it does, trigger **UPSTREAM_MIGRATION_REVIEW** instead of releasing
another version of this projection unconditionally. Migration criteria:
Agent Plugins conformance, Agent Skills conformance, Cua runtime
compatibility, the platform behavior we need, and stable release
artifacts. When the official plugin qualifies, this project moves to
maintenance mode ("Use the official Cua Agent Plugin") and stops forking —
that is the intended long-term exit.

Related upstream work: trycua/cua#2994. This project does not depend on
that PR; if it closes, nothing changes here; if it merges, an upstream
equivalence review begins.
