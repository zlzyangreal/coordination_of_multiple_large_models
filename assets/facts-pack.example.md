# Facts pack — <artifact name>

facts_version: 1
facts_hash: <filled by scripts/facts_pack.py freeze>
frozen_at: <filled by scripts/facts_pack.py freeze>

One file, shared by every model. Frozen before work starts; any scope or budget change goes
through `python scripts/facts_pack.py bump --reason "..."`, which increments the version,
appends a row to `## version log`, and recomputes the hash. A stale pack cannot build an
envelope.

## Goal, audience, deliverable

- Goal:
- Audience / consumer:
- What "done" means in one sentence:
- Where it lives (path, branch, environment):

## constraints — violation means rework

- Interface / API / schema contract that must not change:
- Required behaviour (testable statements):
- Security / privacy rules:
- Banned dependencies, words, numbers or APIs:
- Style / lint rules that are enforced (formatter, linter, naming):
- Mandatory content or sections:

## budgets — overrun is `notice`, never a veto

| budget | value | changed | reason |
|---|---|---|---|
| review_calls | 2 per milestone | | default: round 3 needs --why-unclosed, a raise needs a reason |
| review_minutes | 30 min | | default |
| size (lines / files / pages / PR) | 600 lines | | |
| performance envelope | p95 < 200 ms | | |
| GPT calls | 6 | | |
| deadline | YYYY-MM-DD | | |

Rule: budgets move only **before** production, with the change recorded here (and the review
ledger's `budget_change` column / `review_budget.py raise`). "After seeing the draft" is not a
reason.

## Data, definitions, fixtures

- Key figures / constants and their sources:
- Definitions that must not drift:
- Fixtures, assets, generated inputs (paths):

## Provenance (required when any model produced advisory work)

| unit | source |
|---|---|
| `src/retry.py` | lead |
| `docs/arch.md#2.1` | advisory draft — GPT, round 2, adopted |

Units with an advisory source are reviewed as `origin: advisory-derived`; they are not
independently reviewed unless another model reviews them.

## version log

| version | at | reason |
|---|---|---|
| 1 | YYYY-MM-DDTHH:MM:SS+08:00 | initial freeze |
