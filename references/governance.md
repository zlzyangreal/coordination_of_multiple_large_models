# Decision authority: human goals, agent autonomy, enforced boundaries

This reference is the detail behind `SKILL.md` rules 31–38 and workflow step 0/3/5. It adds no new
policy: if the two disagree, `SKILL.md` wins.

The human owns the **ends** — goal, hard constraints, authorization, acceptance. The agent owns the
**means** — research, planning, design, selection, scheduling, implementation. The orchestrator
enforces the boundary at its controlled paths, records every decision, and says what it cannot
enforce. None of this is prompt-only: `scripts/authority.py` is the execution point.

## When governance is on

- Disabled by default. `python scripts/authority.py init --goal ...` creates
  `_work/governance/` and turns it on. Without a mandate, every integration behaves exactly as
  v2.3.0.
- `authority.py mode off --by user --reason ... --approval-ref ... --safe-stop pause|abort|complete`
  suspends the *negotiation* mode, not the L1 rule: goal, constraint, scope, release and acceptance
  still need a recorded user decision. `pause` stops new writable operations; `resume` continues.
- A missing/corrupt state, or a deleted governance directory while the pin still says enabled, is
  **exit 3** (fail closed), never "disabled".
- Only the user changes the mode. `--by user` is an attestation, not proof of identity: without a
  host secret the record says `unauthenticated`. Set `DSH_GOVERNANCE_SECRET` and pass
  `--approval-token <HMAC-SHA256(secret, approval file)>` to mark it `authenticated`.

## State

```text
_work/governance/
├── ENABLED                 sentinel written once by init
├── mandate.json            goal, ideas, hard constraints, preferences, scope, budgets, hash
├── control.json            enabled / paused / mode / mandate hash / safe_stop
├── decisions.jsonl         hash-chained propose/decide/outcome records
├── grants.jsonl            hash-chained grant/revoke records
├── usage.jsonl             atomic call reservations charged to the grant chain
├── agents.json             registered agents and plugins
├── requests/<Q>.json       one human decision request; requests.md is derived
├── checkpoint.json         phase and the state it depended on
└── approvals/<id>.md       copied verbatim user approval (sha256 recorded)
```

`_work/skill_pin.json` also carries `governance: {enabled, mandate_hash}`. It is a second marker
outside the directory, so deleting the directory does not silently re-enable the legacy behavior.
Deleting the pin too is outside the enforced boundary; `check`/`report` say so.

## The three levels

| Level | Who decides | Examples | Guard behavior |
|---|---|---|---|
| **L1** | Human | goal/target, hard constraint, scope, permission, model/plugin add, release, push, acceptance, unclassifiable | blocked until an accepted user decision exists |
| **L2** | Negotiation | changing a confirmed direction, architecture/stack/interface change, plan change, dependency with network/permission/cost/license effect, budget change, goal conflict, ambiguity, deviation | blocked until a decision; `mode off` allows L2 with a notice |
| **L3** | Agent | research, candidate selection, plan, task split, initial architecture/stack/interface, implementation, test, fix, optimisation, scheduling, produce, review, authorized model/plugin use | allowed inside the mandate/grant; no approval |

`authority.py classify --kind KIND` prints the level. Unknown → L1. `strictness` may escalate
(`high` moves initial architecture and dependency add to L2); it never lowers a confirmed-direction,
scope, constraint or goal change.

## Human idea intake

A user idea is a **candidate direction**, not an order. Only a declared hard constraint is binding.

```bash
python scripts/authority.py intake --summary "use SQLite for the queue" --kind idea \
  --verdict reject --assessment "single-writer lock breaks the 50-worker target" \
  --evidence "load test" --evidence-ref call:R20261103-... \
  --alternative "Postgres advisory locks" --direction-key storage
```

- `adopt` closes at L3; `modify`/`reject` need an alternative; `reject` also needs evidence and a
  resolvable evidence ref.
- A `call:R...` ref is checked against the call record; if its model family differs from the lead,
  the record says `independence: independent`; a log or the lead's own reading is `self|user`.
- If the user accepts the verdict, it closes as an accepted L2 decision. Without a response it stays
  open and blocks its direction key.
- The agent must not reject without a basis, and must not adopt because the user said so.

## Requests, decisions, rejection memory

```bash
python scripts/authority.py request --kind direction --subject queue-backend \
  --topic "replace SQLite with Postgres" --proposal "..." --option "pg:durable:ops cost"
python scripts/authority.py pending
python scripts/authority.py resolve --request Q... --decision accept --by user \
  --note "accepted; cost is fine" --approval-ref _work/governance/approvals/Q....md
```

- A matter is keyed by `mandate_version:kind:subject`; the level is metadata, so it cannot be
  re-asked at a lower level by relabelling.
- An open request with the same key is returned (`duplicate: true`) instead of interrupting again.
  A settled key is refused; reopening needs a reason and new evidence and keeps the original level.
- Rejection is durable: it survives a mandate bump, model switch, task split and subagent call until
  an accepted decision explicitly supersedes it. A rejected direction cannot return as a "small
  optimisation".
- `--batch` groups open requests in `requests.md`; `explain --id D...` shows the full fold;
  `outcome --id D... --verdict met|partial|missed` links the result for the report.

**Limit**: a paraphrase under a different `--subject` is not detected semantically. The report lists
similar subjects and the review checks for a re-ask.

## Grants and budgets

```bash
python scripts/authority.py grant --to lead --ops produce,write,review \
  --paths scripts/,references/,assets/ --models "codex:*,dsh:*" --max-calls 12 \
  --expires 2026-11-04T02:00:00+08:00 --by user --reason "v2.4 delivery" \
  --approval-ref _work/governance/approvals/grant-v24.md
```

- Without a parent, the grant is a subset of the mandate scope; with a parent, a subset of the
  parent (paths after `realpath`, ops/models, call count, expiry). Widening is exit 4, and nothing is
  written.
- Each allowed call reserves one unit in `usage.jsonl` under a cross-process lock and is charged to
  the grant and every ancestor. Two siblings cannot collectively exceed the parent; a crash leaves
  the reservation (attempts count). Cost/tokens are recorded when available but **not enforced**.
- Revoking or expiring a parent invalidates its children. `register --agent A --parent lead` adds an
  agent; `plugin add --name X --ops ...` registers a plugin capability. An unregistered
  agent/plugin is refused.
- `_work/governance/` and `_work/iteration/` are never writable through a grant.

## Guard and controlled paths

`authority.py guard --op produce --work-package WP1 --path scripts/authority.py --model codex:auto`
returns 0 (allow), 2 (bad invocation), 3 (state), or 4 (denied). It is called before the sensitive
action at:

| Path | When |
|---|---|
| `run_gpt.py` produce | before launch; after the call, a clean-tree + commit + diff check must stay inside the WP output |
| `run_gpt.py` review/plan-review/plan | before launch |
| `models.py` `_http_call` | before every credential resolution and every retry attempt |
| `models.py` `_codex_call`, `plan` | before the delegated call |
| `models.py` add/add-custom/init/enable/remove/team/chains/discover/test/key/account/use | before config/credential/network mutation |
| `release.py` | before checks/edits (`release`) and before any push (`push`); a candidate release skips the release guard and cannot push |
| `iteration.py` promote | after approval, before snapshot; `push` before push |
| `review_budget.py` | `raise`, one-off `--budget-change`, and `waive` |
| `facts_pack.py` | `freeze` (except the initial pack), `bump`, `set-budget` |
| `build_envelope.py` | embeds the mandate; fails if enabled but export is unavailable |
| `session.py` | pin records, check compares, status prints |
| `plan_gate.py` | treats an unclosed review `challenge` as blocking (challenge instance id) |

A produce call must name a reviewed work package or an explicit change. The WP's `direction` column
in the reviewed plan is the direction key; splitting one change into two calls does not change that
key. A produce call requires `--produce-artifact`, a clean candidate, `workspace-write` (not
danger-full-access), and a post-call check of `git status`, `git rev-list head_before..HEAD` and
`git diff --name-only head_before..HEAD`; every changed path must be inside the WP output. A
violation is recorded as `authority.scope_violation` and adoption is refused.

This is **detection at the candidate root**, not byte-level prevention: a model can write anywhere
the sandbox allows, but it cannot get the result adopted. If the host offers per-path sandboxing,
call `guard` before granting it and use that stronger boundary.

## Requirement matrix (P0/P1/P2)

Status: **Met** = a command enforces/records it; **Partial** = the enforced part is named and the
rest is a stated boundary or deferred.

| Req | Status | Execution point / test |
|---|---|---|
| P0-1 goal, idea, constraint input | Met | `init/mandate/intake`; goal-only, preference vs hard constraint, ambiguity tests |
| P0-2 independent evaluation | Partial | `intake` assessment + evidence refs; `call:` ref family check; multi-model independence only when a real independent call is cited |
| P0-3 negotiation | Met | `request/pending/resolve`; accept, reject, modify, reopen, settle tests |
| P0-4 autonomous research | Met | L3 research/plan/selection; options recorded in decisions; user scope request L2 |
| P0-5 autonomous planning | Met | L3 default; plan gate unchanged; confirmed-direction change L2 |
| P0-6 three levels | Met | `classify` matrix; unknown → L1; strictness tests |
| P0-7 major-decision interception | Partial | guards, WP direction, rejection memory, clean-tree + commit/diff detection; undeclared semantic change is audited, not prevented; host calls outside |
| P0-8 authorization/resources | Partial | grants/revoke/usage/intersection; call counts enforced; cost/token not enforced |
| P0-9 persistent control | Partial | ENABLED + pin marker, fail-closed, user-only mode, safe stop; an actor deleting all local state is outside the boundary |
| P0-10 models/agents/plugins | Partial | registry + cooperative plugin guards; non-cooperating host/plugin calls outside |
| P1-11 multi-model review | Met | envelope governance block; reviewer sees constraints/rejections; disagreement L2 |
| P1-12 decision records | Met | hash chain, mandate version, artifact/commit binding, `check/report/explain` |
| P1-13 goal/plan drift | Partial | boundary + WP direction + diff; semantic drift audited |
| P1-14 real-time intervention | Partial | pause/resume/off, pending/explain, checkpoint/resume; in-flight kill is host process control |
| P1-15 approval efficiency | Met | key dedupe, batch, grants, no re-ask, duplicate suppression |
| P2-16 preferences | Partial | project preferences + strictness; cross-project store deferred |
| P2-17 decision quality | Partial | outcome events + report by model/level; automatic routing optimisation deferred |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | allowed / recorded |
| 2 | bad invocation, missing required flag, or unapproved L1/L2 |
| 3 | governance state missing, corrupt, or inconsistent (fail closed) |
| 4 | denied by the mandate, grant, budget, rejection memory, or scope |

## Anti-patterns (each is a defect)

- Enforcing a major decision only in prose, a prompt, or a checklist.
- Asking the user to approve a normal design detail while silently changing a confirmed direction.
- Re-asking a settled matter under a new wording; reusing a rejected direction as an "optimisation".
- Widening a grant "temporarily" or letting a child inherit the parent's union.
- Treating a missing mandate as permission; resetting control state instead of reporting exit 3.
- Claiming host-wide enforcement or agent-proof identity that the host does not provide.
