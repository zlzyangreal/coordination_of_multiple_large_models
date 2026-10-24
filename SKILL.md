---
name: coordination_of_multiple_large_models
description: Orchestrate two or more LLMs on one deliverable — capability check, model verification, quota-tiered routing, task contract, request/result envelopes, review gate, failure handling. Use for multi-model routing, GPT/Codex reviews, subagent handoffs, adopting review findings, model outages or proxy issues, and collaboration postmortems. 触发词：多模型协作、模型分工、模型路由、额度分档、GPT 额度利用率、GPT 评审、codex 评审、子代理协作、评审结论采纳、模型卡顿、codex 超时、代理问题、协作复盘。
metadata:
  version: 1.3.1
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
2. **Production ≠ review.** A producer never signs off its own artifact.
3. **No envelope, no review.** Findings without a request envelope are ignored.
4. **Model = slug + reasoning.** Never bake effort into the slug (`x-medium` is wrong). Always pass `-m` and `model_reasoning_effort` explicitly, at the configured level. Effort is **not** a quota lever: the tier changes scope and calls, not effort.
5. **Three paths differ:** artifact, payload/`output_path`, `result_path`. `-o` never targets the artifact.
6. **Version is hash + mtime.** Reviewer must echo `artifact_mtime`; a mismatch means wrong version, discard the round.
7. **Network before waiting.** TCP/proxy/short-call triage; report to the user instead of idling 20 minutes.
8. **Resolve executables at runtime;** never hardcode a versioned `bin/<hash>/` path.
9. **Quota before routing.** Read the 5 h pool (`scripts/quota.py`) before choosing who produces; allocation follows remaining % **and** hours-to-reset, never a fixed split. Re-read after every GPT batch; hold 8–12% for the visual gate.
10. **Stop lines:** ≤3 layout/format passes; ≤2 silent retries of the same operation.
11. **Delete temp files** (logs, renders, trial PDFs) when a stage ends.

## Workflow

| # | Step | Gate | Read |
|---|---|---|---|
| 0 | Environment: processes, CLI, models, toolchain, network, permissions | no routing until green | [references/environment.md](references/environment.md) |
| 1 | Verify models, read quota, route | unverified model = unusable; no quota reading, no routing | [references/model-registry.md](references/model-registry.md) → [references/quota-routing.md](references/quota-routing.md) |
| 2 | Task contract, board, facts pack, budget | no parallel work before facts freeze | [references/execution-and-gates.md](references/execution-and-gates.md) |
| 3 | Produce + independent review via envelopes | producer never self-reviews | [references/handoff-protocol.md](references/handoff-protocol.md) |
| 4 | Review gate: verify at source, accept/reject, log | no acceptance without source check | [references/handoff-protocol.md](references/handoff-protocol.md) |
| 5 | Deliver + postmortem + artifact cleanup | no postmortem, not done | [references/failure-and-artifacts.md](references/failure-and-artifacts.md) |

## Scripts

| Command | Purpose |
|---|---|
| `python scripts/check_env.py --short-call` | Resolve Codex, version, processes, proxy, reachability, writability, quota |
| `python scripts/quota.py [--json] [--remaining 62 --minutes 210] [--note "…"]` | Read 5 h / weekly remaining, tier S/A/B/C/R, who produces, call shape |
| `python scripts/build_envelope.py --artifact <f> --pages 4,8 --out <yaml>` | SHA256 + mtime + page_map skeleton; enforces path separation |
| `assets/result.schema.json` | Result contract |
| `assets/envelope.example.yaml` | Envelope example |

## Scope

Coordination mechanics only — no writing, layout, or design guidance. User instructions override this skill; record the deviation in the postmortem. Never widen scope or touch unrelated config.
