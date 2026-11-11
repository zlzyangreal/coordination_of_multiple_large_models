---
name: coordination_of_multiple_large_models
description: Orchestrate two or more LLMs on one deliverable, across code, documents and data — capability check, model verification, quota-tiered routing, per-project multi-provider access (free/keyless, API key, custom base URL, local server, account-token import), task contract, request/result envelopes, zero-write review calls, schema-enforced review gate, plan-review gate, review-call budget, test evidence in the envelope, delivery gate, release/documentation checks, failure handling, stable-version-drives-candidate self-bootstrap with dual workspaces, baseline comparison and gated promotion/rollback, and human-governed decision authority (mandate, idea intake, L1/L2/L3 decisions, scoped grants, execution guards, decision records). It is also a DSH-native bundle with one default-OFF global switch, a host-injected forty-rule contract that must be acknowledged before any tool call, a version lock with user-verified activation, declaration-driven optional extensions (install disabled, activate separately), and local-only I0–I3 logs. Use for multi-model routing, adding or switching model providers, GPT/Codex reviews, plan-first production, subagent handoffs, adopting review findings, model outages or proxy issues, workspace migration, releases, self-bootstrap iteration, promotion or rollback, human approval/authorization boundaries, decision records, DSH plugin installation/enable/disable/configuration, global switch, rule-first contract, version lock, optional extensions, one-click install and local log triage. 触发词：多模型协作、模型分工、模型路由、模型接入、模型团队、入队审批、方案设计、架构设计、技术选型、计划与分发分离、模型切换、免费模型、API Key、自定义 API 地址、Ollama、本地模型、额度分档、GPT 额度利用率、GPT 评审、codex 评审、子代理协作、评审结论采纳、模型卡顿、codex 超时、代理问题、协作复盘、方案评审门、评审轮次预算、测试证据、工作区身份、发版一致性检查、自举迭代、稳定版、候选版、双工作区、版本晋升、回退演练、决策权、人类治理、授权范围、审批拦截、L1/L2/L3、任务预授权、决策记录、思路评估、DSH 插件、插件安装、插件启停、总开关、规则先行、契约确认、版本锁定、验证后激活、可选扩展、一键安装、本地日志、I0-I3。
metadata:
  version: 3.0.3
  updated: 2026-11-11
allowed-tools: Read, Write, Edit, Bash
---

# Multi-model coordination

Two or more models, one deliverable. Decide who does what, how artifacts are handed over, when a review claim is trustworthy, and how to fail safely.

## When to use

- 2+ models, or lead + subagents, produce one artifact.
- An artifact is sent to another model for review and its findings will be adopted.
- Reconnects, timeouts, proxy errors, or reviews that no longer match the file.
- Work spanning sessions or days.

Skip for single-model, single-file, one-shot work — but still run `scripts/check_env.py`.

## Non-negotiables

1. **One lead** owns the deliverable; other models are roles, not co-commanders.
2. **Production ≠ review.** A producer never signs off its own artifact. Advisory work the lead adopts (designs, outlines, scaffolds, patches, sample text) counts as production for the units it shapes: findings there are `origin: advisory-derived` and never count as independent review.
3. **No envelope, no review.** Findings without a request envelope are ignored.
4. **Reviewer writes nothing.** Review runs `read-only`; the result is the final message, captured by `run_gpt.py --output` and copied to `result_path` by the lead. A brief that asks the reviewer to write a file is a defect.
5. **Schema verdict before adoption.** Review results are validated (`validate_result.py` / `run_gpt.py --schema`, exit 3). Missing `severity_basis` or `units_confirmed` is **invalid**, not "medium". A reviewer that cannot see the evidence says so in `limitations`; silence is not evidence.
6. **Model = slug + reasoning.** Never bake effort into the slug (`x-medium` is wrong). Always pass `-m` and `model_reasoning_effort` explicitly, at the configured level. Effort is **not** a quota lever: the tier changes scope and calls, not effort.
7. **Three paths differ:** artifact, payload/`output_path`, `result_path`. `-o` never targets the artifact.
8. **Version is hash + mtime.** Reviewer must echo `artifact_mtime`; a mismatch means wrong version, discard the round.
9. **Persist every call.** Production, review and plan calls go through `scripts/run_gpt.py`, without `--ephemeral`. An ephemeral call writes no rollout, blinds the quota reader and leaves nothing to audit; `--probe` is connectivity only.
10. **Pin the contract and the root.** `scripts/session.py pin` at session start records the version, absolute workspace root, drive, volume id and git HEAD. Compare each turn, log drift, never switch mid-session. A pin from another root/disk, or a legacy pin without identity, blocks work until re-pinned. Read only the pinned root's `_work`; never scan or merge a same-named directory on another disk. Delivery states "pinned vX (drive), repository vY".
11. **Quota before routing.** Read the 5 h pool (`scripts/quota.py`, live source) before choosing who produces; allocation follows remaining % **and** hours-to-reset, never a fixed split. An expired sample is "unknown", not 100%. Hold 8–12% for the visual gate when the artifact has one.
12. **Network before waiting.** TCP/proxy/short-call triage; report to the user instead of idling 20 minutes. Set `HTTPS_PROXY`/`HTTP_PROXY` for child processes when the host uses Clash: without it codex may retry WebSockets for minutes before falling back to HTTPS.
13. **Resolve executables at runtime;** never hardcode a versioned `bin/<hash>/` path.
14. **Stay in force.** Every turn starts with the status line (`session.py status`). If the skill stops being applied, say so in that turn, re-read, and redo the skipped gate — silent degradation is a defect.
15. **Stop lines:** ≤3 formatting/restructure passes; ≤2 silent retries of the same operation; ≤2 review rounds per milestone (`budgets.review_calls`). A third round needs `--why-unclosed`; exceeding the budget needs `--budget-change` or `review_budget.py raise`; every override goes to `_work/gates/events.jsonl`.
16. **Delete temp files** (logs, intermediate builds, exports) when a stage ends.
17. **Two protected call paths, one semantic role each.** GPT only through `scripts/run_gpt.py`; DeepSeek only through the DSH host session. `lead` and `mechanical` are fixed to `dsh:auto` — `use`, config and pins cannot switch them to codex/HTTP; a disabled dsh lane fails instead of substituting a model. Every runner call declares `plan|produce|review|plan-review|probe` (inferred if omitted); `probe` is text-only. `models.py` delegates the first and refuses the second. A GPT/DeepSeek model reached over HTTP needs `allowProtectedFamily: true` — an explicit second path, never a silent replacement.
18. **Credentials never leave the store.** `models.py key set` writes a 0600 file (ACL-hardened on Windows); status output is `hasKey` + `…last4`; provider errors are redacted before print/log; a read-only view (`list`/`route`/`doctor`) never executes a configured credential command.
19. **Pin and independence are exact.** A `--pin` that is unusable fails routing instead of substituting another model. A review must be a different family from its producer; a family that cannot be established is not an independent reviewer, and a plan review has no override for it — declare `overrides.<model>.family`.
20. **Team changes need a yes.** At task start run `models.py team scan`, show the candidates (availability, family, context/limits, verified state) and ask the user which may join. Add only approved candidates (`team add`), record refusals (`team decline`). A declined channel is not used for that task; the protected lanes and already-approved agents remain. A changed endpoint, wire, auth mode or model invalidates the scan; rescan and ask again. Never add a provider silently.
21. **Planning ≠ dispatching.** The planner owns the executable plan; the lead/dispatcher owns the task board and assignments. Pick the planner by its **Reasoning / Design / Agentic / Coding** scores (`route --role plan`, config `overrides.<model>.scores`), never by quota tier or habit. The planner must independently understand the requirement, research the repo and comparable open source, compare trade-offs, design the architecture, choose the stack, and state milestones, acceptance and risks. It does not assign work to models and never signs off its own plan; the plan passes the independent review gate (different family) before dispatch.
22. **Plan first, and gate it.** `scripts/plan_gate.py check` binds the current plan hash/mtime, its envelope, its result and its call record, then compares the reviewer's family with the planner's (or the lead's, for a lead-authored plan). **Every** `models.py call` with no explicit role is inferred as `produce` and runs this gate on every lane before credentials or network; `run_gpt.py --role produce` runs it too. No production waiver. Exit codes: 3 no review, 4 stale/mixed evidence, 5 an open high/medium finding, 6 not independent. A plan changes only by re-review.
23. **Review budget is a budget, not a habit.** Defaults: 2 review calls and 30 minutes per milestone. The third call requires `--why-unclosed`; exceeding the call budget requires a one-off `--budget-change` or `review_budget.py raise --to N --reason ...`, which bumps the facts pack. Overtime requires a `note-minutes` row. Reservations are atomic (`_work/calls/pending/`), a request id can be used once, and the single decision log is `_work/gates/events.jsonl`.
24. **A reviewer must see behaviour.** The lead records tests with `scripts/test_evidence.py record -- <command>`; `build_envelope.py --test-evidence` embeds the record and adds each log as a review unit. When evidence exists, `validate_result.py` requires `test_refs` with the log hash and a verbatim excerpt that is present in the log but absent from the envelope summary. A review with no evidence is static-only and must say so.
25. **GPT produces at S/A.** A produce unit names a work package from the reviewed plan's work-package table, produces an artifact whose path matches that row's `output` and whose hash is recorded, and has an adoption event. `review_budget.py deliver` blocks an S/A delivery with no such unit unless a stored user approval is recorded; B/C/R/U are exempt; `probe` never counts.
26. **Facts are versioned.** The facts pack carries `facts_version`, `facts_hash`, `frozen_at`. `facts_pack.py freeze|bump|check` is the only writer; a stale or unversioned pack cannot build an envelope; the runner rehashes before launch and the validator after; the result echoes `facts_version`. Any scope or budget change bumps the version and appends a version-log row.
27. **Stable drives candidate.** The released tag V(N) is the execution end; V(N+1) is developed in an independent git worktree created by `iteration.py init`. Never grow the next version by editing the stable tree in place, and never let a candidate release decide by itself that it is stable. One iteration has one manifest (`_work/iteration/iteration.json`) recording the stable tag/commit, capability list, config and tool snapshot.
28. **Two identities, two roots.** `session.py pin` records the orchestrator (`stable`) and the target (`candidate`) roots/commits; envelopes, evidence and call records name their workspace and commit. Candidate writes only its own root; stable `_work` is read-only to it. Overlap, wrong branch, stale identity or a candidate path that is not a worktree of the same repository stops the work (exit 3) — never repair silently.
29. **Freeze the executor.** Stable `SKILL.md`, scripts and model config are hashed at init. Changing them mid-iteration blocks `iteration.py status`/`compare`; no hot switch. Candidate edits are expected and do not touch the running stable executor. Production calls during an iteration run with `--cd <candidate>`; `run_gpt.py` refuses a produce call in the stable root.
30. **Promotion is a gate, not a version edit.** `iteration.py compare` requires candidate tests ≥ baseline with zero failures, consistent docs, and declared capability removals; `approve --by <user>` records the user's acceptance of that exact commit; the release tag must point at it; `promote` re-runs the plan gate, snapshots code/config/pins, fast-forwards and smoke-tests, and auto-restores V(N) if smoke fails. `rollback` (or the generated standalone shim under `_work/iteration/`) restores the snapshot without depending on the failed candidate. Every drill and every override is recorded in `_work/gates/events.jsonl`.
31. **The human owns the ends; the agent owns the means.** Start a governed task with a mandate: goal, deliverable, hard constraints, preferences, scope and call budget. The human may give only the goal. Their initial idea is a **candidate direction to evaluate**, not an order; only a declared hard constraint is binding. Ambiguity in the goal or a hard constraint is an L2 request before production — never a guess.
32. **Evaluate; never defer and never flatter.** Record an `intake` verdict (`adopt|modify|reject`) with an assessment, evidence and, for rejection, a real alternative and a resolvable evidence ref. Adopting because the human said it and rejecting without a basis are both defects. A substantive direction change is L2; a reasonable idea is adopted and improved without asking again.
33. **Three levels, one default.** L1 is the human's: goal, hard constraints, scope, permission, model/plugin add, release, push, acceptance and anything unclassifiable. L2 is negotiation: changing a confirmed direction, architecture/stack/interface/plan change, budget change, goal conflict, ambiguity, a dependency with network/permission/cost/licence effects. L3 is the agent's: research, planning, design, selection, scheduling, implementation, tests, fixes and authorised model/plugin use. Unclassifiable → L1. A settled decision is not re-opened; a rejection survives a mandate bump, model switch, task split and subagent call until an explicit accepted decision supersedes it.
34. **Controlled operations pass `authority.py guard`.** The runner, the model caller (before credentials and **each** retry), release, promotion, push, budget/facts writes and plugin/model registration check the mandate, grant, decision and rejection records before the sensitive action. A denial is a refusal with an exit code, not a warning. Prompt compliance is not enforcement; a declared change cannot be hidden by splitting it across calls, and the reviewed work package supplies the direction key.
35. **Grants are scoped and inherited by intersection.** A grant names ops, paths, models, call budget and expiry; a subagent or plugin receives at most the intersection of its parent's grant, and every call is charged atomically to the grant and all ancestors. A child, sibling, model switch or plugin cannot widen scope, budget or permission. `_work/governance/` and `_work/iteration/` are never writable through a grant.
36. **Ask once, batch, and keep moving.** One matter has one canonical key (`mandate_version:kind:subject`) and one open request; related items are batched in `requests.md`; a settled key is refused unless reopened with a reason and new evidence. While one decision waits, unaffected authorised work continues. A rejected direction cannot return as a "small optimisation".
37. **Failure stops; it never degrades.** Missing, corrupt or inconsistent governance state is exit 3 for protected operations — never "disabled", never auto-reset. Only the user changes the mode; `off` needs a stored approval and a safe-stop choice for open work. The mode survives model switches, context compaction, task splitting and subagent calls because it is on disk. A reconcile/re-checkpoint records the state a resumed task depends on.
38. **State the enforcement boundary.** Enforcement covers the skill's controlled script paths and the candidate-root diff check; host tool calls, shell commands and non-cooperating third-party plugin calls are outside it and rely on DSH's sandbox/approval. `--by user` is an attestation unless a host HMAC (`DSH_GOVERNANCE_SECRET`) authenticates it. Records are hash-chained, not signed; deleting every local marker is outside the local guarantee. Never claim global enforcement or agent-proof identity — say which requirement is partial and why.
39. **Lock the version; activate only after the user verifies.** The running package is authoritative. A new version is staged in a separate verification profile; the plugin reports `pending_verification` when the running version or package digest differs from the last user-verified values, injects no contract and executes no controlled action until the user runs the checks and explicitly activates it. First install is pending too. The plugin never updates, downgrades or hot-swaps itself, and true side-by-side execution in one profile is not claimed.
40. **Load the rules before any controlled action.** When the switch is ON, the host injects the forty-rule contract and the session must acknowledge it before any tool call except `coordination status`; an invalid or missing contract keeps a deny guard, is logged at I0 and fails closed. Enforcement must not depend on the agent choosing to read `SKILL.md`, and the denial must name the required acknowledgement.

## Workflow

| # | Step | Gate | Read |
|---|---|---|---|
| 0 | Pin identity, environment, quota, team scan + approval; for a next-version goal also `iteration.py init` (stable tag → candidate worktree) | no pin / identity mismatch / no green / no team decision / overlapping roots, no routing | [references/environment.md](references/environment.md) → [references/model-access.md](references/model-access.md) → [references/iteration-bootstrap.md](references/iteration-bootstrap.md) |
| 0b | Governance (when the task is human-governed): mandate with goal/constraints/scope; evaluate the human idea (`intake`); grants for the task/agents; control mode and checkpoint | no mandate for a governed task, L1/L2 work without a decision, widened grant, corrupt state → 3 | [references/governance.md](references/governance.md) |
| 1 | Verify models, read quota, route | an unverified HTTP model is not a review default | [references/model-registry.md](references/model-registry.md) → [references/model-access.md](references/model-access.md) → [references/quota-routing.md](references/quota-routing.md) |
| 2 | Plan: requirement → research → architecture → stack → WP table; independent plan review; `plan_gate.py check` | no schema-valid plan review (different family), no dispatch | [references/model-access.md](references/model-access.md#planner-vs-dispatcher-planner-role) → [references/execution-and-gates.md](references/execution-and-gates.md) |
| 3 | Dispatch: task contract, board, facts pack frozen + versioned, review budget, mandate/grants; candidate worktree is the only write target | facts stale or no budget, out-of-grant scope, no work | [references/execution-and-gates.md](references/execution-and-gates.md) → [references/governance.md](references/governance.md) |
| 4 | Produce + independent review: roles, envelopes, test evidence, authority guard before launch/credentials | producer never self-reviews; guard refusal; no evidence = static-only | [references/calling-gpt.md](references/calling-gpt.md) → [references/handoff-protocol.md](references/handoff-protocol.md) |
| 5 | Review gate: validate (facts + `test_refs`) → verify at source → decide → event log → `deliver`; record decisions/outcomes | no valid result/evidence/delivery gate, no delivery; unclosed L1/L2 | [references/handoff-protocol.md](references/handoff-protocol.md) → [references/governance.md](references/governance.md) |
| 5b | Compare with the baseline, accept, promote or roll back | tests < baseline, failures, undeclared capability removal, no approval, tag not on the tested commit, smoke failed | [references/iteration-bootstrap.md](references/iteration-bootstrap.md) |
| 6 | Release: `check_docs.py --run-tests` → `release.py`; postmortem; artifact cleanup | no checks/postmortem, not done | [references/failure-and-artifacts.md](references/failure-and-artifacts.md) |

## Session

```
SKILL v3.0.3 (pinned 22:54, D:) | quota 5h 78% / week 96% | 阶段：评审
iteration: stable v2.4.0 @f26cdda -> candidate v3.0.0 @<commit> (development)
governance: enforce v4 | open L1 1 / L2 0 | grants 2 | checkpoint: review
plugin: switch ON (verified 3.0.3) | contract loaded | extensions 0/1 | logs I0 0 / I1 1
```

- First turn: `python scripts/session.py pin`. Every turn after: `python scripts/session.py status --stage <stage>`.
- For a governed task, `authority.py init --goal ...` runs at task start; `status` prints the
  governance line. If it is absent, governance is off — say so rather than implying control.
- When the goal is V(N+1), `iteration.py init --candidate ../<repo>-candidate` runs before dispatch; the second status line names both roles. Candidate gates run with the stable scripts as the trusted copy.
- Self-check each turn: gate run? runner role? quota read? workspace role? Answer in the status line or the event log, not from memory.
- A mid-session repo update does not change behaviour: continue on the pin, record one deviation line, and mention it at delivery.
- A path/disk change is not a deviation to log: it blocks until `session.py pin --force`, then redo the skipped gate.

## DSH plugin

The same repository is also a DSH bundle (`package.json` → `dsh.bundle`, `cordis.patch.yml`,
`lib/index.js`, `lib/client.js`). Install it with `dsh plugin --profile <name> add <spec>`; the
desktop profile is managed by the Electron app. The plugin adds:

- one global switch, default **OFF**; OFF leaves DSH native, ON injects the forty-rule contract,
  registers the `coordination` tool and refuses every other tool until the session acknowledges
  the contract (rule 40);
- a version lock: the running package version **and** digest must match the last user-verified
  values, otherwise the plugin starts `pending_verification` and does nothing controlled until the
  user runs the checks and explicitly activates it; a new version is staged in a separate
  verification profile (rule 39);
- a settings page that lists **declared** optional extensions, installs one (disabled) only after
  explicit confirmation, and activates only after identity plus a declared public capability probe
  pass; `dsh-our-free-model` is optional and never installed by default;
- local-only `runtime_error_YYYY-MM-DD.jsonl` / `optimization_YYYY-MM-DD.jsonl` logs with I0–I3,
  dedupe, redaction, rotation and retention under `$DSH_HOME/coordination/`; no auto-fix, upload or
  git inclusion.

Supported platform for v3.0.3: DSH **0.2.1-rc.3 Web profile on Windows 11 x64**. Desktop is
expected but unverified; see `docs/desktop.md`. Enforcement covers the plugin's own controlled
paths only (rule 38).

## Scripts

| Command | Purpose |
|---|---|
| `python scripts/check_env.py --short-call` | Resolve Codex, version, processes, proxy, reachability, writability, live quota, pin identity |
| `python scripts/session.py pin\|check\|status\|identity\|note` | Contract + root identity pin, drift, status line, round notes; with an iteration it pins both roles |
| `python scripts/iteration.py init\|status\|guard\|baseline\|evidence\|compare\|decide\|approve\|promote\|rollback` | Stable V(N) → isolated candidate V(N+1), commit-bound evidence, baseline diff, decision log, user approval, gated promotion and independent rollback |
| `python scripts/run_gpt.py --prompt-file <f> --cd <dir> --role produce\|review\|plan-review\|probe` | **The** GPT call path: persistent, role-aware, records thread/rollout/quota, gates, `--schema` + `--result-path` + `--envelope` for reviews |
| `python scripts/plan_gate.py check\|status` | Plan review gate: hash/mtime, envelope, result, call record, family, unclosed challenges |
| `python scripts/authority.py <init\|mandate\|intake\|classify\|request\|pending\|resolve\|decide\|grant\|revoke\|register\|plugin\|usage\|guard\|mode\|checkpoint\|resume\|approve-file\|check\|report\|export\|status>` | Human-governed decision authority: mandate, L1/L2/L3, dedupe/rejection memory, scoped grants and atomic usage, execution guards, control mode, checkpoint, decision report |
| `python scripts/review_budget.py check\|raise\|note-minutes\|adopt\|waive\|report\|deliver` | Review-call/minute budget, production adoption, tier-conditioned delivery gate |
| `python scripts/facts_pack.py freeze\|bump\|check\|show\|set-budget` | Versioned facts pack: canonical hash, version log, budget edits |
| `python scripts/test_evidence.py record -- <cmd>` | Record command/exit code/log/hash evidence for a review envelope |
| `python scripts/check_docs.py --root . [--run-tests]` | Version/count/trigger/rule/path/script/asset consistency |
| `python scripts/check_rules.py [--root .]` | Verify the 40-rule manifest matches `SKILL.md` paragraphs and the v2.4.0 baseline hash |
| `python scripts/release.py --version X.Y.Z --note "..." [--dry-run] [--no-push]` | Check docs, run the real suite, update version/count/changelog, commit, tag, push |
| `python scripts/models.py <init\|providers\|add\|key\|account\|team\|list\|discover\|test\|use\|route\|chains\|call\|plan\|health\|budget\|doctor>` | Per-project model access; `call` infers `produce` when role is omitted |
| `python scripts/quota.py [--source {auto,live,log}] [--json] [--remaining 62 --minutes 210]` | 5 h / weekly remaining, tier S/A/B/C/R/U, who produces, call shape |
| `python scripts/validate_result.py --result <json> [--envelope <yaml>]` | Schema + echo + unit coverage + facts/test_refs validation; exit 1 blocks adoption |
| `python scripts/schema_for_codex.py --schema <json> --out <f>` | Derive the strict schema codex requires (run_gpt does it automatically) |
| `python scripts/build_envelope.py --artifact <path> --out <yaml> --ledger <ledger> --unit "id::locator::expected"` | Envelope with facts, zero-write capture, units, schema path, prior findings, optional `--test-evidence` |
| `python scripts/reap_turns.py [--older-than 30] [--apply]` | Close turns stuck in `inProgress`; backup first |
| `python scripts/selftest_models.py` | Offline proof of the model + gate layer (mock providers; no key, no network) |

Helpers used by the commands above (not usually invoked directly): `scripts/gate_common.py`, `scripts/model_config.py`, `scripts/model_http.py`, `scripts/model_registry.py`, `scripts/model_route.py`.

Contracts and templates: `assets/result.schema.json`, `assets/call.schema.json`, `assets/facts-pack.example.md`, `assets/envelope.example.yaml`, `assets/providers.registry.json`, `assets/models.config.example.json`, `assets/mandate.example.json`, `assets/decision-record.example.json`.

## Scope

Coordination mechanics only — no domain guidance (code style, writing, design, domain checklists). Those belong to the project's own facts pack and DoD. Human-governed mode is coordination: it decides who may change the goal/constraint/scope and records why, not how to write the code. Enforcement covers the controlled script paths; host tools, shell commands and non-cooperating plugins are outside the guarantee (rule 38). User instructions override this skill; record the deviation in the postmortem. Never widen scope or touch unrelated config.
