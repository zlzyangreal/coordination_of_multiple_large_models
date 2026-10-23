# Models: registration, verification, routing, budget

## Registry

Model = `slug` + `reasoning`. Never merge them.

| slug | reasoning | role | status | verified |
|---|---|---|---|---|
| `deepseek-account/deepseek-flash` | — | lead / execution | default | 2026-10-23 |
| `gpt-5.6-sol` | `medium` (low…ultra) | vision / review | default | 2026-10-23, codex 0.163 |
| new | — | — | verify first | — |

Unregistered or unverified = unusable. A failed model is never reported as a "fallback".

## Verification gate

1. **Spelling**: slug must exist in `%USERPROFILE%\.codex\models_cache.json` (`models[].slug`). If absent, it is a typo — fix it, do not call.
2. **Smoke test**: 3-second task ("reply ok"); record client version and date.
3. **Auto-register on success**: write config + registry without interrupting the user, without commenting on their spelling, without reporting a "correct name". Escalate only if (a) several candidates match, or (b) the smoke test fails.
4. Append one line to `_work/model_changes.log`: time, user request, written slug, reasoning, result, client version.
5. Always pass `-m` and `model_reasoning_effort`; never rely on config defaults.

## Routing

1. Match capability: vision/layout → strongest visual model; long-form writing → strongest writer; code/format/batch → executor; independent review → different family from the producer.
2. One artifact, at most two producers; more requires explicit boundaries.
3. Per model: ≤2 concurrent calls; serialize when unstable.
4. Shared facts pack; no divergent assumptions.
5. Handoffs must be self-contained — no "as discussed above".

## Budget and stop lines

| Model | Calls | Cap | Stop | Note |
|---|---|---|---|---|
| lead | — | main pool | — | task-level control |
| reviewer | N | N×1.5 | 80% | batch images into one call |

- Review > generation > polish when budget is tight.
- Two consecutive failures on one model: stop retrying, change path.
- ≤3 layout/format passes; afterwards log issues only.
- Final report: calls, token order of magnitude, wait time, remaining budget.
