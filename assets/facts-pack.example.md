# Facts pack — <artifact name>

Frozen: YYYY-MM-DD HH:MM. One file, shared by every model.

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
| size (lines / files / pages / PR) | 600 lines | 2026-10-24: 600 → 750 | retry path needed its own module |
| performance envelope | p95 < 200 ms | | |
| GPT calls | 6 | | |
| deadline | YYYY-MM-DD | | |

Rule: budgets move only **before** production, with the change recorded here and in the
review ledger's `budget_change` column. "After seeing the draft" is not a reason.

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
