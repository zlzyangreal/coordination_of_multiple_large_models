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

## Request envelope (lead → reviewer)

| Field | Required | Notes |
|---|---|---|
| `request_id` | yes | unique |
| `skill_version`, `skill_pinned_at` | yes | which contract built this envelope (session pin) |
| `artifact` | yes | absolute path |
| `version_hash` | yes | SHA256 |
| `artifact_mtime` | yes | ISO 8601 with offset |
| `artifact_access` | yes | default `read-only` |
| `result_capture` | yes | default `runner` |
| `output_path` | yes | payload path; differs from artifact and result |
| `result_path` | yes | written by the lead after validation, never by the reviewer |
| `output_schema` | yes | **absolute path** to a JSON Schema (`assets/result.schema.json`) |
| `page_map` | paginated | page → `p009.png` → expected title |
| `scope` | yes | pages/sections under review; "others untouched" |
| `acceptance` | yes | itemized, decidable |
| `prior_findings` | yes | closed items from `review_ledger.md`; `[]` for round 1; generated, never hand-written |
| `priority` / `deadline` | no | |

Pre-send: artifact exists and hash/mtime match; page map complete and titled; schema path
exists; `prior_findings` regenerated from the current ledger; three paths distinct;
`capture=runner` (or a successful write probe for `direct`).

Generate:

```bash
python scripts/build_envelope.py --artifact report_v6.docx --out _work/review/env_v6.yaml \
    --ledger _work/review_ledger.md --pages 4,8,13 --scope "只审 2.1-2.4" \
    --text-dump _work/v6.dump.txt
```

Round 1 only: add `--init-ledger` to create the header. Any later round without a real
ledger fails the build — forgetting the closed items is how duplicates get re-raised.

## Result schema (reviewer → lead)

```json
{
  "request_id": "R20261024-07",
  "artifact_hash": "...",
  "artifact_mtime": "2026-10-23T23:05:32+08:00",
  "pages_seen": [9, 10],
  "title_confirmed": [{"page": 9, "title": "2.4 Comparison of representative methods"}],
  "findings": [
    {"page": 9, "element": "2.4", "severity": "high",
     "severity_basis": "structural gap reviewers will challenge",
     "issue": "table still present", "evidence": "five-column table visible",
     "fix": "convert to prose", "origin": "independent"}
  ],
  "challenges": [],
  "confidence": "high",
  "limitations": ["lower half of page 10 not rendered"]
}
```

Rules:

- Every finding needs `page / element / severity / severity_basis / issue / evidence / fix / origin`.
  **Missing `severity_basis` is invalid, not "medium"** — the old fallback silently accepted
  non-conforming results; `validate_result.py` now fails them.
- `origin`: `independent` if the reviewer found it on its own; `advisory-derived` if the
  reviewed text came from this same model's earlier draft. The latter means the chapter was
  **not independently reviewed**, whatever the score says.
- `challenges` re-opens a closed item and requires new evidence; without evidence it is dropped.
- Declare `pages_seen`, `title_confirmed`, `limitations`. Every page in `page_map` must appear
  in `title_confirmed` and `pages_seen`; omission fails validation.
- Echo `artifact_mtime`; a mismatch is a wrong version — discard the round.
- Machine contract: `assets/result.schema.json`. Check every result before adopting it:

```bash
python scripts/validate_result.py --result _work/review/result_v6.json \
    --envelope _work/review/env_v6.yaml
```

`run_gpt.py --schema ... --result-path ...` runs the same check and exits `3` on failure, so a
non-conforming result never becomes "the review passed".

## Versions, timestamps, and what was actually read

1. Every artifact has `artifact_hash` + `artifact_mtime`; both are recorded with the review.
2. Rebuild → regenerate `page_map`; never reuse old page numbers.
3. Reviewer confirms "the title I see on page X is …"; mismatch aborts the review.
4. **Bind findings to claims**, not to a hash: excerpt + page + title.
5. Echoed mtime ≠ envelope → wrong version; re-send, never adopt.
6. **Derived text**: the reviewer often reads a `*.dump.txt`, not the artifact. Generate the
   dump immediately after the rebuild, never reuse an older one, and record
   `sha256(8)` + mtime in the round log (`build_envelope.py --text-dump` does it via
   `scripts/session.py note`). Until that is recorded, the delivery note may not claim
   "version bound".

## Provenance (advisory drafts are production)

Rule 2 in SKILL.md covers drafts, not just finished artifacts. If GPT produced an outline, a
route, a background section or any advisory text that the lead adopted, that text is
**advisory-derived**:

- the brief lists, per section, where the text came from (lead / adopted from model X round N);
- findings on those sections are marked `origin: advisory-derived` and do not count as
  independent review;
- those sections are reviewed by another model, or by the same model only with an explicit
  "not independent" declaration in the delivery note;
- if only one model is usable, delivery says "本轮评审非独立（同源）" — an independent gate
  over your own draft is not a gate.

## Review gate

For each finding: **validate** (`validate_result.py`) → **verify at source** → **decide**
(accept / reject / needs confirmation, one-line reason) → **log** in `review_ledger.md` →
generate the next envelope from that ledger.

Ledger format (parsed by `build_envelope.py`):

```markdown
| id | round | page | status | reason | owner | budget_change |
|---|---|---|---|---|---|---|
| F09 | 1 | 12 | rejected | 已有（二）3，不单独成章 | lead | |
| F07 | 3 | 12 | duplicate-of:F09 | 与 F09 同一处 | lead | pages 30→32 |
```

Closed statuses (`rejected`, `duplicate-of:*`, `accepted`) become `prior_findings`; `open`
rows do not. Never equate "the reviewer says so" with "the problem exists".

## Scheduling

- Serial or ≤2 concurrent reviews; split long jobs by page/section; batch images into one call.
- Every review call goes through `scripts/run_gpt.py` (persistent, recorded) — see
  [calling-gpt.md](calling-gpt.md).
- One runner call = one batch; the runner appends its own quota-ledger row.
- Checkpoint each batch; resume from `review_ledger.md`.

## Call evidence

Each round references `_work/calls/<request_id>.json`: it ties the envelope to a session,
a rollout path, the model/effort used, the schema verdict and the quota delta. A finding whose
call record is missing is treated as unreviewed, not as reviewed. When a call was made outside
the runner, state that in the ledger instead of inventing a session id.
