#!/usr/bin/env python3
"""project_cua_skill.py — deterministic projection of the official Cua skill.

Architecture role (design doc v2, §4-§14): the production Agent Skill under
skills/cua-driver/ is NOT authored by this project. It is mechanically
generated from the official Cua Driver skill pack pinned by
upstream/cua.lock.json. Raw upstream bytes live in upstream/source/cua-driver/
(downloaded by sync_cua.py); this tool projects them into the Agent Plugins
skill tree.

Ownership: packaging / projection only. No Computer Use behavior is authored
here — every behavioral sentence comes from upstream except the explicitly
declared, minimal transforms registered in upstream/projection.json:

    normalize-agent-skills-frontmatter   SKILL.md    (standard frontmatter; official
                                        name preserved verbatim; the official
                                        description is transport-normalized and
                                        its original sha256 recorded in metadata)
    insert-generated-notice              SKILL.md    (provenance comment)
    prefer-agent-plugin-mcp-transport    SKILL.md    (fail-closed transport
                                        normalization: (a) the description phrase
                                        "via the cua-driver CLI (default) or MCP
                                        server" becomes "via the Cua Driver MCP
                                        server"; (b) the upstream "GUI transport
                                        defaults" block that mandates CLI-first
                                        is replaced by the Agent Plugin MCP
                                        transport section; (c) a boundary note
                                        under "Using cua-driver from the shell"
                                        scopes that section to hosts that really
                                        provide a shell)
    remove-plugin-side-native-installer-execution  WINDOWS.md
                                        (irm|iex one-liner -> refer to official guide)
    exclude-upstream-pack-readme         README.md   (pack-level doc, not projected)

Everything else is byte-exact. Transforms fail closed: if an expected
upstream text changes, projection FAILs and demands manual review
(instead of silently producing a divergent skill).

Usage:
    python scripts/project_cua_skill.py [--root PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import yaml

UPSTREAM_SOURCE_REL = Path("upstream") / "source" / "cua-driver"
LOCK_REL = Path("upstream") / "cua.lock.json"
PROJECTION_REL = Path("upstream") / "projection.json"
REPORT_REL = Path("upstream") / "projection-report.json"
SKILL_DIR_REL = Path("skills") / "cua-driver"

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
COMPANIONS: tuple[str, ...] = (
    "MACOS.md",
    "WINDOWS.md",
    "LINUX.md",
    "BROWSER.md",
    "RECORDING.md",
    "EMBEDDING.md",
)
EXCLUDED = ("README.md",)

COMPATIBILITY_LINE = (
    "Requires a compatible locally installed cua-driver executable and an "
    "Agent Plugin host with MCP stdio support."
)


class ProjectionError(RuntimeError):
    """Fail-closed projection abort."""


# ------------------------------------------------------------ SKILL.md parts


def _yaml_string(value: str) -> str:
    """Deterministic YAML double-quoted scalar (JSON string)."""
    return json.dumps(value, ensure_ascii=False)


def parse_upstream_frontmatter(text: str) -> tuple[dict, str]:
    """Split '---\n<yaml>\n---\n<body>' and parse the frontmatter mapping."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ProjectionError("upstream SKILL.md does not start with a frontmatter fence")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ProjectionError("upstream SKILL.md frontmatter is unterminated") from None
    try:
        frontmatter = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise ProjectionError(f"upstream SKILL.md frontmatter is not valid YAML: {exc}") from None
    if not isinstance(frontmatter, dict):
        raise ProjectionError("upstream SKILL.md frontmatter is not a mapping")
    body = "\n".join(lines[end + 1 :])
    return frontmatter, body


def generated_notice(lock: dict) -> str:
    return (
        "<!--\n"
        "GENERATED FILE — do not edit manually.\n"
        "\n"
        f"Source:     {lock['repository']}\n"
        f"Version:    {lock['version']}\n"
        f"Tag:        {lock['tag']}\n"
        f"Commit:     {lock['commit']}\n"
        f"Path:       {lock['skillSource']}/SKILL.md\n"
        "Regenerate: scripts/project_cua_skill.py\n"
        "-->\n"
    )


TRANSPORT_SECTION_REPLACEMENT = """## GUI transport in this Agent Plugin

The Agent Plugin exposes Cua Driver through one persistent
`cua-driver mcp` stdio connection.

Calls such as `click(...)`, `get_window_state(...)`, and other Cua
tool names in this skill refer directly to the corresponding MCP tools.

Do not invoke `cua-driver <tool-name> '<JSON-args>'` through a shell
for ordinary tool calls in this Agent Plugin environment.

Lifecycle, session, cursor, recording, browser, authorization, and
verification semantics remain those defined by Cua Driver.
"""

EXPECTED_TRANSPORT_BLOCK = """## GUI transport defaults — prefer cua-driver over GUI shell shims

**Default transport is the `cua-driver` CLI for one-off calls** — `Bash`
shelling out to `cua-driver <tool-name> '<JSON-args>'`. Each CLI invocation
owns a disposable transport session that is cleaned up after its response.
Use one persistent `cua-driver mcp` connection for a multi-call GUI workflow
that needs shared cursor, recording, browser, or named-session state. A public
session label is not a credential and a later one-shot process cannot adopt
the previous process's lifecycle merely by repeating that label.

CLI wins for isolated inspection and management because it picks up rebuilds
instantly, failures are easier to diagnose, and there's no per-tool
schema-load overhead. Persistent MCP wins for an ordered action loop.

Every reference to `click(...)`, `get_window_state(...)` etc. in this
skill means `cua-driver click '{...}'` — translate to MCP form only
when MCP is requested.
"""

SHELL_HEADING = "## Using cua-driver from the shell\n"
SHELL_BOUNDARY_NOTE = """This section applies only when the calling host independently provides
a shell and the workflow actually requires a Cua management command.
Ordinary Cua tool calls in this Agent Plugin use MCP."""

DESCRIPTION_OLD_PHRASE = "via the cua-driver CLI (default) or MCP server"
DESCRIPTION_NEW_PHRASE = "via the Cua Driver MCP server"


def transform_description(description: str) -> str:
    """Transport-normalize the official description (fail closed on drift)."""
    count = description.count(DESCRIPTION_OLD_PHRASE)
    if count != 1:
        raise ProjectionError(
            f"expected description phrase not found exactly once "
            f"(found {count}): {DESCRIPTION_OLD_PHRASE!r}. Upstream "
            "transport wording changed — manual review required "
            "(transform 'prefer-agent-plugin-mcp-transport')."
        )
    return description.replace(DESCRIPTION_OLD_PHRASE, DESCRIPTION_NEW_PHRASE, 1)


def transform_transport_block(body: str) -> str:
    """Replace the upstream CLI-default transport block (fail closed)."""
    count = body.count(EXPECTED_TRANSPORT_BLOCK)
    if count != 1:
        raise ProjectionError(
            "expected transport-defaults block not found exactly once in "
            f"upstream SKILL.md (found {count}). Upstream transport "
            "guidance changed — manual review required (transform "
            "'prefer-agent-plugin-mcp-transport')."
        )
    return body.replace(EXPECTED_TRANSPORT_BLOCK, TRANSPORT_SECTION_REPLACEMENT, 1)


def transform_shell_section(body: str) -> str:
    """Scope the shell/management section to hosts that provide a shell."""
    heading_with_gap = SHELL_HEADING + "\n"
    count = body.count(heading_with_gap)
    if count != 1:
        raise ProjectionError(
            "expected 'Using cua-driver from the shell' heading not found "
            f"exactly once (found {count}). Upstream reorganized the shell "
            "section — manual review required (transform "
            "'prefer-agent-plugin-mcp-transport')."
        )
    return body.replace(
        heading_with_gap, heading_with_gap + SHELL_BOUNDARY_NOTE + "\n\n", 1
    )


def project_frontmatter(frontmatter: dict, lock: dict, description: str,
                        original_description_sha256: str) -> str:
    name = frontmatter.get("name")
    if not isinstance(name, str) or not name:
        raise ProjectionError("upstream SKILL.md frontmatter has no usable 'name'")
    if not isinstance(description, str) or not description:
        raise ProjectionError("upstream SKILL.md frontmatter has no usable 'description'")
    return "\n".join(
        [
            "---",
            f"name: {_yaml_string(name)}",
            f"description: {_yaml_string(description)}",
            "license: MIT",
            f"compatibility: {_yaml_string(COMPATIBILITY_LINE)}",
            "metadata:",
            f"  upstream: {_yaml_string(lock['repository'])}",
            f"  upstream-version: {_yaml_string(lock['version'])}",
            f"  upstream-commit: {_yaml_string(lock['commit'])}",
            f"  upstream-description-sha256: {_yaml_string(original_description_sha256)}",
            '  projection: "agent-plugins"',
            "---",
            "",
        ]
    )


def project_skill_md(upstream_text: str, lock: dict) -> str:
    """Deterministic projection of the official SKILL.md.

    frontmatter -> normalized (name verbatim; description transport-
    normalized, original sha256 preserved in metadata); body -> the
    fail-closed transport transforms below, everything else verbatim.
    """
    frontmatter, body = parse_upstream_frontmatter(upstream_text)
    original_description = frontmatter.get("description")
    if not isinstance(original_description, str) or not original_description:
        raise ProjectionError("upstream SKILL.md frontmatter has no usable 'description'")
    description = transform_description(original_description)
    body = transform_transport_block(body)
    body = transform_shell_section(body)
    body = body.lstrip("\n")
    return "".join(
        [
            project_frontmatter(
                frontmatter,
                lock,
                description,
                sha256_hex(original_description.encode("utf-8")),
            ),
            generated_notice(lock),
            "\n",
            body,
        ]
    )


# --------------------------------------------------------- WINDOWS.md part

INSTALLER_EXPECTED = """   If missing, point the user at:
   ```powershell
   irm https://cua.ai/driver/install.ps1 | iex
   ```
   and stop."""

INSTALLER_REPLACEMENT = """   If missing, refer the user to the official Cua Driver installation
   guide (https://cua.ai/docs/how-to-guides/driver/install) and stop.
   This Agent Plugin never installs or downloads native executables on
   the user's behalf."""


def transform_windows_md(text: str) -> str:
    """Replace the auto-executable installer one-liner (fail closed on drift)."""
    count = text.count(INSTALLER_EXPECTED)
    if count != 1:
        raise ProjectionError(
            "expected installer block not found exactly once in upstream WINDOWS.md "
            f"(found {count} occurrence(s)). Upstream installer guidance changed — "
            "manual review required (transform "
            "'remove-plugin-side-native-installer-execution')."
        )
    return text.replace(INSTALLER_EXPECTED, INSTALLER_REPLACEMENT, 1)


# ------------------------------------------------------------------ pipeline


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def projection_digest(projected: dict[str, bytes]) -> str:
    """SHA256 over the canonical (sorted name, hash) list of the skill tree."""
    canonical = "".join(
        f"{name}:{sha256_hex(projected[name])}\n" for name in sorted(projected)
    )
    return "sha256:" + sha256_hex(canonical.encode("utf-8"))


def load_lock(root: Path) -> dict:
    lock_path = root / LOCK_REL
    if not lock_path.is_file():
        raise ProjectionError(f"{LOCK_REL} not found — run scripts/sync_cua.py first")
    return json.loads(lock_path.read_text(encoding="utf-8"))


def project(root: Path) -> dict:
    lock = load_lock(root)
    source_dir = root / UPSTREAM_SOURCE_REL

    # Offline shape check (fail closed on any source-tree drift, matching
    # sync_cua's online check): an unexpected file in the source cache is
    # manual-review territory, not something to silently skip.
    if source_dir.is_dir():
        actual = sorted(p.name for p in source_dir.iterdir() if p.is_file())
        if actual != sorted(EXPECTED_FILES):
            extra = sorted(set(actual) - set(EXPECTED_FILES))
            missing = sorted(set(EXPECTED_FILES) - set(actual))
            raise ProjectionError(
                "upstream source file set changed. Manual review required. "
                f"extra={extra} missing={missing}"
            )

    raw: dict[str, bytes] = {}
    for name in EXPECTED_FILES:
        path = source_dir / name
        if not path.is_file():
            raise ProjectionError(f"upstream source file missing: {path}")
        raw[name] = path.read_bytes()
        expected_hash = lock.get("files", {}).get(name, {}).get("sha256")
        if expected_hash and sha256_hex(raw[name]) != expected_hash:
            raise ProjectionError(
                f"upstream source hash mismatch for {name} — re-run scripts/sync_cua.py"
            )

    projected: dict[str, bytes] = {}
    transformed: dict[str, list[str]] = {}

    projected["SKILL.md"] = project_skill_md(
        raw["SKILL.md"].decode("utf-8"), lock
    ).encode("utf-8")
    transformed["SKILL.md"] = [
        "normalize-agent-skills-frontmatter",
        "insert-generated-notice",
        "prefer-agent-plugin-mcp-transport",
    ]

    projected["WINDOWS.md"] = transform_windows_md(
        raw["WINDOWS.md"].decode("utf-8")
    ).encode("utf-8")
    transformed["WINDOWS.md"] = ["remove-plugin-side-native-installer-execution"]

    for name in COMPANIONS:
        if name in projected:
            continue
        projected[name] = raw[name]  # byte-exact
    unchanged = [name for name in COMPANIONS if name not in transformed]

    # Write the skill tree; the expected tree is flat (top-level files
    # only), so anything else — stale files or the old references/ model —
    # is removed.
    skill_dir = root / SKILL_DIR_REL
    skill_dir.mkdir(parents=True, exist_ok=True)
    for entry in sorted(skill_dir.iterdir()):
        if entry.is_file() and entry.name in projected:
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
            print(f"  removed stale skill dir: {entry.name}/")
        else:
            entry.unlink()
            print(f"  removed stale skill file: {entry.name}")
    for name, data in projected.items():
        (skill_dir / name).write_bytes(data)

    digest = projection_digest(projected)
    projection = {
        "schemaVersion": 1,
        "mode": "raw-skill-normalized",
        "source": {
            "repository": lock["repository"],
            "version": lock["version"],
            "tag": lock["tag"],
            "commit": lock["commit"],
            "skillSource": lock["skillSource"],
        },
        "projectionDigest": digest,
        "transforms": [
            {"id": transform_id, "file": file}
            for file, transform_ids in transformed.items()
            for transform_id in transform_ids
        ]
        + [{"id": "exclude-upstream-pack-readme", "file": name} for name in EXCLUDED],
    }
    save_json(root / PROJECTION_REL, projection)

    report = {
        "version": lock["version"],
        "sourceCommit": lock["commit"],
        "unchanged": sorted(unchanged),
        "transformed": {k: v for k, v in sorted(transformed.items())},
        "excluded": list(EXCLUDED),
        "projectionDigest": digest,
    }
    save_json(root / REPORT_REL, report)
    return report


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None, help="repository root")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]

    report = project(root)
    print(
        f"projection complete: {report['version']} @ {report['sourceCommit'][:12]} -> "
        f"{SKILL_DIR_REL.as_posix()}/ ({len(report['unchanged'])} byte-exact, "
        f"{len(report['transformed'])} transformed, {len(report['excluded'])} excluded)"
    )
    print(f"projection digest: {report['projectionDigest']}")
    print("next: python scripts/verify_projection.py && python scripts/validate_plugin.py")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ProjectionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
