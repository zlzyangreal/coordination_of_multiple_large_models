# Handoff protocol: envelope, result schema, provenance, review gate

## Division of labour: the reviewer writes nothing

A review call runs in `--sandbox read-only` and **writes no file**. The result is the final
message; `scripts/run_gpt.py --output <payload>` captures it, `--result-path` copies it, and
validation runs on the copy. Lead-side, never reviewer-side: a brief that says "write the JSON
to result_path" guarantees a failed round under a read-only sandbox.

| Permission | Field | Default | Meaning |
|---|---|---|---|
| artifact access | `artifact_access` | `read-only` | what the reviewer may read; write access is never needed for review |
| result capture | `result_capture` | `runner` | `runner` = `-o` captures the message; `direct` = reviewer writes `result_path` (needs `workspace-write`, must be probed and justified) |

`read-only + direct` is impossible; `build_envelope.py` rejects it at build time.

## Units: what the reviewer must actually open

Artifacts are not always paginated. A **unit** is any slice a reviewer has to look at:

| Kind | `locator` example | `expected` example |
|---|---|---|
| `file` | `src/api.py:40-90` | `handle_request` |
| `diff` | `diffs/PR-214.patch` | `no behaviour change in retry path` |
| `section` | `docs/arch.md#2.4` | `"2.4 Failure modes"` |
| `page` | `_work/renders/p004.png` | `"（一）国内外研究现状"` |
| `endpoint` | `GET /api/v1/orders` | `403 on missing scope` |
| `cell` | `report.xlsx!Budget!B2:H9` | `totals row present` |
| `symbol` | `pkg/auth/token.py::refresh` | `refresh()` |

A unit carries `id`, `kind`, `locator`, `expected`. The reviewer must report every unit in
`units_seen` **and** in `units_confirmed` (with what it actually observed). That is what makes
"the reviewer opened it" decidable instead of asserted.

## Request envelope (lead → reviewer)

| Field | Required | Notes |
|---|---|---|
| `request_id` | yes | unique; a request id is used once |
| `envelope_version` | yes | `2`; a v2 envelope requires a v2 call record |
| `skill_version`, `skill_pinned_at` | yes | which contract built this envelope (session pin) |
| `producer` | yes | producing model id (`dsh:auto` for a lead-authored artifact) |
| `artifact` | yes | absolute path (file or directory) |
| `version_hash` | yes | SHA256 of the file, or a tree hash (paths + file hashes) when the artifact is a directory |
| `artifact_mtime` | yes | ISO 8601 with offset |
| `facts_path`, `facts_version`, `facts_hash`, `facts_frozen_at` | yes | the frozen facts pack; the runner rehashes it before launch |
| `artifact_access` | yes | default `read-only` |
| `result_capture` | yes | default `runner` |
| `output_path` | yes | payload path; differs from artifact and result |
| `result_path` | yes | written by the lead after validation, never by the reviewer |
| `output_schema` | yes | **absolute path** to a JSON Schema (`assets/result.schema.json`) |
| `units` | yes | list of `{id, kind, locator, expected}`; `[]` only for an "open" review; each evidence log is added as a unit |
| `test_evidence` | no | `test_evidence.py` records (id, command, exit code, log sha256, summary); when present the result must cite one |
| `scope` | yes | what is in and out; "others untouched" |
| `acceptance` | yes | itemized, decidable |
| `prior_findings` | yes | closed items from `review_ledger.md`; `[]` for round 1; generated, never hand-written |
| `priority` / `deadline` | no | |

Pre-send: artifact exists and hash/mtime match; every unit locator exists and has an
`expected`; schema path exists; `prior_findings` regenerated from the current ledger; three
paths distinct; capture=runner; facts pack fresh (`facts_pack.py check`); evidence logs exist
and their hashes match.

Generate:

```bash
python scripts/build_envelope.py --artifact src/ --out _work/review/env_r3.yaml \
    --ledger _work/review_ledger.md \
    --unit "api::src/api.py:40-90::handle_request" \
    --unit "retry::src/retry.py::backoff capped" \
    --unit "tests::tests/test_retry.py::" \
    --scope "retry + error handling only" \
    --review-input diffs/PR-214.patch
```

`--units-file` accepts a YAML list or TSV (`id<TAB>locator<TAB>expected<TAB>kind`).
Round 1 only: add `--init-ledger` to create the header. Any later round without a real ledger
fails the build — forgetting the closed items is how duplicates get re-raised.

## Result schema (reviewer → lead)

```json
{
  "request_id": "R20261024-07",
  "artifact_hash": "...",
  "artifact_mtime": "2026-10-24T21:05:32+08:00",
  "units_seen": ["api", "retry"],
  "units_confirmed": [
    {"unit": "api", "observed": "handle_request() at line 42"},
    {"unit": "retry", "observed": "backoff capped at 30 s"}
  ],
  "findings": [
    {"id": "F01", "location": "src/api.py:55", "target": "handle_request",
     "severity": "high", "severity_basis": "unhandled 500 kills the retry loop",
     "issue": "no timeout around the upstream call", "evidence": "line 55 awaits fetch() bare",
     "fix": "wrap in asyncio.wait_for(timeout=...)", "origin": "independent"}
  ],
  "challenges": [],
  "confidence": "high",
  "limitations": ["did not exercise the branch with an expired token"]
}
```

Rules:

- Every finding needs `location / target / severity / severity_basis / issue / evidence / fix / origin`.
  **Missing `severity_basis` is invalid, not "medium"**.
- `origin`: `independent` if the reviewer found it; `advisory-derived` if the reviewed code or
  text came from this same model's earlier draft — that unit is **not independently reviewed**.
- `challenges` re-opens a closed item and requires new evidence; without evidence it is dropped.
- Declare `units_seen`, `units_confirmed`, `limitations`. Every envelope unit must appear in
  both lists; omission fails validation.
- Echo `artifact_mtime`; a mismatch is a wrong version — discard the round.
- A v2 envelope requires `facts_version` (echo it) and, when `test_evidence` is present,
  `test_refs` with at least one entry. Each ref carries the evidence `id`, `command`,
  `exit_code`, `log_sha256` and `observed`: a verbatim excerpt (≥20 chars) that exists in the
  log but **not** in the envelope summary. Copying the envelope cannot pass; the reviewer must
  have read the log. Missing or mismatched refs are invalid, not "medium".
- Machine contract: `assets/result.schema.json`. Check every result before adopting it:

```bash
python scripts/validate_result.py --result _work/review/result_r3.json \
    --envelope _work/review/env_r3.yaml
```

`run_gpt.py --schema ... --result-path ...` runs the same check and exits `3` on failure, so a
non-conforming result never becomes "the review passed".

## Versions, timestamps, and what was actually read

1. Every artifact has `artifact_hash` + `artifact_mtime`; both are recorded with the review.
2. Rebuild → regenerate the units; never reuse stale locators (old line numbers, moved files).
3. Reviewer confirms what it sees at a unit ("handle_request at line 42"); mismatch aborts the round.
4. **Bind findings to claims**, not to a hash: location + excerpt + target.
5. Echoed mtime ≠ envelope → wrong version; re-send, never adopt.
6. **Derived inputs**: reviewers often read a diff, dump, export or render, not the artifact
   itself. Generate it immediately before the call, never reuse an older one, and record
   `sha256(8)` + mtime in the round log (`build_envelope.py --review-input` does it via
   `scripts/session.py note`). Until that is recorded, the delivery note may not claim
   "version bound".

## Provenance (advisory drafts are production)

Rule 2 in SKILL.md covers drafts, not just finished artifacts. If GPT produced an outline, a
design, a scaffold, a patch or any advisory text that the lead adopted, that work is
**advisory-derived**:

- the facts pack / brief lists, per unit, where the content came from (lead / adopted from
  model X round N);
- findings on those units are marked `origin: advisory-derived` and do not count as
  independent review;
- those units are reviewed by another model, or by the same model only with an explicit
  "not independent" declaration in the delivery note;
- if only one model is usable, delivery says "本轮评审非独立（同源）" — an independent gate
  over your own draft is not a gate.

## Plan review gate

A plan is an artifact with its own review. Generate the envelope for `_work/plan/plan.md` and
review it with a different family; then:

```bash
python scripts/plan_gate.py check --envelope _work/plan/env_plan.yaml \
    --result _work/plan/plan.review.json --ledger _work/plan/review_ledger.md
```

The gate validates the result, compares the plan hash/mtime with the envelope and the result,
requires the call record (v2: `protocol_version`, review role, `envelope_sha256`,
`result_sha256`, `artifact_sha256`), checks the planner record (or treats the plan as
lead-authored), and refuses a same-family or unknown-family reviewer. A high/medium finding
without a closed ledger row blocks dispatch. Exit codes: 3 no review, 4 stale/mixed, 5 open
finding, 6 not independent. `run_gpt.py --role produce` and every `models.py call` inferred as
`produce` run this gate first; there is no production waiver.

## Review gate

For each finding: **validate** (`validate_result.py`) → **verify at source** → **decide**
(accept / reject / needs confirmation, one-line reason) → **log** in `review_ledger.md` →
generate the next envelope from that ledger.

Ledger format (parsed by `build_envelope.py`):

```markdown
| id | round | where | status | reason | owner | budget_change |
|---|---|---|---|---|---|---|
| F09 | 1 | docs/arch.md#4.2 | rejected | covered by 2.3 | lead | |
| F07 | 3 | docs/arch.md#4.2 | duplicate-of:F09 | same gap | lead | pages 30→32 |
```

Closed statuses (`rejected`, `duplicate-of:*`, `accepted`) become `prior_findings`; `open`
rows do not. Never equate "the reviewer says so" with "the problem exists".

## Scheduling

- Serial or ≤2 concurrent reviews; split long jobs by unit; batch related units into one call.
- Every review call goes through `scripts/run_gpt.py` (persistent, recorded) — see
  [calling-gpt.md](calling-gpt.md).
- One runner call = one batch; the runner appends its own quota-ledger row.
- Checkpoint each batch; resume from `review_ledger.md`.

## Call evidence

Each round references `_work/calls/<request_id>.json`: it ties the envelope to a session,
a rollout path, the model/effort used, the schema verdict and the quota delta. A finding whose
call record is missing is treated as unreviewed, not as reviewed. When a call was made outside
the runner, state that in the ledger instead of inventing a session id.
