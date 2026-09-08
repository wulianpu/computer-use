#!/usr/bin/env python3
"""verify_upstream.py — offline verification of the upstream mirror.

Runs with NO network access. Verifies (design doc §30):

    lock exists and is well-formed
    expected files exist under skills/cua-driver/references/upstream/
    no unexpected mirrored files
    sha256 of every mirrored file matches the lock
    thin Skill version matches the lock version
    upstream license exists
    third-party notice matches the lock

Any manual modification of generated upstream files must FAIL.

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

MIRROR_REL = Path("skills/cua-driver/references/upstream")
LOCK_REL = Path("upstream/cua.lock.json")
SKILL_REL = Path("skills/cua-driver/SKILL.md")
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


def _parse_frontmatter(text: str):
    """Return (frontmatter_dict, error) for a SKILL.md file, without yaml deps."""
    if not text.startswith("---"):
        return None, "missing frontmatter fence"
    lines = text.splitlines()
    if len(lines) < 2:
        return None, "empty frontmatter"
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None, "unterminated frontmatter"
    fm: dict[str, object] = {}
    current_key: str | None = None
    for line in lines[1:end]:
        if not line.strip():
            continue
        if line[:1] not in (" ", "\t"):  # top-level key
            key, sep, value = line.partition(":")
            if not sep:
                return None, f"malformed frontmatter line: {line!r}"
            current_key = key.strip()
            value = value.strip()
            if value == "":
                fm[current_key] = {}
            else:
                fm[current_key] = value.strip('"')
        elif current_key is not None:  # nested metadata entry
            key, sep, value = line.strip().partition(":")
            if not sep:
                return None, f"malformed nested line: {line!r}"
            nested = fm[current_key]
            if not isinstance(nested, dict):
                return None, f"nested entry under non-mapping {current_key!r}"
            nested[key.strip()] = value.strip().strip('"')
    return fm, None


def run_checks(root: Path) -> list[Check]:
    checks: list[Check] = []
    lock_path = root / LOCK_REL
    mirror_dir = root / MIRROR_REL

    # -- lock exists / well-formed -----------------------------------------
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

    # -- expected files exist ------------------------------------------------
    for name in EXPECTED_FILES:
        ok = (mirror_dir / name).is_file()
        checks.append(Check(f"mirror file exists: {name}", ok, str(mirror_dir / name)))

    # -- no unexpected mirrored files ---------------------------------------
    if mirror_dir.is_dir():
        actual = sorted(p.name for p in mirror_dir.iterdir() if p.is_file())
    else:
        actual = []
    extras = [n for n in actual if n not in EXPECTED_FILES]
    checks.append(
        Check(
            "no unexpected mirrored files",
            not extras,
            f"unexpected: {extras}" if extras else f"{len(actual)} files",
        )
    )

    # -- sha256 matches lock ---------------------------------------------------
    if lock and isinstance(lock.get("files"), dict):
        locked: dict = lock["files"]
        lock_names = sorted(locked)
        expected_sorted = sorted(EXPECTED_FILES)
        checks.append(
            Check(
                "lock file set == expected set",
                lock_names == expected_sorted,
                f"lock has {lock_names}",
            )
        )
        for name in EXPECTED_FILES:
            path = mirror_dir / name
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

    # -- thin Skill version matches lock version -------------------------------
    skill_path = root / SKILL_REL
    if lock and skill_path.is_file():
        fm, err = _parse_frontmatter(skill_path.read_text(encoding="utf-8"))
        if err or not isinstance(fm, dict):
            checks.append(Check("thin Skill version matches lock", False, err or "parse error"))
        else:
            metadata = fm.get("metadata")
            upstream_version = (
                metadata.get("upstream-version") if isinstance(metadata, dict) else None
            )
            ok = upstream_version == lock.get("version")
            checks.append(
                Check(
                    "thin Skill version matches lock",
                    ok,
                    f"skill upstream-version={upstream_version!r} lock={lock.get('version')!r}",
                )
            )
    else:
        checks.append(Check("thin Skill version matches lock", False, "skill or lock missing"))

    # -- license exists ----------------------------------------------------------
    license_path = root / LICENSE_REL
    license_ok = license_path.is_file()
    detail = ""
    if license_ok:
        text = license_path.read_text(encoding="utf-8", errors="replace")
        if not any(marker in text for marker in MIT_MARKERS):
            license_ok = False
            detail = "no MIT markers found"
    checks.append(Check("upstream license exists", license_ok, detail or str(LICENSE_REL)))

    # -- third-party notice matches lock -------------------------------------
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
        if not check.ok and check.detail:
            line += f"  ({check.detail})"
        elif check.ok and check.detail:
            line += f"  ({check.detail})"
        print(line)
        failed += not check.ok

    if failed:
        print(f"\nFAIL: {failed} check(s) failed. Mirrored upstream files are "
            "generated material — regenerate with scripts/sync_cua.py instead of editing.")
        return 1
    print(f"\nAll {len(checks)} upstream verification checks passed (offline).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
