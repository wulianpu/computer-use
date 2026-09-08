#!/usr/bin/env python3
"""sync_cua.py — download and pin the official Cua Driver skill pack.

This is an upstream-sync development tool. It belongs to one of the five
allowed self-maintained categories: packaging / projection / upstream
synchronization / verification /
qualification. It is NOT part of the plugin runtime surface.

Scope (design doc v2, §22): sync downloads and pins ONLY. It does not touch
the production skill tree (that is project_cua_skill.py's deterministic
projection) and does not touch compatibility receipts (qualification owns
those; verify_projection.py checks receipt validity).

Responsibilities:
    resolve tag -> immutable commit SHA
    discover the skill-pack directory and check its file set (fail closed)
    download exact-commit files (byte-exact, never rewritten)
    compute sha256 hashes
    save the raw source cache under upstream/source/cua-driver/
    save the upstream license under licenses/CUA-LICENSE.md
    generate upstream/cua.lock.json and THIRD_PARTY_NOTICES.md

Fail-closed rules:
    - The upstream skill-pack file set must match EXPECTED_FILES exactly.
      Any addition/removal is an error requiring manual review (§28).
    - A skillSource change requires --allow-source-path-change (§26).
    - Files are written byte-for-byte; no Markdown rewriting (§29).

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

SOURCE_REL = Path("upstream/source/cua-driver")
LOCK_REL = Path("upstream/cua.lock.json")
NOTICES_REL = Path("THIRD_PARTY_NOTICES.md")
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


def check_shape(entries) -> set[str]:
    """Fail closed on any upstream shape drift (§28).

    `entries` is the NON-RECURSIVE listing of the skill-pack directory:
    (name, kind) pairs with kind in {"file", "dir"}. The pack must contain
    exactly the expected files and ZERO directories — a new upstream
    directory (e.g. a future `references/`) is shape drift requiring manual
    review, never something to silently ignore, and neither is a
    file-becomes-directory change.
    """
    dirs = sorted(name for name, kind in entries if kind == "dir")
    files = {name for name, kind in entries if kind == "file" and not name.startswith(".")}
    expected = set(EXPECTED_FILES)
    missing = sorted(expected - files)
    extra = sorted(files - expected)
    if missing or extra or dirs:
        raise SyncError(
            "Upstream skill-pack shape changed. Manual review required. "
            f"missing={missing} extra={extra} directories={dirs}"
        )
    return files


def _parse_ls_tree(out: str, path: str) -> list[tuple[str, str]]:
    """Parse non-recursive `git ls-tree <commit> -- <path>/` output into
    (relative_name, "file"|"dir") entries."""
    entries: list[tuple[str, str]] = []
    prefix = path.rstrip("/") + "/"
    for line in out.splitlines():
        if not line.strip():
            continue
        meta, _, obj_path = line.partition("\t")
        parts = meta.split()
        if len(parts) < 2 or not obj_path:
            continue
        if not obj_path.startswith(prefix) or obj_path.rstrip("/") == path.rstrip("/"):
            continue  # the directory row itself or unrelated rows
        kind = parts[1]  # "blob" or "tree"
        entries.append((obj_path[len(prefix) :], "dir" if kind == "tree" else "file"))
    return entries


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

This project distributes generated upstream material — none of it is
hand-edited.

- Raw upstream source: `upstream/source/cua-driver/` — synchronized by
  `scripts/sync_cua.py`
- Production projected skill: `skills/cua-driver/` — generated by
  `scripts/project_cua_skill.py` (deterministic, minimal, registered
  transforms only; see `upstream/projection.json`)

## Cua Driver — trycua/cua

- Upstream repository: https://github.com/{lock["repository"]}
- Component: Cua Driver Agent Skill pack + repository license
- Version: {lock["version"]}
- Tag: {lock["tag"]}
- Commit: {lock["commit"]}
- Source path in upstream repository: {lock["skillSource"]}
- License: MIT — © Cua AI (full text in `licenses/CUA-LICENSE.md`)

The raw source and its projected skill are distributed under the upstream
MIT license with attribution preserved. See `upstream/cua.lock.json` for
the pinned hashes and `upstream/projection-report.json` for the transform
summary.
"""


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
            payload = json.loads(self._http(f"{self.api_base}/commits/{urllib.parse.quote(tag)}"))
            return payload["sha"]
        except SyncError:
            pass  # fall through to git
        out = self._git("ls-remote", self.repo_url, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}")
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

    def list_dir(self, commit: str, path: str) -> list[tuple[str, str]]:
        """Non-recursive listing of `path` at `commit` as (name, kind) pairs.

        Directories are reported, not filtered away: check_shape treats any
        directory as shape drift. The git fallback fetches BY THE RESOLVED
        COMMIT SHA — never HEAD, a branch, or the tag (which could move) —
        and uses a non-recursive ls-tree so nested files cannot hide inside
        new subdirectories.
        """
        try:
            payload = json.loads(self._http(f"{self.api_base}/contents/{path}?ref={commit}"))
            if not isinstance(payload, list):
                raise SyncError(f"Unexpected contents payload for {path}")
            return [
                (item["name"], "dir" if item.get("type") == "dir" else "file") for item in payload
            ]
        except SyncError:
            pass  # fall through to git
        tmp = Path(tempfile.mkdtemp(prefix="cua-sync-"))
        try:
            self._git("init", "--quiet", str(tmp))
            self._git("-C", str(tmp), "remote", "add", "origin", self.repo_url)
            self._git(
                "-C",
                str(tmp),
                "fetch",
                "--quiet",
                "--depth",
                "1",
                "--filter=blob:none",
                "origin",
                commit,
            )
            out = self._git("-C", str(tmp), "ls-tree", commit, "--", f"{path.rstrip('/')}/")
            return _parse_ls_tree(out, path)
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
        source_path = existing_lock["skillSource"] if existing_lock else DEFAULT_SOURCE_PATH
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
            f"--commit {args.commit} does not match tag {args.tag} (resolved to {commit})."
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

    # --- write raw source cache (generated material; reset to the expected
    #     file set). The skill tree itself is produced later by
    #     project_cua_skill.py — sync only downloads and pins. ---
    source_dir = root / SOURCE_REL
    source_dir.mkdir(parents=True, exist_ok=True)
    for name in EXPECTED_FILES:
        (source_dir / name).write_bytes(payloads[name])
    for stale in sorted(p.name for p in source_dir.iterdir() if p.name not in EXPECTED_FILES):
        (source_dir / stale).unlink()
        print(f"  removed stale source file: {stale}")

    (root / LICENSE_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / LICENSE_REL).write_bytes(license_bytes)

    lock = build_lock(args.repo, version, args.tag, commit, source_path, hashes)
    save_json(root / LOCK_REL, lock)
    (root / NOTICES_REL).write_text(
        render_third_party_notices(lock), encoding="utf-8", newline="\n"
    )

    print(
        f"sync complete: {args.repo} {version} ({args.tag} @ {commit[:12]}) -> "
        f"{SOURCE_REL.as_posix()}"
    )
    print("next: python scripts/project_cua_skill.py")
    print("then: python scripts/verify_upstream.py && python scripts/verify_projection.py")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SyncError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
