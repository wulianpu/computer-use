#!/usr/bin/env python3
"""sync_cua.py — mirror the official Cua Driver skill pack at an exact tag/commit.

This is an upstream-sync development tool. It belongs to one of the four
allowed self-maintained categories: packaging / upstream sync / verification /
qualification. It is NOT part of the plugin runtime surface.

Responsibilities (design doc §27):
    resolve tag -> immutable commit SHA
    discover the skill-pack directory and check its file set (fail closed)
    download exact-commit files (byte-exact, never rewritten)
    compute sha256 hashes
    save upstream references under skills/cua-driver/references/upstream/
    save the upstream license under licenses/CUA-LICENSE.md
    generate upstream/cua.lock.json
    update candidate metadata (compatibility.json + thin Skill version)

Fail-closed rules:
    - The upstream skill-pack file set must match EXPECTED_FILES exactly.
      Any addition/removal is an error requiring manual review (§28).
    - A skillSource change requires --allow-source-path-change (§26).
    - Mirror files are written byte-for-byte; no Markdown rewriting (§29).

Usage:
    python scripts/sync_cua.py --tag cua-driver-rs-v0.24.0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------- constants

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

DEFAULT_REPO = "trycua/cua"
DEFAULT_SOURCE_PATH = "libs/cua-driver/rust/Skills/cua-driver"
LICENSE_CANDIDATES: tuple[str, ...] = (
    "LICENSE.md",
    "LICENSE",
    "LICENSE.txt",
    "COPYING",
    "COPYING.md",
)
MIT_MARKERS = ("MIT License", "Permission is hereby granted")

MIRROR_REL = Path("skills/cua-driver/references/upstream")
LOCK_REL = Path("upstream/cua.lock.json")
COMPAT_REL = Path("upstream/compatibility.json")
NOTICES_REL = Path("THIRD_PARTY_NOTICES.md")
SKILL_REL = Path("skills/cua-driver/SKILL.md")
LICENSE_REL = Path("licenses/CUA-LICENSE.md")

GIT_TIMEOUT = 300
HTTP_TIMEOUT = 60


class SyncError(RuntimeError):
    """Fail-closed sync abort."""


# ------------------------------------------------------------ pure helpers


def version_from_tag(tag: str) -> str:
    """Extract the X.Y.Z version from a release tag like cua-driver-rs-v0.24.0."""
    match = re.search(r"v(\d+\.\d+\.\d+)$", tag)
    if not match:
        raise SyncError(
            f"Cannot derive a X.Y.Z version from tag {tag!r}; pass --version explicitly."
        )
    return match.group(1)


def check_shape(actual_names) -> set[str]:
    """Fail closed when the upstream skill-pack shape changes (§28).

    New upstream files (e.g. a future HISTORY.md) or missing files must abort
    the sync for manual review; they must never be silently ignored.
    """
    actual = {name for name in actual_names if not name.startswith(".")}
    expected = set(EXPECTED_FILES)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        raise SyncError(
            "Upstream skill-pack shape changed. Manual review required. "
            f"missing={missing} extra={extra}"
        )
    return actual


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_lock(
    repository: str,
    version: str,
    tag: str,
    commit: str,
    skill_source: str,
    hashes: dict[str, str],
) -> dict:
    return {
        "repository": repository,
        "version": version,
        "tag": tag,
        "commit": commit,
        "skillSource": skill_source,
        "files": {name: {"sha256": hashes[name]} for name in EXPECTED_FILES},
    }


def render_third_party_notices(lock: dict) -> str:
    return f"""# Third-Party Notices

This project mirrors generated upstream material. The files under
`skills/cua-driver/references/upstream/` and `licenses/CUA-LICENSE.md` are
produced by `scripts/sync_cua.py` and must not be edited by hand.

## Cua Driver — trycua/cua

- Upstream repository: https://github.com/{lock['repository']}
- Component: Cua Driver Agent Skill pack + repository license
- Version: {lock['version']}
- Tag: {lock['tag']}
- Commit: {lock['commit']}
- Source path in upstream repository: {lock['skillSource']}
- License: MIT — © Cua AI (full text in `licenses/CUA-LICENSE.md`)

The mirrored skill files are distributed under the upstream MIT license with
attribution preserved. See `upstream/cua.lock.json` for the pinned hashes.
"""


COMPATIBILITY_LINE = (
    "compatibility: Requires a compatible cua-driver installation and an "
    "Agent Plugin host with MCP stdio support. Upstream guidance is qualified "
    "against Cua Driver {version}."
)


def update_thin_skill(skill_path: Path, version: str) -> None:
    """Synchronize the thin Skill frontmatter with the synced upstream version."""
    text = skill_path.read_text(encoding="utf-8")
    text, n_compat = re.subn(
        r"^compatibility: .*$",
        lambda _m: COMPATIBILITY_LINE.format(version=version),
        text,
        count=1,
        flags=re.MULTILINE,
    )
    text, n_ver = re.subn(
        r'^(\s+upstream-version:\s*)"[^"]*"$',
        rf'\1"{version}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n_compat != 1 or n_ver != 1:
        raise SyncError(
            "Thin SKILL.md frontmatter does not contain the expected "
            "'compatibility:' / 'upstream-version:' lines; manual update needed."
        )
    skill_path.write_text(text, encoding="utf-8", newline="\n")


def update_compatibility(path: Path, candidate: dict) -> None:
    """Update the candidate block; drop 'verified' entries from other versions."""
    compat = json.loads(path.read_text(encoding="utf-8"))
    compat["candidate"] = candidate
    kept = []
    for entry in compat.get("verified", []):
        if entry.get("version") != candidate["version"]:
            print(
                f"  note: dropping verified entry for {entry.get('version')!r} "
                "(belongs to a previous candidate; re-qualify after upgrade)"
            )
        else:
            kept.append(entry)
    compat["verified"] = kept
    save_json(path, compat)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


# ---------------------------------------------------------- network layer


class Upstream:
    """GitHub access with API-first and git/raw fallback.

    The anonymous GitHub REST API is heavily rate-limited; when it is
    unavailable we fall back to `git ls-remote` (tag resolution), a blobless
    partial clone (directory shape) and raw.githubusercontent.com (file
    bodies). All fallbacks still operate on the exact immutable commit.
    """

    def __init__(self, repository: str, token: str | None = None) -> None:
        self.repository = repository
        self.repo_url = f"https://github.com/{repository}"
        self.api_base = f"https://api.github.com/repos/{repository}"
        self.raw_base = f"https://raw.githubusercontent.com/{repository}"
        self.headers = {
            "User-Agent": "computer-use-sync/0.1 (+development tool)",
            "Accept": "application/vnd.github+json",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    # -- low level ---------------------------------------------------------

    def _http(self, url: str, binary: bool = False, retries: int = 2):
        last_error: Exception | None = None
        for _ in range(retries + 1):
            try:
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                    data = resp.read()
                return data if binary else data.decode("utf-8")
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                last_error = exc
        raise SyncError(f"Request failed after retries: {url} ({last_error})")

    def _git(self, *args: str, cwd: str | Path | None = None) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
        if proc.returncode != 0:
            raise SyncError(f"git {' '.join(args[:3])} failed: {proc.stderr.strip()}")
        return proc.stdout

    # -- tag resolution ------------------------------------------------------

    def resolve_tag(self, tag: str) -> str:
        try:
            payload = json.loads(
                self._http(f"{self.api_base}/commits/{urllib.parse.quote(tag)}")
            )
            return payload["sha"]
        except SyncError:
            pass  # fall through to git
        out = self._git(
            "ls-remote", self.repo_url, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"
        )
        peeled = direct = ""
        for line in out.splitlines():
            sha, _, ref = line.partition("\t")
            if ref == f"refs/tags/{tag}^{{}}":
                peeled = sha
            elif ref == f"refs/tags/{tag}":
                direct = sha
        commit = peeled or direct  # prefer the peeled commit of annotated tags
        if not commit:
            raise SyncError(f"Tag {tag!r} not found in {self.repository}.")
        return commit

    # -- directory shape -----------------------------------------------------

    def list_dir(self, commit: str, path: str) -> list[str]:
        try:
            payload = json.loads(
                self._http(f"{self.api_base}/contents/{path}?ref={commit}")
            )
            if not isinstance(payload, list):
                raise SyncError(f"Unexpected contents payload for {path}")
            return [item["name"] for item in payload if item.get("type") == "file"]
        except SyncError:
            pass  # fall through to git
        tmp = Path(tempfile.mkdtemp(prefix="cua-sync-"))
        try:
            self._git(
                "clone",
                "--quiet",
                "--depth",
                "1",
                "--filter=blob:none",
                "--no-checkout",
                self.repo_url,
                str(tmp),
            )
            out = self._git("-C", str(tmp), "ls-tree", "-r", "--name-only", "HEAD", "--", path)
            names = [line.rsplit("/", 1)[-1] for line in out.splitlines() if line.strip()]
            return [name for name in names if name != path.rsplit("/", 1)[-1]]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # -- file bodies -----------------------------------------------------------

    def fetch_file(self, commit: str, path: str) -> bytes:
        return self._http(f"{self.raw_base}/{commit}/{path}", binary=True)

    def fetch_license(self, commit: str) -> tuple[str, bytes]:
        for name in LICENSE_CANDIDATES:
            try:
                data = self.fetch_file(commit, name)
                if data:
                    return name, data
            except SyncError:
                continue
        raise SyncError(
            f"No license file found at {self.repository}@{commit} "
            f"(tried: {', '.join(LICENSE_CANDIDATES)})."
        )


# ------------------------------------------------------------------- main


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", required=True, help="exact upstream release tag")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="upstream repository (owner/name)")
    parser.add_argument(
        "--version",
        default=None,
        help="X.Y.Z version; derived from the tag when omitted",
    )
    parser.add_argument(
        "--source-path",
        default=None,
        help=(
            "skill pack directory inside the repository; defaults to the "
            "existing lock's skillSource, then to the built-in default"
        ),
    )
    parser.add_argument(
        "--allow-source-path-change",
        action="store_true",
        help="acknowledge a skillSource change that differs from the existing lock (manual review)",
    )
    parser.add_argument(
        "--commit",
        default=None,
        help="expected immutable commit SHA (cross-checked against the resolved tag)",
    )
    parser.add_argument(
        "--no-verify-tag",
        action="store_true",
        help="skip tag resolution and trust --commit when the GitHub API and git are both unavailable",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="repository root (defaults to the parent of scripts/)",
    )
    parser.add_argument("--github-token", default=None, help="optional GitHub API token")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]

    existing_lock: dict | None = None
    if (root / LOCK_REL).exists():
        existing_lock = load_json(root / LOCK_REL)

    # --- source path policy (§26): never silently fall back to a new path ---
    source_path = args.source_path
    if source_path is None:
        source_path = (
            existing_lock["skillSource"] if existing_lock else DEFAULT_SOURCE_PATH
        )
    if existing_lock and source_path != existing_lock["skillSource"]:
        if not args.allow_source_path_change:
            raise SyncError(
                f"skillSource changed: {existing_lock['skillSource']!r} -> "
                f"{source_path!r}. Manual review required; re-run with "
                "--allow-source-path-change after reviewing the change."
            )
        print(f"  source path change acknowledged: {source_path}")

    version = args.version or version_from_tag(args.tag)

    upstream = Upstream(args.repo, args.github_token)

    # --- resolve tag -> immutable commit (§24) ---
    commit = ""
    try:
        commit = upstream.resolve_tag(args.tag)
    except SyncError as exc:
        if args.commit and args.no_verify_tag:
            print(f"  WARNING: tag resolution unavailable ({exc}); trusting --commit")
            commit = args.commit
        else:
            raise
    if args.commit and args.commit.lower() != commit.lower():
        raise SyncError(
            f"--commit {args.commit} does not match tag {args.tag} "
            f"(resolved to {commit})."
        )
    print(f"resolved {args.tag} -> {commit}")

    # --- fail-closed shape check (§28) ---
    names = upstream.list_dir(commit, source_path)
    check_shape(names)
    print(f"skill-pack shape OK at {source_path} ({len(EXPECTED_FILES)} files)")

    # --- byte-exact download (§29) ---
    hashes: dict[str, str] = {}
    payloads: dict[str, bytes] = {}
    for name in EXPECTED_FILES:
        data = upstream.fetch_file(commit, f"{source_path}/{name}")
        payloads[name] = data
        hashes[name] = sha256_hex(data)
        print(f"  fetched {name} ({len(data)} bytes, sha256 {hashes[name][:12]}…)")

    license_name, license_bytes = upstream.fetch_license(commit)
    if not any(marker in license_bytes.decode("utf-8", "replace") for marker in MIT_MARKERS):
        raise SyncError(
            f"Upstream license {license_name} does not look like an MIT license; "
            "attribution policy requires manual review."
        )

    # --- write mirror (generated material; reset to the expected file set) ---
    mirror_dir = root / MIRROR_REL
    mirror_dir.mkdir(parents=True, exist_ok=True)
    for name in EXPECTED_FILES:
        (mirror_dir / name).write_bytes(payloads[name])
    for stale in sorted(p.name for p in mirror_dir.iterdir() if p.name not in EXPECTED_FILES):
        (mirror_dir / stale).unlink()
        print(f"  removed stale mirror file: {stale}")

    (root / LICENSE_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / LICENSE_REL).write_bytes(license_bytes)

    lock = build_lock(args.repo, version, args.tag, commit, source_path, hashes)
    save_json(root / LOCK_REL, lock)
    (root / NOTICES_REL).write_text(
        render_third_party_notices(lock), encoding="utf-8", newline="\n"
    )
    update_compatibility(root / COMPAT_REL, {"version": version, "tag": args.tag})
    update_thin_skill(root / SKILL_REL, version)

    print(
        f"sync complete: {args.repo} {version} ({args.tag} @ {commit[:12]}) -> "
        f"{MIRROR_REL.as_posix()}"
    )
    print("next: python scripts/verify_upstream.py && python scripts/validate_plugin.py")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SyncError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
