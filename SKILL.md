---
name: coordination_of_multiple_large_models
description: Orchestrate two or more LLMs on one deliverable, across code, documents and data — capability check, model verification, quota-tiered routing, per-project multi-provider access (free/keyless, API key, custom base URL, local server, account-token import), task contract, request/result envelopes, zero-write review calls, schema-enforced review gate, failure handling. Use for multi-model routing, adding or switching model providers, GPT/Codex reviews, subagent handoffs, adopting review findings, model outages or proxy issues, and collaboration postmortems. 触发词：多模型协作、模型分工、模型路由、模型接入、模型接入、模型团队、入队审批、方案设计、架构设计、技术选型、计划与分发分离、模型切换、免费模型、API Key、自定义 API 地址、Ollama、本地模型、额度分档、GPT 额度利用率、GPT 评审、codex 评审、子代理协作、评审结论采纳、模型卡顿、codex 超时、代理问题、协作复盘。
metadata:
  version: 2.1.0
  updated: 2026-10-27
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
5. **Schema verdict before adoption.** Review results are validated (`validate_result.py` / `run_gpt.py --schema`, exit 3). Missing `severity_basis` or `units_confirmed` is **invalid**, not "medium".
6. **Model = slug + reasoning.** Never bake effort into the slug (`x-medium` is wrong). Always pass `-m` and `model_reasoning_effort` explicitly, at the configured level. Effort is **not** a quota lever: the tier changes scope and calls, not effort.
7. **Three paths differ:** artifact, payload/`output_path`, `result_path`. `-o` never targets the artifact.
8. **Version is hash + mtime.** Reviewer must echo `artifact_mtime`; a mismatch means wrong version, discard the round.
9. **Persist every call.** Production and review calls go through `scripts/run_gpt.py`, without `--ephemeral`. An ephemeral call writes no rollout, blinds the quota reader and leaves nothing to audit; `--probe` is connectivity only.
10. **Pin the contract.** `scripts/session.py pin` at session start; compare each turn, log drift, never switch mid-session. Delivery states "pinned vX, repository vY".
11. **Quota before routing.** Read the 5 h pool (`scripts/quota.py`, live source) before choosing who produces; allocation follows remaining % **and** hours-to-reset, never a fixed split. An expired sample is "unknown", not 100%. Hold 8–12% for the visual gate when the artifact has one.
12. **Network before waiting.** TCP/proxy/short-call triage; report to the user instead of idling 20 minutes.
13. **Resolve executables at runtime;** never hardcode a versioned `bin/<hash>/` path.
14. **Stay in force.** Every turn starts with the status line (`session.py status`). If the skill stops being applied, say so in that turn, re-read, and redo the skipped gate — silent degradation is a defect.
15. **Stop lines:** ≤3 formatting/restructure passes; ≤2 silent retries of the same operation.
16. **Delete temp files** (logs, intermediate builds, exports) when a stage ends.
17. **Two protected call paths.** GPT only through `scripts/run_gpt.py`; DeepSeek only through the DSH host session. `models.py` delegates the first and refuses the second. A GPT/DeepSeek model reached over HTTP needs `allowProtectedFamily: true` — an explicit second path, never a silent replacement.
18. **Credentials never leave the store.** `models.py key set` writes a 0600 file (ACL-hardened on Windows); status output is `hasKey` + `…last4`; provider errors are redacted before print/log; a read-only view (`list`/`route`/`doctor`) never executes a configured credential command.
19. **Pin and independence are exact.** A `--pin` that is unusable fails routing instead of substituting another model. A review must be a different family from its producer; a family that cannot be established is not an independent reviewer unless the config declares it.
20. **Team changes need a yes.** At task start run `models.py team scan`, show the candidates (availability, family, context/limits, verified state) and ask the user which may join. Add only approved candidates (`team add`), record refusals (`team decline`). A declined channel is not used for that task; the protected lanes and already-approved agents remain. A changed endpoint, wire, auth mode or model invalidates the scan; rescan and ask again. Never add a provider silently.
21. **Planning ≠ dispatching.** The planner owns the executable plan; the lead/dispatcher owns the task board and assignments. Pick the planner by its **Reasoning / Design / Agentic / Coding** scores (`route --role plan`, config `overrides.<model>.scores`), never by quota tier or habit. The planner must independently understand the requirement, research the repo and comparable open source, compare trade-offs, design the architecture, choose the stack, and state milestones, acceptance and risks. It does not assign work to models and never signs off its own plan; the plan passes the independent review gate (different family) before dispatch.

## Workflow

| # | Step | Gate | Read |
|---|---|---|---|
| 0 | Pin contract, environment, quota, team scan + approval | no pin / no green / no team decision, no routing | [references/environment.md](references/environment.md) → [references/model-access.md](references/model-access.md) |
| 1 | Verify models, read quota, route | an unverified HTTP model is not a review default | [references/model-registry.md](references/model-registry.md) → [references/model-access.md](references/model-access.md) → [references/quota-routing.md](references/quota-routing.md) |
| 2 | Plan: requirement → research → architecture → stack → executable plan; independent plan review | no plan review, no dispatch | [references/model-access.md](references/model-access.md#planner-vs-dispatcher-planner-role) → [references/execution-and-gates.md](references/execution-and-gates.md) |
| 3 | Dispatch: task contract, board, facts pack (constraints vs budgets) | planner does not assign; dispatcher does | [references/execution-and-gates.md](references/execution-and-gates.md) |
| 4 | Produce + independent review: runner calls, envelopes | producer never self-reviews | [references/calling-gpt.md](references/calling-gpt.md) → [references/handoff-protocol.md](references/handoff-protocol.md) |
| 5 | Review gate: validate → verify at source → decide → log | no acceptance without a schema-valid result and a source check | [references/handoff-protocol.md](references/handoff-protocol.md) |
| 6 | Deliver + postmortem + artifact cleanup | no postmortem (skill-interruption question answered), not done | [references/failure-and-artifacts.md](references/failure-and-artifacts.md) |

## Session

```
SKILL v2.1.0 (pinned 19:36) | quota 5h 78% / week 96% | 阶段：评审
```

- First turn: `python scripts/session.py pin`. Every turn after: `python scripts/session.py status --stage <stage>`.
- Self-check each turn: gate run? runner used? quota read? Answer in the status line or the ledger, not from memory.
- A mid-session repo update does not change behaviour: continue on the pin, record one deviation line, and mention it at delivery.

## Scripts

| Command | Purpose |
|---|---|
| `python scripts/check_env.py --short-call` | Resolve Codex, version, processes, proxy, reachability, writability, live quota |
| `python scripts/session.py pin\|check\|status\|note` | Contract pin/drift, status line, round notes (review-input hashes) |
| `python scripts/run_gpt.py --prompt-file <f> --cd <dir> --request-id <id>` | **The** GPT call path: persistent, records thread/rollout/quota, `--schema` + `--result-path` for reviews |
| `python scripts/models.py <init\|providers\|add\|key\|account\|team\|list\|discover\|test\|use\|route\|chains\|call\|plan\|health\|budget\|doctor>` | Per-project model access: provider catalog, 0600 credentials, vendor-mediated account login, task team scan/approval, scored planning, availability, routing, chains, unified calls, budgets |
| `python scripts/quota.py [--source {auto,live,log}] [--json] [--remaining 62 --minutes 210]` | 5 h / weekly remaining, tier S/A/B/C/R/U, who produces, call shape |
| `python scripts/validate_result.py --result <json> [--envelope <yaml>]` | Schema + echo + unit-coverage validation; exit 1 blocks adoption |
| `python scripts/schema_for_codex.py --schema <json> --out <f>` | Derive the strict schema codex requires (run_gpt does it automatically) |
| `python scripts/build_envelope.py --artifact <path> --out <yaml> --ledger <ledger> --unit "id::locator::expected"` | Envelope with zero-write capture, unit list, schema path, `prior_findings` from the ledger |
| `python scripts/reap_turns.py [--older-than 30] [--apply]` | Close turns stuck in `inProgress`; backup first |
| `python scripts/selftest_models.py` | Offline proof of the model layer (mock providers; no key, no network) |
| `assets/result.schema.json`, `assets/call.schema.json`, `assets/facts-pack.example.md`, `assets/providers.registry.json`, `assets/models.config.example.json` | Contracts, templates, vendored provider data |

## Scope

Coordination mechanics only — no domain guidance (code style, writing, design, domain checklists). Those belong to the project's own facts pack and DoD. User instructions override this skill; record the deviation in the postmortem. Never widen scope or touch unrelated config.
