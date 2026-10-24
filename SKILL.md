---
name: coordination_of_multiple_large_models
description: Orchestrate two or more LLMs on one deliverable — capability check, model verification, quota-tiered routing, task contract, request/result envelopes, zero-write review calls, schema-enforced review gate, failure handling. Use for multi-model routing, GPT/Codex reviews, subagent handoffs, adopting review findings, model outages or proxy issues, and collaboration postmortems. 触发词：多模型协作、模型分工、模型路由、额度分档、GPT 额度利用率、GPT 评审、codex 评审、子代理协作、评审结论采纳、模型卡顿、codex 超时、代理问题、协作复盘。
metadata:
  version: 1.5.0
  updated: 2026-10-24
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
2. **Production ≠ review.** A producer never signs off its own artifact. Advisory drafts (outlines, routes, background text the lead adopts) count as production for the sections they shape: findings there are `origin: advisory-derived` and never count as independent review.
3. **No envelope, no review.** Findings without a request envelope are ignored.
4. **Reviewer writes nothing.** Review runs `read-only`; the result is the final message, captured by `run_gpt.py --output` and copied to `result_path` by the lead. A brief that asks the reviewer to write a file is a defect.
5. **Schema verdict before adoption.** Review results are validated (`validate_result.py` / `run_gpt.py --schema`, exit 3). Missing `severity_basis` or `title_confirmed` is **invalid**, not "medium".
6. **Model = slug + reasoning.** Never bake effort into the slug (`x-medium` is wrong). Always pass `-m` and `model_reasoning_effort` explicitly, at the configured level. Effort is **not** a quota lever: the tier changes scope and calls, not effort.
7. **Three paths differ:** artifact, payload/`output_path`, `result_path`. `-o` never targets the artifact.
8. **Version is hash + mtime.** Reviewer must echo `artifact_mtime`; a mismatch means wrong version, discard the round.
9. **Persist every call.** Production and review calls go through `scripts/run_gpt.py`, without `--ephemeral`. An ephemeral call writes no rollout, blinds the quota reader and leaves nothing to audit; `--probe` is connectivity only.
10. **Pin the contract.** `scripts/session.py pin` at session start; compare each turn, log drift, never switch mid-session. Delivery states "pinned vX, repository vY".
11. **Quota before routing.** Read the 5 h pool (`scripts/quota.py`, live source) before choosing who produces; allocation follows remaining % **and** hours-to-reset, never a fixed split. An expired sample is "unknown", not 100%. Hold 8–12% for the visual gate.
12. **Network before waiting.** TCP/proxy/short-call triage; report to the user instead of idling 20 minutes.
13. **Resolve executables at runtime;** never hardcode a versioned `bin/<hash>/` path.
14. **Stay in force.** Every turn starts with the status line (`session.py status`). If the skill stops being applied, say so in that turn, re-read, and redo the skipped gate — silent degradation is a defect.
15. **Stop lines:** ≤3 layout/format passes; ≤2 silent retries of the same operation.
16. **Delete temp files** (logs, renders, trial PDFs) when a stage ends.

## Workflow

| # | Step | Gate | Read |
|---|---|---|---|
| 0 | Pin contract, environment, quota | no pin / no green, no routing | [references/environment.md](references/environment.md) |
| 1 | Verify models, read quota, route | unverified model = unusable | [references/model-registry.md](references/model-registry.md) → [references/quota-routing.md](references/quota-routing.md) |
| 2 | Task contract, board, facts pack (constraints vs budgets) | no parallel work before facts freeze | [references/execution-and-gates.md](references/execution-and-gates.md) |
| 3 | Produce + independent review: runner calls, envelopes | producer never self-reviews | [references/calling-gpt.md](references/calling-gpt.md) → [references/handoff-protocol.md](references/handoff-protocol.md) |
| 4 | Review gate: validate → verify at source → decide → log | no acceptance without a schema-valid result and a source check | [references/handoff-protocol.md](references/handoff-protocol.md) |
| 5 | Deliver + postmortem + artifact cleanup | no postmortem (skill-interruption question answered), not done | [references/failure-and-artifacts.md](references/failure-and-artifacts.md) |

## Session

```
SKILL v1.5.0 (pinned 19:36) | quota 5h 78% / week 96% | 阶段：评审
```

- First turn: `python scripts/session.py pin`. Every turn after: `python scripts/session.py status --stage <stage>`.
- Self-check each turn: gate run? runner used? quota read? Answer in the status line or the ledger, not from memory.
- A mid-session repo update does not change behaviour: continue on the pin, record one deviation line, and mention it at delivery.

## Scripts

| Command | Purpose |
|---|---|
| `python scripts/check_env.py --short-call` | Resolve Codex, version, processes, proxy, reachability, writability, live quota |
| `python scripts/session.py pin\|check\|status\|note` | Contract pin/drift, status line, round notes (text-dump hashes) |
| `python scripts/run_gpt.py --prompt-file <f> --cd <dir> --request-id <id>` | **The** call path: persistent, records thread/rollout/quota, `--schema` + `--result-path` for reviews |
| `python scripts/quota.py [--source {auto,live,log}] [--json] [--remaining 62 --minutes 210]` | 5 h / weekly remaining, tier S/A/B/C/R/U, who produces, call shape |
| `python scripts/validate_result.py --result <json> [--envelope <yaml>]` | Schema + echo + page-coverage validation; exit 1 blocks adoption |
| `python scripts/schema_for_codex.py --schema <json> --out <f>` | Derive the strict schema codex requires (run_gpt does it automatically) |
| `python scripts/build_envelope.py --artifact <f> --out <yaml> --ledger <ledger> --pages 4,8` | Envelope with zero-write capture, schema path, `prior_findings` from the ledger |
| `python scripts/reap_turns.py [--older-than 30] [--apply]` | Close turns stuck in `inProgress`; backup first |
| `assets/result.schema.json`, `assets/call.schema.json`, `assets/facts-pack.example.md` | Contracts and templates |

## Scope

Coordination mechanics only — no writing, layout, or design guidance. User instructions override this skill; record the deviation in the postmortem. Never widen scope or touch unrelated config.
