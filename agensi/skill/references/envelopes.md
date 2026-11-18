# Envelopes and adoption

An envelope is the whole request. If it is incomplete, the review is not a review - it is an
opinion about whatever the model happened to read.

## Request envelope fields

| Field | Why it is required |
|---|---|
| `request_id` | One id per call; the result echoes it; a reused id is a defect |
| `role` | `plan`, `produce`, `review`, `plan-review` - changes the expectations |
| `artifact` + hash + mtime | Fixes the version under review |
| `workspace` | Absolute root, so paths cannot be resolved against a different tree |
| `facts_version` | Which frozen facts the reviewer is judging against |
| `criteria` | Acceptance tests, not adjectives ("handles errors" is not a criterion) |
| `units` | The exact files and line ranges in scope |
| `evidence` | Test command, exit code, log hash |
| `output_format` | Usually a JSON schema - a free-form reply invites paraphrase |
| `read_only` | Explicit: the reviewer writes nothing |

## Sending rules

- Send the artifact, not the producer's chat history. The producer's self-assessment is not input.
- Rebuild the artifact from disk at send time; never from a buffer you think is current.
- If the envelope exceeds the model's context, split by unit. Never truncate silently; state which
  units were omitted and mark the review partial.
- One artifact version per round. Two versions in one envelope produces findings that cannot be
  attributed.

## Reading the result

1. **Reject invalid results first.** No `verdict`, no `severity_basis`, no `units_confirmed` ->
   invalid. Do not "interpret" it into validity.
2. **Check the version echo.** Hash and mtime must match what you sent. Mismatch -> discard.
3. **Verify at source, one finding at a time.** Open the cited lines at the current version. The
   claim may be true of the previous version, a related file, or nowhere.
4. **Classify each finding**: `accepted` (fix), `rejected` (why it does not apply, in one line),
   `deferred` (owner + trigger), `unverifiable` (could not locate).
5. **Log the decision**, not just the outcome. The next round starts from this log.

## Common failure modes

- **The agreeing reviewer.** Same family as the producer, or given the producer's rationale. It
  returns compliments and a "looks good". Independent family plus artifact-only input fixes it.
- **The moving target.** Reviewer reads a file the producer is still editing. Fix with a frozen
  copy or a lock; the hash check catches it.
- **The unbounded scope.** "Review the project" yields generic advice. Units and criteria bound it.
- **The hiding severity.** A result that labels everything `medium` has no severity basis. Require
  the basis, not just the label.
- **The orphan finding.** A real problem, but not in scope. Record it; do not let it silently expand
  the milestone.
