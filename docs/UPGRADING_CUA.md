# Upgrading Cua

The upgrade path is fixed and manual — automated upgrade bots and
auto-merge are forbidden because a Cua release simultaneously changes the
runtime, the Agent Tool contract, the agent behavior documentation, and
desktop security behavior.

## Fixed sequence

```text
1.  discover new release (exact tag)
2.  python scripts/sync_cua.py --tag cua-driver-rs-vX.Y.Z
      (downloads + pins; fail-closed on file-set / source-path drift)
3.  python scripts/project_cua_skill.py
      (fail-closed on transform drift: e.g. the Windows installer block
       changed upstream -> manual review required)
4.  review the projection diff via upstream/projection-report.json +
      git diff of upstream/source/ and skills/cua-driver/
5.  python scripts/verify_upstream.py
6.  python scripts/verify_projection.py
      (invalidated receipts fail here until re-qualification)
7.  python scripts/validate_plugin.py
8.  python scripts/mcp_probe.py --snapshot
9.  review the tool/schema diff of tests/contract/cua-tools.snapshot.json
10. real desktop qualification: python scripts/e2e_calculator.py --yes
11. write a fresh receipt in upstream/compatibility.json
      (version, tag, upstreamCommit, skillSource, projectionMode,
       projectionDigest, pluginCommit, driver artifact sha256)
12. release a new computer-use version
```

Versioning stays independent: plugin `0.1.0` → Cua 0.24.0; plugin `0.1.1`
→ packaging fix; plugin `0.2.0` → Cua 0.25.x projection.

## Upgrade checklist

- exact tag and commit confirmed (lock records it)
- skill source path confirmed (a change needs `--allow-source-path-change`
  and an explicit review — never a silent fallback)
- skill file set confirmed (new upstream files abort the sync on purpose;
  update `EXPECTED_FILES` in sync + projection + verifiers together after
  review)
- official skill diff reviewed (raw: `upstream/source/`)
- projection diff reviewed (`upstream/projection-report.json`: unchanged /
  transformed / excluded)
- every transform still applies or was consciously updated (fail-closed)
- upstream license confirmed; all hashes regenerated
- projection verification PASS (regeneration equality, digest recorded)
- Agent Skills validation PASS (`validate_plugin.py`)
- MCP handshake + required tools PASS (`mcp_probe.py`)
- schema diff reviewed — semantics, not names: `element_token`, window
  targeting, `delivery_mode`, coordinates, `structuredContent`, image
  content, session lifecycle, desktop and browser semantics
- image content PASS; desktop E2E PASS (Calculator `6 × 7 = 42`,
  semantic-only, ownership-proven cleanup)
- fresh qualification receipt bound to the new projection digest

## Official portable projection detection (every upgrade — trycua/cua#3387)

Check whether the release ships an official portable skill path, e.g.:

```text
libs/cua-driver/plugins/cua-driver/skills/cua-driver
```

If it does, trigger **UPSTREAM_PORTABLE_PROJECTION_REVIEW**: evaluate
switching `projectionMode` from `raw-skill-normalized` to
`upstream-portable` — consuming the official portable skill directly, with
zero (or only explicitly declared, near-zero) transforms. Target: the
local transform set shrinks toward nothing.

## Official plugin detection (every upgrade — trycua/cua#2994)

Check whether the release ships its own Agent Plugin:

```text
plugin.json
mcp.json
skills/cua-driver/
```

If it does, trigger **UPSTREAM_PLUGIN_MIGRATION_REVIEW** instead of
releasing another projection unconditionally. Migration criteria: Agent
Plugins conformance, Agent Skills conformance, Cua runtime compatibility,
the platform behavior we need, and stable release artifacts. When the
official plugin qualifies, this project moves to maintenance mode ("Use
the official Cua Agent Plugin") and stops forking — that is the intended
long-term exit.
