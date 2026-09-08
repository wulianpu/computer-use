#!/usr/bin/env python3
"""verify_upstream.py — offline verification of the pinned upstream source.

Runs with NO network access. The production skill tree is verified by
verify_projection.py; this tool verifies the raw source cache and its
integrity metadata (design doc v2, §22):

    upstream/cua.lock.json exists and is well-formed
    expected files exist under upstream/source/cua-driver/
    no unexpected source files
    sha256 of every raw source file matches the lock
    upstream license exists
    third-party notice matches the lock

Usage:
    python scripts/verify_upstream.py [--root PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

SOURCE_REL = Path("upstream/source/cua-driver")
LOCK_REL = Path("upstream/cua.lock.json")
LICENSE_REL = Path("licenses/CUA-LICENSE.md")
NOTICES_REL = Path("THIRD_PARTY_NOTICES.md")

LOCK_REQUIRED_KEYS = ("repository", "version", "tag", "commit", "skillSource", "files")
MIT_MARKERS = ("MIT License", "Permission is hereby granted")

EXPECTED_FILES: tuple[str, ...] = (
    "SKILL.md",
    "MACOS.md",
    "WINDOWS.md",
    "LINUX.md",
    "BROWSER.md",
    "RECORDING.md",
    "EMBEDDING.md",
    "README.md",
)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_checks(root: Path) -> list[Check]:
    checks: list[Check] = []
    lock_path = root / LOCK_REL
    source_dir = root / SOURCE_REL

    # -- lock exists / well-formed ------------------------------------------
    lock = None
    if not lock_path.exists():
        checks.append(Check("lock exists", False, str(LOCK_REL)))
    else:
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            missing_keys = [k for k in LOCK_REQUIRED_KEYS if k not in lock]
            checks.append(
                Check("lock well-formed", not missing_keys, f"missing keys: {missing_keys}")
            )
        except json.JSONDecodeError as exc:
            checks.append(Check("lock well-formed", False, f"invalid JSON: {exc}"))

    # -- expected source files -----------------------------------------------
    for name in EXPECTED_FILES:
        ok = (source_dir / name).is_file()
        checks.append(Check(f"source file exists: {name}", ok, str(source_dir / name)))

    # -- no unexpected source files -------------------------------------------
    if source_dir.is_dir():
        actual = sorted(p.name for p in source_dir.iterdir() if p.is_file())
    else:
        actual = []
    extras = [n for n in actual if n not in EXPECTED_FILES]
    checks.append(
        Check(
            "no unexpected source files",
            not extras,
            f"unexpected: {extras}" if extras else f"{len(actual)} files",
        )
    )

    # -- sha256 matches lock ----------------------------------------------------
    if lock and isinstance(lock.get("files"), dict):
        locked: dict = lock["files"]
        checks.append(
            Check(
                "lock file set == expected set",
                sorted(locked) == sorted(EXPECTED_FILES),
                f"lock has {sorted(locked)}",
            )
        )
        for name in EXPECTED_FILES:
            path = source_dir / name
            if not path.is_file() or name not in locked:
                checks.append(Check(f"sha256 matches lock: {name}", False, "missing"))
                continue
            digest = _sha256(path)
            expected_hash = locked[name].get("sha256", "")
            checks.append(
                Check(
                    f"sha256 matches lock: {name}",
                    digest == expected_hash,
                    "" if digest == expected_hash else f"{digest} != {expected_hash}",
                )
            )
    else:
        checks.append(Check("sha256 matches lock", False, "lock unavailable"))

    # -- license -------------------------------------------------------------------
    license_path = root / LICENSE_REL
    license_ok = license_path.is_file()
    detail = ""
    if license_ok:
        text = license_path.read_text(encoding="utf-8", errors="replace")
        if not any(marker in text for marker in MIT_MARKERS):
            license_ok = False
            detail = "no MIT markers found"
    checks.append(Check("upstream license exists", license_ok, detail or str(LICENSE_REL)))

    # -- third-party notice -----------------------------------------------------------
    notices_path = root / NOTICES_REL
    if lock and notices_path.is_file():
        notices = notices_path.read_text(encoding="utf-8")
        required_tokens = (
            lock.get("repository", ""),
            lock.get("version", ""),
            lock.get("tag", ""),
            lock.get("commit", ""),
            lock.get("skillSource", ""),
            "MIT",
        )
        missing = [t for t in required_tokens if t and t not in notices]
        checks.append(
            Check(
                "third-party notice matches lock",
                not missing,
                f"missing tokens: {missing}" if missing else "all lock tokens present",
            )
        )
    else:
        checks.append(Check("third-party notice matches lock", False, str(NOTICES_REL)))

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
            f"\nFAIL: {failed} check(s) failed. upstream/source/ is generated "
            "material — regenerate with scripts/sync_cua.py instead of editing."
        )
        return 1
    print(f"\nAll {len(checks)} upstream verification checks passed (offline).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
