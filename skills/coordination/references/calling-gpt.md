# Calling GPT: runner, persistence, encoding, observability

One entry point, one record per call. Hand-written `codex exec` invocations are for
one-off diagnosis only; everything that produces or reviews an artifact goes through
`scripts/run_gpt.py`.

`scripts/models.py` does not add a second GPT path: its `codex:<slug>` lane builds a
`run_gpt.py` invocation (same runner, same record, same quota ledger, with
`--calls-dir`/`--ledger` rooted in the selected project) and returns the runner's exit
code. `models.py test codex:auto` is `run_gpt.py --probe`, not a direct `codex exec`.
The `dsh:` lane is refused from scripts on purpose — the lead session is the DeepSeek
caller.

## Why (the failure this prevents)

Production calls were being copied from the environment probe, which used
`--ephemeral`. Ephemeral calls write **no rollout and no history row**, so:

- the quota reader has nothing to read and freezes at its last sample;
- the Codex UI, desktop pet and any dashboard cannot show the call;
- a postmortem cannot prove the call happened, or correlate a review with a session;
- an interrupted call leaves no trace to clean up.

## The runner

```bash
python scripts/run_gpt.py --prompt-file prompt.txt --cd _work --role produce \
    --work-package WP2 --produce-artifact _work/out/api.py --request-id R20261028-01 \
    --sandbox workspace-write --output _work/payload.md --timeout 1200
```

| Rule | Detail |
|---|---|
| Semantic role | `plan`, `produce`, `review`, `plan-review` or `probe`; omitted = `review` if `--schema` else `produce` |
| Plan gate | `produce` requires `--work-package`, a reviewed plan, and passes `plan_gate.py` before launch |
| Review gate | `review`/`plan-review` require `--schema`, `--envelope`, `--result-path` and `--milestone`; a third round needs `--why-unclosed` |
| Persistent by default | no `--ephemeral`; `--probe`/`--role probe` is the only ephemeral path, text-only, never adoptable |
| Runtime path | newest `%LOCALAPPDATA%\OpenAI\Codex\bin\*\codex.exe`; a `bin/<hash>/` literal is a defect |
| Explicit model + effort | `-m <model>` and `-c model_reasoning_effort=<effort>` always passed, defaulting to `~/.codex/config.toml` |
| Review calls carry a schema | `--schema assets/result.schema.json`; free-text production calls must **not** pass one |
| Strict derivation | the runner converts the contract to codex strict mode (`additionalProperties: false`, all-required); `--schema-raw` bypasses it |
| Result capture | `--output` (payload) then `--result-path`; the runner copies and validates, the reviewer writes nothing |
| Facts freshness | the runner rehashes the facts pack named by the envelope immediately before launch |
| Identity | a pin naming another root/disk refuses the call; re-pin first |
| Three paths | `--output` / `--result-path` / artifact stay distinct |
| Timeout | default 1200 s; on expiry the process is killed and the turn is closed as `interrupted` |
| Record | `_work/calls/<request_id>.json` + `.events.jsonl`; role/milestone/tier/envelope/result hashes |
| Ledger | one quota row per call, prefixed `role=... M=... WP=...`; `--no-ledger` skips it |
| Exit | 0 ok, 1 failure, 2 bad invocation or refused gate, 3 schema-invalid, 124 timeout |

For a review, the budget preflight runs before launch and writes an atomic
`_work/calls/pending/<request_id>.json`; the marker is removed after the final record. A request
id can be used once. Overrides go to `_work/gates/events.jsonl` (query with
`review_budget.py report`).

Long prompts (>6000 chars) are fed on stdin to stay under the Windows command-line
limit; short prompts are passed as an argument. Both produce the same record.

## Review call (the only shape that passes a read-only gate)

```bash
python scripts/run_gpt.py --prompt-file _work/review/brief_v6.md --cd _work --request-id R6 \
    --role review --milestone M2 --sandbox read-only \
    --schema assets/result.schema.json \
    --envelope _work/review/env_v6.yaml \
    --output _work/review/payload_v6.json \
    --result-path _work/review/result_v6.json
```

- The brief ends with "return the JSON as your final message, write no files". Write access is
  neither needed nor granted; `read-only + direct` is rejected by `build_envelope.py`.
- `--schema` makes codex emit schema-shaped output; the runner then validates the captured
  payload (echoed `artifact_mtime`, unit coverage, required findings fields) and
  exits `3` instead of letting a non-conforming result become "the review passed".
- **Strict-mode gotcha**: codex forwards `--output-schema` to the API with `strict: true`,
  which rejects plain draft-07 (every object needs `additionalProperties: false`, every
  property must be `required`, `pattern`/`minLength` are unsupported). The runner therefore
  derives `<request_id>.codex-schema.json` with `schema_for_codex.py` and passes that; your
  hand-written contract stays the validation schema. `--schema-raw` skips the derivation and
  will fail unless the file is already strict.
- `--envelope` turns on the cross-check against `request_id`, `version_hash` and `artifact_mtime`.
- A failed validation is a failed gate: repair and re-run, never hand the payload to the ledger.

## Call record (observability contract)

Every call leaves this, and the postmortem quotes it instead of "it ran":

| Field | Meaning |
|---|---|
| `request_id`, `status` | identity and `ok / failed / timeout / incomplete / schema-invalid` |
| `protocol_version`, `role`, `role_source`, `milestone` | semantic role and the budget bucket |
| `work_package`, `produce_artifact`, `produce_artifact_hash` | C2 production evidence |
| `routing_tier`, `tier_source` | the quota tier the call was authorised under |
| `started_at`, `finished_at`, `duration_s` | wall clock |
| `model`, `effort`, `sandbox`, `ephemeral` | what actually ran |
| `contract`, `workspace` | pinned skill version, drift, and absolute root/drive/volume/HEAD |
| `thread_id`, `rollout_path`, `events_path` | where to look, and whether it persisted |
| `exit_code`, `usage` | process result and token usage |
| `schema_path`, `schema_valid`, `schema_errors` | whether the result met the contract |
| `envelope_sha256`, `artifact`, `artifact_sha256`, `result_sha256` | the evidence chain the plan gate checks |
| `budget` | prior calls/minutes, `why_unclosed`, `budget_change` |
| `quota_before`, `quota_after`, `quota_delta_pts`, `quota_ledger` | budget accounting |
| `turn_cleanup` | `reaped: true` and the backup path when a killed turn was closed |
| `final_message`, `output_path`, `result_path` | what came back, and where it landed |

Machine contract: `assets/call.schema.json`.

Write these fields into the ledger row for the round. A review that cannot be tied to a
record is not evidence.

## Persistence boundary

- Calls made **outside** `codex exec` (direct API clients, other orchestration layers)
  produce no rollout and no history row. That is not a monitoring failure: such calls are
  outside the quota reader and out of scope for this skill. Say so in the report instead
  of claiming the call left no trace.
- `--ephemeral` is acceptable for exactly two things: the connectivity probe in
  `check_env.py --short-call`, and `run_gpt.py --probe`/`--role probe`. A probe is text-only:
  it cannot write `--output`, carry a schema/envelope, or count as production. Never use an
  ephemeral call for production or review.

## Interrupted calls

A killed turn keeps `status='inProgress'` in `~/.codex/thread_history_1.sqlite` forever;
consumers then show "working" for a turn that no longer exists.

- `run_gpt.py` closes its own turn on timeout and records `turn_cleanup`.
- Historical rows: `python scripts/reap_turns.py --older-than 30` (dry-run) then `--apply`.
  It backs up the database first, touches only rows whose status is exactly `inProgress`,
  and `--thread <id>` narrows it to one conversation.

## Windows script encoding

Generated `.ps1` files must be **UTF-8 with BOM** (or pure ASCII). Windows PowerShell 5.1
parses UTF-8-without-BOM Chinese as ANSI and fails with a misleading error such as
`表达式或语句中包含意外的标记` pointing at an unrelated brace. Prefer `.py` for new
scripts; if a `.ps1` is unavoidable, write the BOM and test with
`powershell.exe -NoProfile -File script.ps1`.

```python
with open(path, "w", encoding="utf-8-sig", newline="\r\n") as fh:
    fh.write(script)
```
