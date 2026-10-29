---
name: coordination_of_multiple_large_models
description: Orchestrate two or more LLMs on one deliverable, across code, documents and data — capability check, model verification, quota-tiered routing, per-project multi-provider access (free/keyless, API key, custom base URL, local server, account-token import), task contract, request/result envelopes, zero-write review calls, schema-enforced review gate, plan-review gate, review-call budget, test evidence in the envelope, delivery gate, release/documentation checks, failure handling. Use for multi-model routing, adding or switching model providers, GPT/Codex reviews, plan-first production, subagent handoffs, adopting review findings, model outages or proxy issues, workspace migration, releases, and collaboration postmortems. 触发词：多模型协作、模型分工、模型路由、模型接入、模型团队、入队审批、方案设计、架构设计、技术选型、计划与分发分离、模型切换、免费模型、API Key、自定义 API 地址、Ollama、本地模型、额度分档、GPT 额度利用率、GPT 评审、codex 评审、子代理协作、评审结论采纳、模型卡顿、codex 超时、代理问题、协作复盘、方案评审门、评审轮次预算、测试证据、工作区身份、发版一致性检查。
metadata:
  version: 2.2.0
  updated: 2026-10-29
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

## Workflow

| # | Step | Gate | Read |
|---|---|---|---|
| 0 | Pin identity, environment, quota, team scan + approval | no pin / identity mismatch / no green / no team decision, no routing | [references/environment.md](references/environment.md) → [references/model-access.md](references/model-access.md) |
| 1 | Verify models, read quota, route | an unverified HTTP model is not a review default | [references/model-registry.md](references/model-registry.md) → [references/model-access.md](references/model-access.md) → [references/quota-routing.md](references/quota-routing.md) |
| 2 | Plan: requirement → research → architecture → stack → WP table; independent plan review; `plan_gate.py check` | no schema-valid plan review (different family), no dispatch | [references/model-access.md](references/model-access.md#planner-vs-dispatcher-planner-role) → [references/execution-and-gates.md](references/execution-and-gates.md) |
| 3 | Dispatch: task contract, board, facts pack frozen + versioned, review budget | facts stale or no budget, no work | [references/execution-and-gates.md](references/execution-and-gates.md) |
| 4 | Produce + independent review: roles, envelopes, test evidence | producer never self-reviews; no evidence = static-only | [references/calling-gpt.md](references/calling-gpt.md) → [references/handoff-protocol.md](references/handoff-protocol.md) |
| 5 | Review gate: validate (facts + `test_refs`) → verify at source → decide → event log → `deliver` | no valid result/evidence/delivery gate, no delivery | [references/handoff-protocol.md](references/handoff-protocol.md) |
| 6 | Release: `check_docs.py --run-tests` → `release.py`; postmortem; artifact cleanup | no checks/postmortem, not done | [references/failure-and-artifacts.md](references/failure-and-artifacts.md) |

## Session

```
SKILL v2.2.0 (pinned 22:54, D:) | quota 5h 78% / week 96% | 阶段：评审
```

- First turn: `python scripts/session.py pin`. Every turn after: `python scripts/session.py status --stage <stage>`.
- Self-check each turn: gate run? runner role? quota read? Answer in the status line or the event log, not from memory.
- A mid-session repo update does not change behaviour: continue on the pin, record one deviation line, and mention it at delivery.
- A path/disk change is not a deviation to log: it blocks until `session.py pin --force`, then redo the skipped gate.

## Scripts

| Command | Purpose |
|---|---|
| `python scripts/check_env.py --short-call` | Resolve Codex, version, processes, proxy, reachability, writability, live quota, pin identity |
| `python scripts/session.py pin\|check\|status\|identity\|note` | Contract + root identity pin, drift, status line, round notes |
| `python scripts/run_gpt.py --prompt-file <f> --cd <dir> --role produce\|review\|plan-review\|probe` | **The** GPT call path: persistent, role-aware, records thread/rollout/quota, gates, `--schema` + `--result-path` + `--envelope` for reviews |
| `python scripts/plan_gate.py check\|status` | Plan review gate: hash/mtime, envelope, result, call record, family |
| `python scripts/review_budget.py check\|raise\|note-minutes\|adopt\|waive\|report\|deliver` | Review-call/minute budget, production adoption, tier-conditioned delivery gate |
| `python scripts/facts_pack.py freeze\|bump\|check\|show\|set-budget` | Versioned facts pack: canonical hash, version log, budget edits |
| `python scripts/test_evidence.py record -- <cmd>` | Record command/exit code/log/hash evidence for a review envelope |
| `python scripts/check_docs.py --root . [--run-tests]` | Version/count/trigger/rule/path/script/asset consistency |
| `python scripts/release.py --version X.Y.Z --note "..." [--dry-run] [--no-push]` | Check docs, run the real suite, update version/count/changelog, commit, tag, push |
| `python scripts/models.py <init\|providers\|add\|key\|account\|team\|list\|discover\|test\|use\|route\|chains\|call\|plan\|health\|budget\|doctor>` | Per-project model access; `call` infers `produce` when role is omitted |
| `python scripts/quota.py [--source {auto,live,log}] [--json] [--remaining 62 --minutes 210]` | 5 h / weekly remaining, tier S/A/B/C/R/U, who produces, call shape |
| `python scripts/validate_result.py --result <json> [--envelope <yaml>]` | Schema + echo + unit coverage + facts/test_refs validation; exit 1 blocks adoption |
| `python scripts/schema_for_codex.py --schema <json> --out <f>` | Derive the strict schema codex requires (run_gpt does it automatically) |
| `python scripts/build_envelope.py --artifact <path> --out <yaml> --ledger <ledger> --unit "id::locator::expected"` | Envelope with facts, zero-write capture, units, schema path, prior findings, optional `--test-evidence` |
| `python scripts/reap_turns.py [--older-than 30] [--apply]` | Close turns stuck in `inProgress`; backup first |
| `python scripts/selftest_models.py` | Offline proof of the model + gate layer (mock providers; no key, no network) |

Helpers used by the commands above (not usually invoked directly): `scripts/gate_common.py`, `scripts/model_config.py`, `scripts/model_http.py`, `scripts/model_registry.py`, `scripts/model_route.py`.

Contracts and templates: `assets/result.schema.json`, `assets/call.schema.json`, `assets/facts-pack.example.md`, `assets/envelope.example.yaml`, `assets/providers.registry.json`, `assets/models.config.example.json`.

## Scope

Coordination mechanics only — no domain guidance (code style, writing, design, domain checklists). Those belong to the project's own facts pack and DoD. User instructions override this skill; record the deviation in the postmortem. Never widen scope or touch unrelated config.
