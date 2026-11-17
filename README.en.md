# Multi-model coordination — DSH plugin + Skill

Turn "how two or more models collaborate" into an executable, auditable process: who owns which
node, which model a node may use, whether a review is truly independent, and when a new package
version is allowed to take effect. The plugin lives in the DSH right sidebar, so the workflow, the
model choice and the version review all happen in the UI.

- Repository: <https://github.com/zlzyangreal/coordination_of_multiple_large_models>
- Version: 3.2.0 (2026-11-17)
- Offline self-test: 373 checks (mock providers, no network); Node tests: 94
- Previous generation: `多模型协作Spec.md` v2.4.1, frozen and kept for reference only

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

## Workflow board

One board per task lives in `<workspace>/_work/orchestration/<task>.json`. The lead drives it with
the `coordination` tool:

```
coordination({ action: "workflow", op: "init",  task: "api", title: "API 重构" })
coordination({ action: "workflow", op: "plan",  task: "api", nodes_file: "nodes.json", expected_revision: 1 })
coordination({ action: "workflow", op: "start", task: "api", node: "n1", expected_revision: 2 })
```

The panel reads it through four endpoints under `/_multimodel`:

| Endpoint | Purpose |
|---|---|
| `GET /workflow/boards` | index of every board in the workspace, active first |
| `GET /workflow/events?tasks=all` | one SSE stream, frames `{boards, tasks}`, only changed boards |
| `GET /workflow/tasks?ids=a,b` | polling fallback with the same shape |
| `GET /workflow/options?task=&node=` | per-route `eligible / code / rule / reason` for that node |
| `POST /workflow/pin` | assign a model to a node (`expected_revision` guards races) |

Boards are independent: each task keeps its own receipt stream, so advancing one board never makes
another board's chain look stale. The board root is the workspace from the host's workspace
registry (falling back to the process directory); every workflow route also accepts `?project=`.

## Model rules

A model is not "just a name": for a review node the server evaluates independence before anything
is written.

1. Family resolution: `models.config.json` `overrides.<model>.family` beats the shipped
   `assets/families.json` rules; when neither resolves it, the family is **unknown**.
2. A review node then rejects, in order: no producer model yet, unknown family (rule 19, "unknown
   is never independent"), the producer's exact model (rule 2), the producer's family (rule 19),
   a channel the team scan declined (rule 20).
3. The UI shows every rejected route greyed out with its reason; the same verdict is enforced for
   `pin` and `assign`, so bypassing the UI does not bypass the rule.

## Version review

After an update the plugin refuses to inject the contract until a human verifies the running
version and digest. The console offers one click either way:

- **Pass and enable** — runs the checks, stores `verification{decision, by, note, version,
  digest, at}`, bumps the contract epoch and enables the switch.
- **Reject** — records the reason, writes an I1 `verify.rejected` log, keeps the package pending
  and leaves the constrained mode off. A rejection is cleared only by a later pass.

## Install

In the DSH desktop app the package is installed as a profile bundle (the app manages the desktop
profile itself); a standalone verification profile can be used from the CLI:

```sh
dsh plugin --profile coordination-verify add <path-to-this-package>
dsh --profile coordination-verify --port 0 --no-open
```

Open the printed URL, go to **Settings → Multi-model coordination**, press **pass and enable**
once, and only then turn the switch ON. See `docs/install.md` and `docs/workflow.md`.

Supported platform: **DSH 0.2.1-rc.3, Web profile and desktop, Windows 11 x64**. The desktop app
was verified end to end for 3.2.0 (restart, one-click review, workspace board).

## Sync

```sh
git pull --rebase
git add -A && git commit -m "..." && git push
```

On this machine the network proxy is `http://127.0.0.1:7897`; unset `http.proxy` when Clash is not
running. A fresh machine: `git clone https://github.com/zlzyangreal/coordination_of_multiple_large_models.git`.

## Scripts

- `scripts/check_env.py`
- `scripts/session.py`
- `scripts/run_gpt.py`
- `scripts/authority.py`
- `scripts/plan_gate.py`
- `scripts/review_budget.py`
- `scripts/facts_pack.py`
- `scripts/test_evidence.py`
- `scripts/check_docs.py`
- `scripts/check_rules.py`
- `scripts/release.py`
- `scripts/gate_common.py`
- `scripts/models.py`
- `scripts/model_registry.py`
- `scripts/model_config.py`
- `scripts/model_http.py`
- `scripts/model_route.py`
- `scripts/quota.py`
- `scripts/validate_result.py`
- `scripts/schema_for_codex.py`
- `scripts/reap_turns.py`
- `scripts/build_envelope.py`
- `scripts/iteration.py`
- `scripts/orchestration.py`
- `scripts/families.py`
- `scripts/eligibility.py`
- `scripts/selftest_models.py`

The full command table is in `SKILL.md`.

## Version history

| Version | Date | Change |
|---|---|---|
| 3.2.0 | 2026-11-17 | Multi-task workflow panel on one page, typable model picker, rule-disabled options with reasons, one-click version review; 94 Node tests |
| 3.1.0 | 2026-11-17 | Executable collaboration board (`orchestration.py`, T1–T11, I1–I8), live DAG, per-node model pin, receipt chain; 78 Node tests |
| 3.0.0 | 2026-11-10 | DSH-native bundle: default-OFF switch, 40-rule contract gate, version lock with verified activation, declared optional extensions, local I0–I3 logs |
| 2.4.0 | 2026-11-03 | Human-governed decision authority: mandate, L1/L2/L3, scoped grants, execution guards, decision records |
| 2.3.0 | 2026-11-02 | Self-bootstrap iteration: stable drives a candidate worktree, baseline comparison, user approval, snapshot promotion and rollback |
| 2.2.0 | 2026-10-29 | Executable gates: plan review, review budget, test evidence, facts pack version, workspace identity |
| 2.1.0 | 2026-10-27 | Plan and dispatch split: planner chosen by Reasoning/Design/Agentic/Coding scores, plan reviewed before dispatch |
| 2.0.0 | 2026-10-27 | Multi-provider model access (`models.py` family), team admission, protected lanes, per-project credentials |

## Boundary

Enforcement covers the plugin's own system-prompt injection, `coordination` tool/guard, human-only
install/verify routes, Python gate calls and logs. Host tools outside the plugin, direct
shell/network, other plugins' private paths and model reasoning are outside it. No host-wide
interception, OS sandbox or official certification is claimed.

MIT licensed. The Python engine, references and schemas retain the project's v2.4.0 provenance;
third-party plugins are never bundled.
