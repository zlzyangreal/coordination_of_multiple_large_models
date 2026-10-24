# Handoff protocol: envelope, result schema, versions, review gate

## Request envelope (lead → reviewer)

| Field | Required | Notes |
|---|---|---|
| `request_id` | yes | unique |
| `artifact` | yes | absolute path |
| `version_hash` | yes | SHA256 |
| `artifact_mtime` | yes | ISO 8601 with offset |
| `write_permission` | yes | read-only / workspace-write / danger-full-access (review default read-only) |
| `output_path` | yes | payload path; differs from artifact and result_path |
| `result_path` | yes | structured result; differs from artifact and output_path |
| `page_map` | paginated | page → `p009.png` → expected title |
| `scope` | yes | pages/sections under review; "others untouched" |
| `acceptance` | yes | itemized, decidable |
| `output_schema` | yes | default `findings[]` |
| `priority` / `deadline` | no | |

Pre-send: file exists, page count matches, every image has a page number, titles filled, hash + mtime written, three paths distinct, permission matches the task, file not locked.

Generate: `python scripts/build_envelope.py --artifact <f> --pages 4,8 --out <yaml>`.
Example: `assets/envelope.example.yaml`.

## Result schema (reviewer → lead)

```json
{
  "request_id": "R20261021-07",
  "artifact_hash": "...",
  "artifact_mtime": "2026-10-23T23:05:32+08:00",
  "pages_seen": [9, 10],
  "title_confirmed": [{"page": 9, "title": "2.4 Comparison of representative methods"}],
  "findings": [
    {"page": 9, "element": "2.4", "severity": "high", "severity_basis": "structural gap reviewers will challenge",
     "issue": "table still present", "evidence": "five-column table visible", "fix": "convert to prose"}
  ],
  "confidence": "high",
  "limitations": ["lower half of page 10 not rendered"]
}
```

Rules:

- Every finding needs `page / element / severity / severity_basis / issue / evidence / fix`. No evidence → not accepted. Missing `severity_basis` → treat as medium.
- Must declare `pages_seen`, `title_confirmed`, `limitations`. Say "cannot see" instead of guessing.
- Must echo `artifact_mtime`; mismatch = wrong version, discard the round.
- Low-confidence findings go in a separate list.
- Machine contract: `assets/result.schema.json`.

## Versions and timestamps

1. Every artifact has `artifact_hash` + `artifact_mtime`; both are recorded with the review.
2. Rebuild → regenerate `page_map`; never reuse old page numbers. Renders named by page.
3. Reviewer confirms "the title I see on page X is …"; mismatch aborts the review.
4. **Bind findings to claims**, not to a hash: excerpt + page + title. Reusable for 24 h after a hash change, but re-verify at source before accepting. After 24 h or structural change, re-review.
5. Echoed mtime ≠ envelope → wrong version; re-send, never adopt.

## Review gate

For each finding: **verify at source** → **decide** (accept / reject / needs confirmation, one-line reason) → **log** in `review_ledger.md` (id, page, status, owner, reason) → **reply** to the reviewer so closed items are not raised again.

Never equate "the reviewer says so" with "the problem exists". Reviewers never approve their own production.

## Scheduling

- Serial or ≤2 concurrent reviews; split long jobs by page/section; batch images into one call.
- Every review call goes through `scripts/run_gpt.py` (persistent, recorded) — see
  [calling-gpt.md](calling-gpt.md). A hand-written `codex exec` leaves no rollout.
- Connection errors: triage first (environment.md), then keep the job alive 10–20 min.
- Checkpoint each batch; resume from `review_ledger.md`.
- Log every call: purpose, inputs, output path, duration, whether adopted, **and the
  `request_id` / `thread_id` from the call record**.
- Budget per artifact; re-read quota after each batch (quota-routing.md).

## Call evidence

Each round references `_work/calls/<request_id>.json`: it ties the envelope to a session,
a rollout path, the model/effort used and the quota delta. A finding whose call record is
missing is treated as unreviewed, not as reviewed. When a call was made outside the runner,
state that in the ledger instead of inventing a session id.
