# Multi-model coordination — DSH plugin

This repository is both the v3.0.0 Skill and an installable DeepSeek Harness bundle.

## What it adds

- One global switch, default **OFF**; OFF leaves DSH native, ON injects the 40-rule contract and
  registers a single `coordination` tool. Until the session acknowledges the contract, every other
  tool call is denied by a host-level guard.
- Version lock: the running package version **and** digest must be verified by the user before the
  switch can be enabled; a new version is staged in a separate verification profile and never
  silently takes over.
- A settings page listing declared optional extensions. Installation is user-confirmed and
  disabled; activation requires a reported identity plus a declared probe. `dsh-our-free-model`
  is optional and is not installed by default.
- Local-only I0–I3 logs under `$DSH_HOME/coordination/`; no uploads, no automatic repair, no git
  inclusion.

## Install

```sh
dsh plugin --profile coordination-verify add <path-to-this-package>
dsh --profile coordination-verify --port 0 --no-open
```

Then open the printed URL, Settings → 多模型协作, **verify this version**, **confirm activation**,
and only then turn the switch ON. See `docs/install.md`.

Supported platform for v3.0.0: **DSH 0.2.1-rc.3 Web profile, Windows 11 x64**. Desktop is expected
to work but is **unverified**; see `docs/desktop.md`.

## Boundary

Enforcement covers the plugin's own system-prompt injection, `coordination` tool/guard, human-only
install/verify routes, Python gate calls and logs. Host tools outside the plugin, direct
shell/network, other plugins' private paths and model reasoning are outside it. No host-wide
interception, OS sandbox or official certification is claimed.

MIT licensed. The Python engine, references and schemas retain the project's v2.4.0 provenance;
third-party plugins are never bundled.
