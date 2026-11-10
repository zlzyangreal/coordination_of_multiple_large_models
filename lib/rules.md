# Multi-model coordination contract (v3.0.0)

Read this before acting. Then call `coordination({action:"contract"})`; until then the host denies every other tool call.

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

