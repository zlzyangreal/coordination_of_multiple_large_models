# Failure handling and artifact safety

## Failure matrix

| Situation | Verdict | Action |
|---|---|---|
| Reconnects / timeouts | triage first | environment.md; report if triage fails; keep alive 10–20 min if it passes |
| Timeout, no new logs | failed | runner kills and closes the turn; switch model or self-produce; record real cause |
| Turn stuck `inProgress` | hygiene | `scripts/reap_turns.py --older-than 30` (dry-run) then `--apply`; backup is automatic |
| Production call run with `--ephemeral` | defect | no rollout, no history, quota reader blind: rerun through `scripts/run_gpt.py`; note it in the postmortem |
| Quota reading unknown (tier U) | constrained | never assume a surplus; take a live reading or `--remaining`; keep GPT work to gates meanwhile |
| Quota exhausted / tier R | constrained | lead takes all text work; one visual gate only; defer the rest to the next window and report the reset time |
| Window closed with >50% unused and a backlog | defect | wasted capacity: log used% and the reason, change the next window's plan (quota-routing.md) |
| Approval rejected | unavailable | substitute or self-produce; never re-request the same model |
| File locked / permission denied | environment | report; release lock or change mode; no silent retries |
| Artifact overwritten | incident | restore from snapshot or call log; postmortem entry |
| Write conflict | incident | owner's version wins; merge with diff; record rollback point |
| Reviewer vs producer conflict | technical | lead decides, records reasons |
| Unusable model output | quality | fix facts pack / narrow scope / change model; never ship as-is |

## Artifact safety

- **Snapshot before overwrite**: copy the current artifact to `_work/snapshots/<name>_<hhmmss>.<ext>`; keep at most 3 per file.
- **Path separation**: `-o` / `result_path` never point at the artifact.
- **Restore order**: diff the damage → newest snapshot → replay `apply_patch` entries from the call log → postmortem entry (what overwrote it, how restored, prevention).

## Cleanup

- Delete `.err` logs, intermediate renders, trial PDFs, one-off scripts at stage end; keep evidence in the ledger or postmortem, not as files.
- `_work/` over 2 GB: delete oldest snapshots and renders. `fist_output/` holds finals only.
- Snapshot cap: 3 per file, no per-edit archives.

## Escalate immediately

Report symptom + intended fix before acting, and cap silent retries at 2:

- permission denied / wrong sandbox mode;
- file locked, cannot write or delete;
- artifact overwritten or path collision;
- network egress or proxy not working;
- model verification failed or ambiguous candidates.
