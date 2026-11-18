---
name: Multi-Model Coordination
description: Run one deliverable across two or more models without losing control - choose a planner by scores, hand work over with an envelope, require a reviewer from a different model family, gate on evidence instead of opinion, and keep a decision trail. Covers routing, parallel producers, independent review, plan gates, review budgets, failure handling, and safe version iteration.
summary: Decide who produces, who reviews, and when a review actually counts - for GPT, Claude, Gemini, DeepSeek, Qwen, Kimi and local models.
version: 3.3.0
tags:
  - agents-orchestration
  - llm-ops
  - code-quality-review
  - workflow-automation
keywords:
  - multi-model
  - model routing
  - llm orchestration
  - independent review
  - subagents
  - plan review
  - code review
  - model fallback
use_cases:
  - Split a feature across a planner model, a producer model and an independent reviewer
  - Stop a model from approving its own output
  - Decide which model to use when one is rate-limited, out of quota or timing out
  - Run a plan review before any code is written
  - Keep a review from being adopted when it was produced against an older file
  - Hand a task to a subagent without losing acceptance criteria
docs_url: https://github.com/zlzyangreal/coordination_of_multiple_large_models
---

# Multi-model coordination

Two or more models, one deliverable. This skill fixes the parts teams get wrong: who owns the
work, what a hand-off contains, when a review is trustworthy, and how to fail without degrading.

It is model-agnostic. It names roles, not vendors: any agent that can call two models (directly,
through subagents, or through an MCP server) can follow it.

## When to use

- Two or more models, or a lead plus subagents, produce one artifact.
- An artifact is sent to another model for review and its findings will be adopted.
- A model is rate-limited, out of quota, timing out through a proxy, or returning stale context.
- The work spans sessions or days and a decision trail matters.
- A release or version bump must not be decided by the code that is being released.

## When to skip

Single model, single file, one shot. Coordination overhead exceeds the value. Do not skip the
pre-flight check below.

## Roles

| Role | Owns | Must not |
|---|---|---|
| **Lead** | The deliverable, the task board, the final message to the human | Produce a unit it then reviews |
| **Planner** | The executable plan: requirement, options, stack, milestones, acceptance, risks | Assign work to models; sign off its own plan |
| **Producer** | One work package, one artifact path | Review or approve its own artifact |
| **Reviewer** | A verdict on a specific, hashed version of an artifact | Write files; review without an envelope |
| **Dispatcher** | Routing, retries, budgets, stop lines | Quietly widen scope or budget |

One lead. Other models are roles, not co-commanders. Two models "collaborating" without an owner
produces merged opinions and no decision.

## The contract

1. **One lead owns the deliverable.** Every artifact has one owner at a time. Co-ownership is how a
   decision disappears.
2. **Production is not review.** The producer never signs off its own artifact. Advisory work the
   lead adopts (an outline, a scaffold, a patch, sample text) counts as production for the units it
   shapes; findings there are advisory-derived and never count as independent review.
3. **No envelope, no review.** A review request without scope, artifact path, version hash and
   acceptance criteria is ignored. See `references/envelopes.md`.
4. **The reviewer writes nothing.** Review is read-only. The reviewer's final message is the result;
   the lead captures it and copies it to the result path. A brief that asks the reviewer to write a
   file is a defective brief.
5. **Schema before adoption.** A result missing its verdict, severity basis, or the list of units it
   actually confirmed is invalid, not "medium confidence". Invalid means: fix the brief, re-run.
6. **Independence is a property of the pair, not the prompt.** Producer and reviewer must come from
   different model families. Asking the same model with "be critical" is a second opinion, not an
   independent one. If the family cannot be established, the reviewer is not independent - say so
   instead of pretending.
7. **Three paths, never conflated.** Artifact (the thing under review), payload (the request
   envelope), result (the verdict). An output flag never points at the artifact.
8. **Version is hash + mtime.** The reviewer echoes the artifact hash and modification time it saw.
   A mismatch means it reviewed a different version: discard the round, do not argue with it.
9. **Quota and health before routing.** Read remaining quota and reset time before choosing who
   produces. An expired sample is "unknown", not 100%. Route by role fit first, then by quota.
10. **Evidence before delivery.** When tests can run, record command, exit code, log and hash, and
    put them in the envelope. A review with no behavioural evidence is static-only and must say so.
11. **Stop lines are part of the plan.** Defaults: at most 2 review rounds per milestone, at most 3
    formatting passes, at most 2 silent retries of the same operation. A third review round needs a
    written reason stating what remains unclosed.
12. **Failure stops; it never degrades.** Missing state, a stale pin, an unusable model or a corrupt
    budget is a hard stop with an explicit exit. Never "continue anyway" and never auto-reset.

## Workflow

| # | Step | Gate |
|---|---|---|
| 0 | Pre-flight: environment, workspace root, quota, available models, budget | No pin, no quota reading, no routing |
| 1 | Plan: requirement, at least two options, trade-offs, stack, work-package table | No plan, no production |
| 2 | Plan review by a different family; resolve findings | No schema-valid review, no dispatch |
| 3 | Dispatch: task contract + envelope per unit; freeze the plan and the facts | Stale facts or no budget, no work |
| 4 | Produce, then review with an independent model | Producer never self-reviews |
| 5 | Gate: validate result, verify findings at source, decide, log, deliver | No valid result, no delivery |
| 6 | Release: docs and version checks, changelog, tag, postmortem | No checks, not done |

### Step 1 - what a plan must contain

A plan is executable, not aspirational. Require: the requirement restated in one paragraph; at
least two candidate approaches with the trade-off that decides between them; the chosen stack and
why; a work-package table (id, output path, owner role, acceptance test); risks with mitigations;
and the call budget. A plan that cannot be dispatched is a summary.

### Step 2 - plan review

Send the plan to a model from a different family than the planner. Ask for exactly four things:
contradictions, missing acceptance tests, unstated assumptions, and cheaper alternatives. A plan
review with no findings is allowed - but it must say what it checked and what it could not see.

### Step 4 - the hand-off

Every unit gets an envelope: goal, non-goals, artifact path, acceptance criteria, constraints,
inputs with hashes, and the exact output format expected. The reviewer gets the artifact, the
criteria, and the evidence - not the producer's reasoning or its self-assessment. Reasoning leaks
bias; the artifact is the only shared truth.

### Step 5 - adoption

Findings are not adopted because they sound right. For each finding: open the cited location,
confirm the claim at the current version, then classify as `accepted`, `rejected` (with a reason)
or `deferred` (with an owner). A finding that cannot be located is not adopted - and not silently
dropped either; record it as unverifiable.

## Envelopes

Request (what the reviewer receives):

```yaml
request_id: R-0042
role: review
artifact: src/router.ts
artifact_sha256: 9f2c...
artifact_mtime: 2026-03-04T10:12:31Z
workspace: /work/project
facts_version: 7
criteria:
  - Routing must not pick a model from the producer's family for review
  - A failed provider must fall through to the next candidate
units:
  - src/router.ts:1-120
evidence:
  - command: node --test tests/router.test.mjs
    exit: 0
    log_sha256: 4c11...
output_format: json   # schema below
read_only: true
```

Result (what comes back):

```json
{
  "request_id": "R-0042",
  "verdict": "pass | fail",
  "units_confirmed": ["src/router.ts:1-120"],
  "artifact_sha256": "9f2c...",
  "artifact_mtime": "2026-03-04T10:12:31Z",
  "facts_version": 7,
  "severity_basis": "how severity was assigned",
  "findings": [
    {"severity": "high|medium|low", "location": "src/router.ts:88",
     "claim": "...", "evidence": "...", "fix": "..."}
  ],
  "test_refs": [{"log_sha256": "4c11...", "excerpt": "verbatim line from the log"}],
  "limitations": ["what the reviewer could not see"]
}
```

`severity_basis` and `units_confirmed` are not optional. `limitations` is where a reviewer states
blind spots; silence there is treated as a claim of full visibility, which is almost never true.

## Independence

- Producer and reviewer must be different families (e.g. producer GPT, reviewer Claude; producer
  Qwen, reviewer DeepSeek). Same family = same blind spots, no matter the prompt.
- The planner never reviews its own plan. The lead reviews a plan only if the lead did not write it.
- A subagent inherits the lead's constraints but not its permissions: a child cannot widen scope,
  model access or budget.
- Record the family per model once, in a small config, and let the router enforce it. Do not rely on
  remembering which model is "the Claude one".

## Routing and failure

Choose the model per role: planning needs reasoning and design; production needs coding and
instruction-following; review needs a different family and a critical posture; bulk mechanical work
needs cheap throughput.

When a call fails, triage before retrying:

1. Network/TCP - proxy, DNS, endpoint. Fix the transport, not the prompt.
2. Auth/quota - 401/403/429. Switch model or wait; do not retry the same call twice.
3. Context - truncation or stale files. Rebuild the envelope from disk, not from chat history.
4. Model - it refused or produced unusable output. Change model, not temperature.

Two silent retries of the same operation is the limit. After that, report to the human with the
evidence and stop.

## Version iteration

Never grow the next version by editing the stable tree in place.

1. Tag the released version; freeze it.
2. Create an isolated worktree for the next version.
3. Compare candidate against baseline: tests must be at least equal, with zero failures, and any
   removed capability must be declared.
4. The human approves that exact commit; the release tag must point at it.
5. Promote with a re-run of the gates; keep a rollback that does not depend on the candidate.

The candidate never decides by itself that it is stable, and the stable version never hot-swaps its
own executor mid-iteration.

## Reference implementation

The full toolchain for this protocol - model registry, routing, envelopes, gates, review budgets,
iteration and promotion - is open source (MIT):
<https://github.com/zlzyangreal/coordination_of_multiple_large_models>

It ships as a DeepSeek Harness plugin with scripts (`run_gpt.py`, `models.py`, `plan_gate.py`,
`review_budget.py`, `iteration.py`, `check_docs.py`). This listing is the portable protocol layer:
your agent can follow it with its own model and subagent tools, with no install.

## 中文速览

多模型协作的可移植协议层：一个 lead 负责交付；规划者与执行者分离；评审必须来自不同模型家族；
没有信封不评审；没有 schema 结果不采纳；评审只读、不写文件；版本用 hash + mtime 锁定；
有测试证据才交付；评审轮次、格式轮次、重试次数都有硬上限；失败即停，绝不降级继续。
完整工具链（DSH 插件，MIT）见仓库。
