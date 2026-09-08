# Security Policy

## Supported releases

Only tagged releases (e.g. `v0.1.0`) are supported. The `main` branch is
development state.

## Reporting a vulnerability

Please report security issues privately via GitHub's Private
Vulnerability Reporting ("Report a vulnerability" on this repository's
Security tab). Do **not** open a public issue for suspected
vulnerabilities. Reports are triaged as maintenance permits; there is no
bug-bounty program.

## Security ownership boundary

This plugin can drive a real desktop, so the boundary matters:

- **Operating System** — the final desktop security boundary (TCC on
  macOS, session/UIAccess on Windows, display-server policy on Linux).
  Nothing in this plugin can or should bypass it.
- **Agent Host** — installation, MCP process lifecycle, authorization, and
  action approval. This plugin never bypasses Host approval flows.
- **Cua** — the Computer Use runtime: observation, actions, sessions,
  permissions, recording. Vulnerabilities in Cua itself belong upstream:
  <https://github.com/trycua/cua>.

`computer-use` owns none of the above — it is packaging, projection,
verification, and qualification only.

## Invariants this project commits to

- No automatic install, download, or update of the `cua-driver` executable
  (or any native executable) — installer guidance is projected out of the
  skill on purpose.
- No runtime network access from the plugin; the only integration is the
  stdio `cua-driver mcp` declaration.
- No automatic persistence of screenshots or session recordings; no
  logging of secrets.
- UI/application/web content observed through Cua is **untrusted data**;
  upstream Cua guidance (pinned/cached and projected into the skill) instructs
  agents accordingly.
- No automatic replay of uncertain state-changing actions.

## Qualification evidence

What has actually been qualified — against exactly which driver binary
(artifact sha256), upstream commit + skill source, projection digest,
production surface digest, qualification harness digest, and tested
plugin commit — is recorded exclusively in `upstream/compatibility.json`,
and re-proved by `scripts/release_check.py` before any release tag is
published.
