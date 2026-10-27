# Vendored provider registry — provenance and licence

`assets/providers.registry.json` is a copy of
`src/providers/registry.json` from the **dsh-our-free-model** project
(<https://github.com/Ebony-Vinyl/dsh-our-free-model>), version 2.7.2,
retrieved 2026-10-26.

- Licence: MIT (Copyright (c) 2026 Ebony-Vinyl). The full text is in that
  repository's `LICENSE`; the data is reused unmodified except for this notice.
- The upstream file records its own sources per row in the `source` field
  (`fcc` = free-claude-code provider directory, `9router` = 9router provider
  registry, `custom` = that project's own additions). Those upstream projects
  remain the authoritative source for each provider row.
- 76 providers are included: 61 `apikey`, 10 `free-tier`, 3 `local`, 2 `oauth`.
  Each carries the auth kind, wire format, base URL, optional model-listing URL
  and published model ids.

## What this skill does with it

- Reads it as **data only**; nothing in the registry reaches the network by
  itself. `scripts/model_registry.py` resolves ids, endpoints and headers.
- Adds the user's own providers and overrides from
  `<project>/_work/models/config.json` on top.
- Refuses `oauth` / `adc` rows with a reason: their credentials are minted by an
  interactive sign-in or a cloud identity flow that this skill cannot drive.
  They are listed so the market is visible, not because they are callable.

## Updating

Re-copy the upstream file when the reference project's registry changes, then run
`python scripts/selftest_models.py` (it asserts the id grammar and a sample of
the data). Do not edit rows in place: an override belongs in the project config,
which keeps the vendored copy diffable against upstream.
