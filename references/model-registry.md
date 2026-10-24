# Models: registration, verification, routing, budget

## Registry

Model = `slug` + `reasoning`. Never merge them.

| slug | reasoning | role | status | verified |
|---|---|---|---|---|
| `deepseek-account/deepseek-flash` | — | lead / execution | default | 2026-10-23 |
| `gpt-5.6-sol` | `medium` (low…ultra) | vision / review / production (quota-tiered) | default | 2026-10-23, codex 0.163 |
| new | — | — | verify first | — |

Unregistered or unverified = unusable. A failed model is never reported as a "fallback".

## Verification gate

1. **Spelling**: slug must exist in `%USERPROFILE%\.codex\models_cache.json` (`models[].slug`). If absent, it is a typo — fix it, do not call.
2. **Smoke test**: 3-second task ("reply ok"); record client version and date.
3. **Auto-register on success**: write config + registry without interrupting the user, without commenting on their spelling, without reporting a "correct name". Escalate only if (a) several candidates match, or (b) the smoke test fails.
4. Append one line to `_work/model_changes.log`: time, user request, written slug, reasoning, result, client version.
5. Always pass `-m` and `model_reasoning_effort`; never rely on config defaults.

## Routing

1. Match capability: visual artifacts (UI, charts, screenshots, rendered output) → the only
   multimodal model, at **any** quota level; reasoning-heavy design or long-form authoring →
   strongest reasoning model **while quota allows it**; mechanical code/format/batch → executor;
   independent review → different family from the producer.
2. One artifact, at most two producers; more requires explicit boundaries.
3. Per model: ≤2 concurrent calls; serialize when unstable.
4. Shared facts pack; no divergent assumptions.
5. Handoffs must be self-contained — no "as discussed above".

## Budget: quota-tiered

GPT's share is not fixed. Read the pool, then allocate:

| Tier | Lead | GPT | Calls |
|---|---|---|---|
| S / A | deterministic parts, integration | production + gates (S also gets a second pass) | one per artifact / group |
| B | all production | gates only, batched | batch units and artifacts |
| C | all text/code work | visual units only | batch all images into one call |
| R | everything | one visual gate, then stop | one call |

- `model_reasoning_effort` stays at the configured level (`medium`); tiers change who produces
  and how many calls, not effort. Only tier R may drop one notch for a single gate, recorded in
  the ledger.
- Read before routing and re-read after every batch: `python scripts/quota.py`; bands,
  burn/pace/weekly overrides, reserve and worked examples live in [quota-routing.md](quota-routing.md).
- Review > generation > polish when the pool is tight; at S the order flips — produce first,
  because expiring quality is worth more than a third polish pass.
- Mechanical edits, scripts, file ops: always the lead, at every tier.
- Two consecutive failures on one model: stop retrying, change path.
- ≤3 formatting/restructure passes; afterwards log issues only.
- Final report: 5 h and weekly used start→end, calls, tier path, wait time, wasted capacity.
