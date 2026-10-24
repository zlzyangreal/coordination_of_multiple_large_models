# Environment, toolchain, network, permissions

## Capability checklist (gate before routing)

| Item | Check | Record |
|---|---|---|
| Process | target client running (Codex / ChatGPT) | name + PID |
| CLI | absolute path + version | `codex --version` |
| Models | slugs, reasoning levels, quota | see model-registry.md |
| Quota | 5 h remaining %, reset time, weekly | `python scripts/quota.py` (live app-server) |
| Runner | production call path present and working | `scripts/run_gpt.py` |
| Toolchain | python, node, Word/LibreOffice, renderer | absolute paths |
| Network | egress, proxy vars, Clash/TUN | triage below |
| Permissions | sandbox mode, target writable, file locks | read-only / workspace-write / full |
| Skills | available skills/plugins | names |

Run once: `python scripts/check_env.py [--short-call]`. The `--short-call` probe is
`--ephemeral` **by design** (connectivity only, no history). Production and review calls go
through `scripts/run_gpt.py`, which persists; see [calling-gpt.md](calling-gpt.md).

## Toolchain

- Never hardcode `bin/<hash>/codex.exe`; app updates move it. Resolve the newest `%LOCALAPPDATA%\OpenAI\Codex\bin\*\codex.exe` at runtime.
- On ENOENT, re-resolve — do not retry the stale path.
- Use absolute runtime paths for python/node; never rely on PATH.
- A generated `.ps1` must be UTF-8 **with BOM** (or pure ASCII), or PowerShell 5.1 will
  misparse Chinese text and report a misleading syntax error. Prefer `.py`.

## Network and proxy (triage before waiting)

Symptoms: `Reconnecting`, `request timed out`, WebSocket fallback to HTTPS.

Three checks (≤2 min):

1. Reachability: `Test-NetConnection chatgpt.com -Port 443` (direct probes are unreliable behind TUN/proxy — treat as advisory).
2. Proxy: `$env:HTTPS_PROXY` / `$env:HTTP_PROXY`. Clash in system-proxy mode is not inherited by child processes; set the vars explicitly.
3. Short call: `codex exec "ok"` returning within ~60 s.

Decision:

- Any failure → report to the user (which check, paste output); they toggle Clash/TUN. Do not treat it as model slowness.
- All pass but still slow → keep the job alive 10–20 min, inspect logs every 3–5 min, work locally in parallel.
- Never add concurrency to fight a stall. Downgrade only after the wait window with no new logs, and record the real cause.

Observed 2026-10-23: without proxy, identical command looped `Reconnecting 5/5`; with proxy, returned in 7 minutes, zero reconnects.

## Permissions

| Task | Minimum | Note |
|---|---|---|
| Read-only review writing only to `result_path` | read-only + writable result dir | default for reviewers |
| Build, render, write intermediates | workspace-write | default for production |
| Modify config outside workspace / install deps | danger-full-access | user must know |

- A writable task must not run in a read-only sandbox.
- Probe-write the target directory before starting.
- File locks (Word/PDF export) must be released; do not silently retry a write more than twice.
