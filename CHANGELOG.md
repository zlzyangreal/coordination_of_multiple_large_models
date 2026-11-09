# Changelog

## 3.0.0 — 2026-11-09

DSH-native bundle on top of the v2.4.0 engine.

- Standard bundle: `package.json` (`dsh.bundle`, `dsh.client`), `cordis.patch.yml`, host and client
  entries; `engines.dsh` is exactly `0.2.1-rc.3`.
- One global switch, default **OFF**; ON injects a 40-rule contract and registers one
  `coordination` tool. The monotonic guard denies every other tool until the session calls
  `coordination({action:"contract"})`; an invalid contract keeps a deny guard and logs I0.
- Version lock: running version **and** package digest must match the user-verified values;
  otherwise the plugin starts `pending_verification` (no contract, no tool, switch disabled).
  First install is pending too. A new version is staged in a separate verification profile.
- Optional extensions are declaration-driven; install is confirmed and **disabled**, activation
  needs a reported version/source plus a declared probe. `dsh-our-free-model` is optional and is
  neither bundled nor installed by default.
- Local-only logs: daily `runtime_error_*.jsonl` / `optimization_*.jsonl`, I0–I3, dedupe/index,
  redaction, rotation/retention, fallback; OFF collects only minimal lifecycle faults. No
  automatic repair, config write, upload or git inclusion.
- Python core retained through `ctx.subprocess`; `core_unavailable` is I0 and fail-closed.
- 331 Python offline cases still pass; 15 new Node tests cover state, logs, extensions, guard,
  bundle metadata and 40-rule parity.
- Supported platform: **DSH 0.2.1-rc.3 Web profile, Windows 11 x64**. Desktop is unverified.
