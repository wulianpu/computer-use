#!/usr/bin/env python3
"""release_check.py — machine-prove the release evidence chain.

Verification category (design doc v2 §14). Composes the existing
verifiers rather than duplicating their rules:

    verify_projection.run_checks()   <- source/projection/receipt authority
                                        (version, upstreamCommit, skillSource,
                                        projectionMode, projectionDigest,
                                        pluginSurfaceDigest,
                                        qualificationHarnessDigest, ...)
    verify_upstream.run_checks()     <- license/notices/raw hashes

...and adds the release-specific proofs on top:

    release tag exists and points at HEAD
    tag version == plugin.json version, CHANGELOG entry exists
    current pluginSurfaceDigest == projection.json (recomputed)
    production surface unchanged since testedPluginCommit
    qualification harness unchanged since testedPluginCommit
      (so evidence cannot silently outlive a harness edit, even a
       hand-re-signed one)
    receipts cover L2 + L3

Usage:
    uv run python scripts/release_check.py [--tag v0.1.0] [--root PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import project_cua_skill as projection
import verify_projection
import verify_upstream

COMPAT_REL = Path("upstream/compatibility.json")
PROJECTION_REL = Path("upstream/projection.json")
CHANGELOG_REL = Path("CHANGELOG.md")
PRODUCTION_PATHS = ("plugin.json", "mcp.json", "skills/")
HARNESS_PATHS = tuple(str(p) for p in verify_projection.HARNESS_FILES)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=60
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


def run_checks(root: Path, tag: str) -> list[Check]:
    checks: list[Check] = []

    def add(name, ok, detail=""):
        checks.append(Check(name, ok, detail))

    version = tag[1:] if tag.startswith("v") else tag
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        add(f"tag {tag!r} is a version tag", False, f"derived version {version!r}")
        return checks

    # -- tag geometry ----------------------------------------------------------
    try:
        tagged = _git(root, "rev-list", "-n", "1", tag)
        head = _git(root, "rev-parse", "HEAD")
        add(f"tag {tag} exists", True, tagged[:12])
        add("tag points at HEAD", tagged == head, f"{tagged[:12]} vs {head[:12]}")
    except RuntimeError as exc:
        add(f"tag {tag} exists / points at HEAD", False, exc)
        return checks

    # -- manifests + changelog ---------------------------------------------------
    try:
        manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
        add(
            f"plugin.json version == {version}",
            manifest.get("version") == version,
            repr(manifest.get("version")),
        )
    except (OSError, json.JSONDecodeError) as exc:
        add("plugin.json version", False, str(exc))
    changelog = (
        (root / CHANGELOG_REL).read_text(encoding="utf-8")
        if (root / CHANGELOG_REL).is_file()
        else ""
    )
    add(f"CHANGELOG has [{version}] entry", f"## [{version}]" in changelog)

    # -- compose the projection/receipt authority --------------------------------
    try:
        projection_failures = [c.name for c in verify_projection.run_checks(root) if not c.ok]
        add(
            "verify_projection PASS (source/projection/receipt authority)",
            not projection_failures,
            ", ".join(projection_failures)
            if projection_failures
            else "source+projection+surface+harness digests bound",
        )
    except Exception as exc:  # verifier crash is a release failure
        add("verify_projection PASS (source/projection/receipt authority)", False, str(exc))

    # -- compose the upstream/licensing verifier -----------------------------------
    upstream_failures = [c.name for c in verify_upstream.run_checks(root) if not c.ok]
    add(
        "verify_upstream PASS (LICENSE/notices/raw hashes)",
        not upstream_failures,
        ", ".join(upstream_failures) if upstream_failures else "",
    )

    # -- surface digest recomputation -----------------------------------------------
    try:
        surface = projection.plugin_surface_digest(root)
        projection_doc = json.loads((root / PROJECTION_REL).read_text(encoding="utf-8"))
        add(
            "projection.json surface digest == recomputed",
            projection_doc.get("pluginSurfaceDigest") == surface,
            surface,
        )
        compat = json.loads((root / COMPAT_REL).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, projection.ProjectionError) as exc:
        add("digests + receipts loadable", False, str(exc))
        return checks

    # -- evidence ancestry: nothing that produced the evidence may have drifted ----
    receipts = compat.get("verified", [])
    if not receipts:
        add("verified receipt present", False, "compatibility.json has no receipts")
        return checks
    for entry in receipts:
        label = f"receipt {entry.get('platform', '?')} {entry.get('version', '?')}"
        tested = entry.get("testedPluginCommit")
        levels = set(entry.get("levels", []))
        problems = []
        if not {"L2", "L3"} <= levels:
            problems.append("levels")
        if not (isinstance(tested, str) and re.fullmatch(r"[0-9a-f]{40}", tested or "")):
            problems.append("testedPluginCommit invalid")
        else:
            try:
                _git(root, "rev-parse", "--verify", f"{tested}^{{commit}}")
            except RuntimeError as exc:
                problems.append(f"testedPluginCommit unknown ({exc})")
        add(
            f"{label}: L2+L3 testedPluginCommit well-formed",
            not problems,
            ", ".join(problems) if problems else tested[:12],
        )
        # Ancestry proof: an empty tree diff alone does NOT prove "since" —
        # two unrelated histories can carry byte-identical production and
        # harness files. The tested commit must be a real ancestor of HEAD.
        ancestry_ok = False
        if not problems:
            try:
                _git(root, "merge-base", "--is-ancestor", tested, "HEAD")
            except RuntimeError as exc:
                add(
                    f"{label}: testedPluginCommit is an ancestor of the release commit",
                    False,
                    f"diff-emptiness is not lineage: {tested[:12]} is NOT in the "
                    f"release history ({exc})",
                )
            else:
                ancestry_ok = True
                add(
                    f"{label}: testedPluginCommit is an ancestor of the release commit",
                    True,
                    f"{tested[:12]} -> HEAD",
                )
        if not problems and ancestry_ok:
            for scope, paths in (
                ("production surface", PRODUCTION_PATHS),
                ("qualification harness", HARNESS_PATHS),
            ):
                try:
                    changed = _git(root, "diff", "--name-only", f"{tested}..HEAD", "--", *paths)
                    add(
                        f"{label}: {scope} unchanged since tested commit",
                        not changed,
                        f"changed: {changed.splitlines()}" if changed else f"{tested[:12]}..HEAD",
                    )
                except RuntimeError as exc:
                    add(f"{label}: {scope} unchanged since tested commit", False, str(exc))

    return checks


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--tag", default=None, help="release tag to check (default: version from plugin.json)"
    )
    parser.add_argument("--root", default=None, help="repository root")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]

    tag = args.tag
    if tag is None:
        try:
            version = json.loads((root / "plugin.json").read_text(encoding="utf-8"))["version"]
            tag = f"v{version}"
        except (OSError, json.JSONDecodeError, KeyError):
            print("FAIL: cannot derive a default tag — pass --tag explicitly")
            return 1

    checks = run_checks(root, tag)
    width = max(len(c.name) for c in checks)
    failed = 0
    for check in checks:
        status = "PASS" if check.ok else "FAIL"
        line = f"[{status}] {check.name.ljust(width)}"
        if check.detail:
            line += f"  ({check.detail})"
        print(line)
        failed += not check.ok

    if failed:
        print(f"\nNOT READY: {failed} release check(s) failed for {tag}.")
        return 1
    print(
        f"\nRELEASE READY: {tag} satisfies the machine-checkable acceptance "
        "list (DEVELOPMENT_DESIGN §14). Push the tag and publish the GitHub "
        "Release with the CHANGELOG notes."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
