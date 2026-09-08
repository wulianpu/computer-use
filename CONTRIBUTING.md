# Contributing

Contributions are welcome — but this repository is deliberately thin, and
most of its quality lives in what it *refuses* to contain. Before opening
a PR, read `docs/DEVELOPMENT_DESIGN.md` (the authoritative implementation
design).

## The one rule that matters most

> If a piece of production Computer Use behavior is authored in
> `computer-use` instead of coming from Cua, the architecture has probably
> drifted.

Every change must fit one of five categories:

```text
packaging
projection
upstream synchronization
verification
qualification
```

If it doesn't, it likely belongs in Cua, the Agent Host, MCP, or the OS —
not here.

## Generated files are never hand-edited

The following are generated material. Change the generator or the upstream
pin, then regenerate — never edit the output:

```text
upstream/source/cua-driver/        (scripts/sync_cua.py)
skills/cua-driver/                 (scripts/project_cua_skill.py)
upstream/cua.lock.json             (scripts/sync_cua.py)
upstream/projection.json           (scripts/project_cua_skill.py)
upstream/projection-report.json    (scripts/project_cua_skill.py)
licenses/CUA-LICENSE.md            (scripts/sync_cua.py)
THIRD_PARTY_NOTICES.md             (scripts/sync_cua.py)
tests/contract/cua-tools.snapshot.json  (scripts/mcp_probe.py --snapshot)
```

`verify_upstream.py` / `verify_projection.py` fail on any manual edit.

## Cua upgrades are always manual

Automated upstream bumps and auto-merge are forbidden — a Cua release
changes the runtime, the tool contract, the agent guidance, and desktop
security behavior simultaneously. Follow `docs/UPGRADING_CUA.md` exactly.

## Running Level 1 (no Cua, no desktop)

```bash
uv sync --locked --extra dev
uv run ruff check scripts tests && uv run ruff format --check scripts tests
uv run python scripts/validate_plugin.py
uv run python scripts/verify_upstream.py
uv run python scripts/verify_projection.py
uv run python -m pytest -v
```

This is what CI runs; it must be green before review.

## When L2/L3 qualification is required

Any change that touches the pinned source, the projection output, the
production surface (`plugin.json`, `mcp.json`, `skills/`), or the
qualification tooling invalidates the receipts in
`upstream/compatibility.json` (the digests won't match). Re-run, on a real
machine:

```bash
python scripts/mcp_probe.py        # L2
python scripts/e2e_calculator.py --yes   # L3 (drives a real desktop)
```

…then re-sign the receipt (bind: version, upstreamCommit, skillSource,
projectionMode, projectionDigest, pluginSurfaceDigest, pluginCommit,
driver artifact sha256) and finish with `python scripts/release_check.py`.

## Releases

`scripts/release_check.py` must print `RELEASE READY` before tagging. The
acceptance list it (partially) machine-proves is
`docs/DEVELOPMENT_DESIGN.md §14`.
