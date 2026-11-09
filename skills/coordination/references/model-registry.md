# Models: registration, verification, routing, budget

## Lanes

The skill has two protected lanes and any number of optional HTTP lanes. The full
grammar, credential rules and failure matrix live in
[model-access.md](model-access.md); this page is the routing summary.

| id | lane | role default | notes |
|---|---|---|---|
| `codex:auto` / `codex:<slug>` | GPT via `scripts/run_gpt.py` | review, vision | protected: one call path, persistent, quota-accounted |
| `dsh:auto` / `dsh:<slug>` | DeepSeek via the DSH host session | lead, mechanical | protected: the lead session itself; scripts never spawn it |
| `<provider>/<model>` | HTTP (registry or custom) | none | optional; free/keyless, API key, custom base URL, local, account token |
| `chain:<name>` | ordered list of the above | none | virtual id for `models.py call --chain` |

`auto` means "resolve from the host tool's own config": for codex that is
`~/.codex/config.toml` (model + effort), for dsh it is the session running the skill.
Do not bake a slug into the role when the host already configures one.

## Registry

Model = `slug` (or `provider/model`) + reasoning. Never merge them.

| id | reasoning | role | status | verified |
|---|---|---|---|---|
| `dsh:auto` | — | lead / execution | default | the session itself |
| `codex:auto` | config (`medium`/`high`) | vision / review / production (quota-tiered) | default | `models.py test codex:auto` (runner probe) |
| `<provider>/<model>` | provider default | opt-in | unverified until `models.py test` | `models.py discover` + `list` |

Unverified is not the same as callable-for-everything: an HTTP row with no successful
probe or call is usable for ordinary work but is **not** eligible for the `review` role
until `models.py test` or a successful call records health. A failed probe leaves the
provider unverified (the router skips it for review) without banning it from ordinary
calls; repeated call failures cool the model or provider. Every add/switch/remove is
appended to `_work/models/model_changes.log`.

## Verification gate

1. **Spelling / existence**: for the codex lane, the slug must exist in
   `%USERPROFILE%\.codex\models_cache.json` (`models[].slug`); a typo is fixed, not
   called. For an HTTP row, the id must appear in the project catalog
   (`models.py list --json`): configured `models`, a discovered listing, or the
   registry's published rows.
2. **Smoke test**: `models.py test <provider|model>` uses the model listing (no
   generation spent); `--generate` spends one small completion when the provider
   publishes no listing. The codex lane's probe is delegated to `run_gpt.py --probe`
   (ephemeral by design — connectivity only).
3. **Auto-register on success**: `models.py add` / `key set` write the project config
   and 0600 store without interrupting the user; `discover` records the live roster.
   Escalate only when a credential is missing, an OAuth-only provider is chosen, or
   the smoke test fails.
4. Append one line to `_work/models/model_changes.log` for a model switch or a new
   provider (time, request, id, reasoning, result, client version).
5. Always pass the model explicitly where the client supports it: `run_gpt.py -m/--model`
   plus `--effort`, and the HTTP body's `model`. Never rely on config defaults for a
   review call.

## Routing

1. **Planning is scored, dispatching is not.** `route --role plan` ranks by reasoning
   (0.35) / design (0.30) / agentic (0.20) / coding (0.15), not by quota or habit. The
   planner produces the executable plan; the lead (dispatcher) owns the board and
   assignments. The plan is reviewed by a different family before dispatch.
2. **Pin wins, exactly.** `models.py route --pin <id>` either returns that id or exits
   3 with the reason; it never substitutes a different provider. `models.py use <role>
   <id>` is the persistent form.
3. **Protected defaults.** `lead`/`mechanical` → dsh; `review`/`vision` → codex.
   A provider may replace them only by an explicit role switch or pin.
4. **Capability match**: visual units → a model that declares vision (the codex lane by
   default; HTTP rows need `vision: true`); reasoning-heavy design/long-form → the
   strongest reasoning model while quota allows; mechanical work → the lead; independent
   review → a different family from the producer.
5. **Independence**: `route --role review --producer <id>` excludes the producer's
   family and excludes rows whose family cannot be established (aggregators). Declare
   `overrides.<model>.family` for a private model; `--allow-unknown-family` accepts the
   risk and must be declared at delivery.
6. One artifact, at most two producers; more requires explicit boundaries.
7. Per model: ≤2 concurrent calls; serialize when unstable.
8. Shared facts pack; no divergent assumptions.
9. Handoffs must be self-contained — no "as discussed above".

## Budget: quota-tiered

GPT's share is not fixed. Read the pool, then allocate:

| Tier | Lead | GPT | Calls |
|---|---|---|---|
| S / A | deterministic parts, integration | production + gates (S also gets a second pass) | one per artifact / group |
| B | all production | gates only, batched | batch units and artifacts |
| C | all text/code work | visual units only | batch all images into one call |
| R | everything | one visual gate, then stop | one call |

- `models.py route --role produce` applies this table to choose who produces: S/A →
  codex produces, B/C/R/U → the lead produces (and `call` refuses the dsh link, since
  the session is the producer).
- `model_reasoning_effort` stays at the configured level; tiers change who produces
  and how many calls, not effort. Only tier R may drop one notch for a single gate,
  recorded in the ledger.
- Read before routing and re-read after every batch: `python scripts/quota.py`; bands,
  burn/pace/weekly overrides, reserve and worked examples live in
  [quota-routing.md](quota-routing.md).
- HTTP providers have no meters here: their declared `limits` are counted locally
  (`models.py budget`), and an unknown quota is "unknown", never "unlimited".
- Review > generation > polish when the pool is tight; at S the order flips — produce
  first, because expiring quality is worth more than a third polish pass.
- Mechanical edits, scripts, file ops: always the lead, at every tier.
- Two consecutive failures on one model: stop retrying, change path. Cooldowns and the
  per-model call log live in `_work/models/state.json` and `model_calls.jsonl`.
- ≤3 formatting/restructure passes; afterwards log issues only.
- Final report: 5 h and weekly used start→end, calls, tier path, wait time, wasted
  capacity, and (when HTTP lanes were used) which provider produced or reviewed.
