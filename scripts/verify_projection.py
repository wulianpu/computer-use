#!/usr/bin/env python3
"""verify_projection.py — prove the production skill is a correct projection.

Offline, deterministic (design doc v2, §26). Checks:

    1. raw upstream source (upstream/source/cua-driver/) hashes == lock
    2. skill tree (skills/cua-driver/) == deterministic projection of the
       raw source — verified by REGENERATING the projection in memory and
       comparing bytes, so no unexplained diff can exist
    3. the skill tree contains exactly the expected projected file set
    4. upstream/projection.json matches the lock source and the recomputed
       projection digest
    5. mode 'upstream-portable' requires zero content transforms
    6. every verified receipt in upstream/compatibility.json still matches
       the pinned source and the current projection digest (§31);
       an invalidated receipt FAILs until re-qualification

Usage:
    python scripts/verify_projection.py [--root PATH]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import project_cua_skill as projection

LOCK_REL = Path("upstream/cua.lock.json")
PROJECTION_REL = Path("upstream/projection.json")
REPORT_REL = Path("upstream/projection-report.json")
COMPAT_REL = Path("upstream/compatibility.json")
SOURCE_REL = Path("upstream/source/cua-driver")
SKILL_DIR_REL = Path("skills/cua-driver")

CONTENT_TRANSFORM_IDS = {
    "normalize-agent-skills-frontmatter",
    "insert-generated-notice",
    "prefer-agent-plugin-mcp-transport",
    "exclude-host-specific-mcp-setup-guidance",
    "remove-plugin-side-native-installer-execution",
}
PROJECTED_FILES = ("SKILL.md", *projection.COMPANIONS)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def run_checks(root: Path) -> list[Check]:
    checks: list[Check] = []

    def add(name, ok, detail=""):
        checks.append(Check(name, ok, detail))

    # -- lock + raw source hashes ------------------------------------------
    lock = None
    if not (root / LOCK_REL).is_file():
        add("lock exists", False, str(LOCK_REL))
    else:
        try:
            lock = _load_json(root / LOCK_REL)
            add("lock exists", True)
        except json.JSONDecodeError as exc:
            add("lock exists", False, f"invalid JSON: {exc}")

    source_dir = root / SOURCE_REL
    if lock and source_dir.is_dir():
        actual = sorted(p.name for p in source_dir.iterdir() if p.is_file())
        add(
            "raw source file set == lock file set",
            actual == sorted(projection.EXPECTED_FILES),
            f"source has {actual}",
        )
        for name in projection.EXPECTED_FILES:
            path = source_dir / name
            expected_hash = lock.get("files", {}).get(name, {}).get("sha256", "")
            ok = (
                path.is_file()
                and expected_hash
                and (projection.sha256_hex(path.read_bytes()) == expected_hash)
            )
            add(f"raw source hash == lock: {name}", ok, "" if ok else "mismatch/missing")
    else:
        add("raw source present", False, str(SOURCE_REL))

    # -- regenerate the projection in memory and compare bytes -------------
    projected_ok = True
    try:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            # Regenerate from the real root into a scratch root that shares
            # the same lock + source, then byte-compare the skill trees.
            scratch = Path(tmp)
            (scratch / SOURCE_REL).mkdir(parents=True)
            for name in projection.EXPECTED_FILES:
                (scratch / SOURCE_REL / name).write_bytes((source_dir / name).read_bytes())
            for rel in ("plugin.json", "mcp.json"):
                shutil.copy(root / rel, scratch / rel)
            (scratch / "upstream").mkdir(parents=True, exist_ok=True)
            (scratch / LOCK_REL).write_text(
                (root / LOCK_REL).read_text(encoding="utf-8"), encoding="utf-8"
            )
            report = projection.project(scratch)
            for name in PROJECTED_FILES:
                expected_bytes = (scratch / SKILL_DIR_REL / name).read_bytes()
                actual_path = root / SKILL_DIR_REL / name
                same = actual_path.is_file() and actual_path.read_bytes() == expected_bytes
                add(f"projected file matches regeneration: {name}", same)
                projected_ok &= same
        digest = report["projectionDigest"]
        add("projection regenerates deterministically", True, digest)
    except projection.ProjectionError as exc:
        add("projection regenerates deterministically", False, str(exc))
        digest = None

    # -- skill tree file set ------------------------------------------------
    skill_dir = root / SKILL_DIR_REL
    if skill_dir.is_dir():
        tree = sorted(str(p.relative_to(skill_dir)) for p in skill_dir.rglob("*") if p.is_file())
        add(
            "skill tree == expected projected file set",
            tree == sorted(PROJECTED_FILES),
            f"tree has {tree}",
        )
        legacy = sorted(str(p.relative_to(root)) for p in (root / "skills").rglob("references"))
        add("no legacy references/ model under skills/", not legacy, f"found: {legacy}")
    else:
        add("skill tree exists", False, str(SKILL_DIR_REL))

    # -- projection.json ------------------------------------------------------
    if (root / PROJECTION_REL).is_file() and lock:
        try:
            proj = _load_json(root / PROJECTION_REL)
            source_ok = proj.get("source", {}) == {
                "repository": lock["repository"],
                "version": lock["version"],
                "tag": lock["tag"],
                "commit": lock["commit"],
                "skillSource": lock["skillSource"],
            }
            add("projection.json source == lock", source_ok)
            mode = proj.get("mode")
            add(
                "projection mode valid",
                mode in ("raw-skill-normalized", "upstream-portable"),
                repr(mode),
            )
            transforms = proj.get("transforms", [])
            transform_ids = {t.get("id") for t in transforms}
            if mode == "upstream-portable":
                content_transforms = transform_ids & CONTENT_TRANSFORM_IDS
                add(
                    "upstream-portable mode has zero content transforms",
                    not content_transforms,
                    f"unexpected: {sorted(content_transforms)}",
                )
            digest_ok = digest is not None and proj.get("projectionDigest") == digest
            add(
                "projection.json digest == recomputed digest",
                digest_ok,
                f"{proj.get('projectionDigest')} vs {digest}",
            )
            try:
                surface = projection.plugin_surface_digest(root)
            except projection.ProjectionError as exc:
                surface = None
                add("plugin surface digest computable", False, str(exc))
            else:
                surface_ok = proj.get("pluginSurfaceDigest") == surface
                add(
                    "projection.json surface digest == recomputed surface digest",
                    surface_ok,
                    f"{proj.get('pluginSurfaceDigest')} vs {surface}",
                )
            report_disk = _load_json(root / REPORT_REL) if (root / REPORT_REL).is_file() else None
            content_files = {t["file"] for t in transforms if t["id"] in CONTENT_TRANSFORM_IDS}
            add(
                "projection-report.json matches projection.json",
                report_disk is not None
                and report_disk.get("projectionDigest") == proj.get("projectionDigest")
                and set(report_disk.get("transformed", {})) == content_files,
            )
        except json.JSONDecodeError as exc:
            add("projection.json parses", False, str(exc))
    else:
        add("projection.json exists", False, str(PROJECTION_REL))

    # -- qualification receipts (§31 invalidation rule) ----------------------
    if (root / COMPAT_REL).is_file() and lock:
        try:
            compat = _load_json(root / COMPAT_REL)
            proj_mode = None
            proj_surface = None
            if (root / PROJECTION_REL).is_file():
                proj_doc = _load_json(root / PROJECTION_REL)
                proj_mode = proj_doc.get("mode")
                proj_surface = proj_doc.get("pluginSurfaceDigest")
            for entry in compat.get("verified", []):
                label = f"receipt valid: {entry.get('platform', '?')} {entry.get('version', '?')}"
                mismatches = []
                if entry.get("version") != lock["version"]:
                    mismatches.append("version")
                if entry.get("upstreamCommit") != lock["commit"]:
                    mismatches.append("upstreamCommit")
                if entry.get("skillSource", lock["skillSource"]) != lock["skillSource"]:
                    mismatches.append("skillSource")
                if digest is not None and entry.get("projectionDigest") != digest:
                    mismatches.append("projectionDigest")
                if proj_mode is not None and entry.get("projectionMode") != proj_mode:
                    mismatches.append("projectionMode")
                if proj_surface is not None and entry.get("pluginSurfaceDigest") != proj_surface:
                    mismatches.append("pluginSurfaceDigest")
                add(
                    label,
                    not mismatches,
                    "invalidated (re-qualify): " + ", ".join(mismatches)
                    if mismatches
                    else "matches pinned source + projection + surface",
                )
        except json.JSONDecodeError as exc:
            add("compatibility.json parses", False, str(exc))
    else:
        add("compatibility.json exists", False, str(COMPAT_REL))

    return checks


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None, help="repository root")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]

    checks = run_checks(root)
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
        print(
            f"\nFAIL: {failed} projection check(s) failed. skills/cua-driver/ and "
            "upstream/source/ are generated — regenerate with "
            "scripts/project_cua_skill.py (after scripts/sync_cua.py) instead of "
            "editing; invalidated receipts require re-running L2/L3 qualification."
        )
        return 1
    print(f"\nAll {len(checks)} projection verification checks passed (offline).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
