# Multi-model coordination — DSH plugin + Skill

Turn "how two or more models collaborate" into an executable, auditable process: who owns which
node, which model a node may use, whether a review is truly independent, and when a new package
version is allowed to take effect. The plugin lives in the DSH right sidebar, so the workflow, the
model choice and the version review all happen in the UI.

## What it adds

- **Multi-task workflow board**: every board on one page (status, progress, current owner, blocked
  reason, recent events); incremental SSE behind a revision gate, so an unchanged board never
  spawns python.
- **Model picker + rule gate**: a typable combobox grouped by provider; a review node disables
  same-family / same-model / unknown-family routes inline with the rule number and a one-line
  reason, and `pin`/`assign` are refused server-side with 422 `rule_violation`.
- **Independent review and delivery**: a producer never reaches done on its own result; rework
  attempts, verdicts and the receipt hash chain stay on the board, and delivery binds a resolvable
  commit.
- **40-rule contract gate**, default OFF: ON injects the contract and registers a single
  `coordination` tool; until the session acknowledges it, every other tool call is denied by a
  host-level guard.
- **Version lock with one-click review**: the running version and digest must be user-verified; a
  staged version offers "pass and enable" or "reject", and a rejection records the reason, writes
  an I1 log and keeps the package unverified.
- **Governance and self-bootstrap**: mandate, L1/L2/L3 decisions, review budget, plan gate,
  baseline comparison, candidate promotion and rollback.
- **Declared optional extensions** (installed disabled, activated separately) and local-only
  I0–I3 logs.

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
