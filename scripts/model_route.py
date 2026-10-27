#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Catalog, routing and named chains across every lane.

The lanes are deliberately different things and this module never pretends
otherwise:

    codex:<slug>    GPT through the codex CLI, called by scripts/run_gpt.py
    dsh:<slug>      DeepSeek through the DSH host session 鈥?the lead itself
    <provider>/<model>   an OpenAI-compatible / Anthropic endpoint over HTTP

`auto` is a first-class id: ``codex:auto`` means "whatever
``~/.codex/config.toml`` is configured with" and ``dsh:auto`` means "the lead
session". Both keep the pre-existing call paths exactly as they were; adding a
provider never changes them, and routing only leaves a protected lane when that
lane is unavailable, out of budget, or lacks a required capability 鈥?and says so
in ``notes`` when it does.

Selection inputs, in order: an explicit pin, the role's configured model, the
quota tier (codex lane only), capability requirements (vision, context),
independence (a reviewer must be a different family from the producer), declared
daily budgets, health/cooldowns, and finally the user's preference list.
"""
import os
import sys

import model_config as cfg_mod
import model_registry as registry

PROTECTED = {"lead": "dsh", "mechanical": "dsh", "review": "codex", "vision": "codex"}
ROLE_NAMES = ("lead", "plan", "produce", "review", "vision", "mechanical")

#: Planning is a different job from dispatching: the planner must understand the
#: requirement, research the field, compare open source, design the architecture,
#: choose a stack and produce an executable plan. These four dimensions decide who
#: plans; the weights say what matters most when they disagree.
PLANNING_WEIGHTS = {"reasoning": 0.35, "design": 0.30, "agentic": 0.20, "coding": 0.15}

#: Conservative defaults when the project declares no scores. They are heuristics
#: about *general* model strength, not measured passes; a project that cares should
#: set `overrides.<model>.scores` and the source is reported as `config`.
PLANNING_DEFAULTS = {
    "openai": {"reasoning": 5, "design": 5, "agentic": 5, "coding": 5},
    "anthropic": {"reasoning": 4, "design": 5, "agentic": 4, "coding": 5},
    "deepseek": {"reasoning": 4, "design": 4, "agentic": 4, "coding": 5},
    "google": {"reasoning": 4, "design": 4, "agentic": 4, "coding": 4},
}
LANE_PLANNING = {"codex": PLANNING_DEFAULTS["openai"],
                 "dsh": PLANNING_DEFAULTS["deepseek"]}
NEUTRAL_PLANNING = {"reasoning": 3, "design": 3, "agentic": 3, "coding": 3}


def _score(value, fallback):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(0.0, min(5.0, number))


def planning_scores(entry, cfg):
    """Reasoning/design/agentic/coding scores for one catalog entry.

    Priority: explicit `overrides.<model>.scores` > lane default > family default >
    neutral 3. The weighted value is what `route --role plan` sorts by.
    """
    lane = entry.get("lane")
    base = dict(LANE_PLANNING.get(lane) or
                PLANNING_DEFAULTS.get(entry.get("family")) or NEUTRAL_PLANNING)
    source = "lane" if lane in LANE_PLANNING else (
        "family" if entry.get("family") in PLANNING_DEFAULTS else "neutral")
    if lane == "http":
        setting = ((cfg.get("providers") or {}).get(entry.get("provider")) or {})
        override = (((setting.get("overrides") or {}).get(entry.get("model")) or {})
                    .get("scores") or {})
        if isinstance(override, dict):
            for key in ("reasoning", "design", "agentic", "coding"):
                if key in override:
                    base[key] = _score(override[key], base[key])
                    source = "config"
    weighted = sum(base[key] * PLANNING_WEIGHTS[key] for key in PLANNING_WEIGHTS)
    return {**{key: round(base[key], 1) for key in base},
            "weighted": round(weighted, 2), "source": source}



class RouteError(Exception):
    """No usable candidate exists; the message explains every exclusion."""


def _text(value):
    return str(value or "").strip()


def setting_for(provider_id, cfg):
    return (cfg.get("providers") or {}).get(provider_id) or {}


def provider_record(provider_id, cfg):
    """The record used to build requests: the vendored row, plus a config override.

    A provider that exists only in the project config (a custom base URL, a local
    server, a company gateway) gets a record synthesised from its setting, so the
    same code path serves both.
    """
    stored = registry.provider(provider_id)
    setting = setting_for(provider_id, cfg)
    if stored is not None:
        record = dict(stored)
    elif setting:
        record = {
            "id": provider_id,
            "name": _text(setting.get("name")) or provider_id,
            "category": "custom",
            "format": _text(setting.get("format")) or "openai-chat",
            "baseUrl": _text(setting.get("baseUrl")),
            "modelsUrl": _text(setting.get("modelsUrl")) or None,
            "chatPath": _text(setting.get("chatPath")) or None,
            "chatUrl": _text(setting.get("chatUrl")) or None,
            "auth": dict(setting.get("auth") or {"kind": "none"}),
            "models": [],
            "local": bool(setting.get("local")),
            "headers": dict(setting.get("headers") or {}),
        }
    else:
        return None
    if setting:
        for key in ("format", "baseUrl", "modelsUrl", "headers", "chatPath", "chatUrl"):
            value = setting.get(key)
            if value not in (None, "", {}):
                record[key] = value
        if isinstance(setting.get("auth"), dict):
            record["auth"] = dict(setting["auth"])
    return record


def _models_for(provider_id, record, setting, state):
    """Model ids for one provider and where they came from.

    Priority: an explicit `models` list (the user's contract), then a discovered
    listing, then the registry's published rows. A discovered list is a cache,
    not a contract: it never overrides what the user wrote.
    """
    configured = [_text(m) for m in (setting.get("models") or []) if _text(m)]
    if configured:
        return configured, "config"
    found = cfg_mod.discovered_models(state, provider_id)
    if found:
        return found, "discovered"
    published = [_text(row.get("id")) for row in (record.get("models") or [])
                 if _text(row.get("id"))]
    return published, "registry"


def _caps(record, model_id, setting):
    caps = registry.capabilities(record, model_id)
    overrides = ((setting.get("overrides") or {}).get(model_id) or {})
    for key in ("vision", "reasoning", "contextWindow"):
        if key in overrides:
            caps[key] = overrides[key]
    if "maxOutput" in overrides:
        caps["maxOutput"] = overrides["maxOutput"]
    return caps


def catalog(cfg, state, project="."):
    """Every configured model as one entry, usable or not (with the reason).

    An entry is returned even when it cannot be called: ``models.py list`` and a
    router failure both need to say *why*, and hiding an unconfigured provider
    turns "no key" into "the skill is broken".
    """
    entries = []
    store = cfg_mod.load_credentials(project)
    lanes = cfg.get("lanes") or {}
    team = cfg_mod.load_team(project)
    members = set((team or {}).get("added") or {}) if team else None

    for lane in ("codex", "dsh"):
        lane_cfg = lanes.get(lane) or {}
        enabled = lane_cfg.get("enabled") is not False
        for slug in lane_cfg.get("models") or ["auto"]:
            slug = _text(slug) or "auto"
            entry = {
                "id": f"{lane}:{slug}",
                "lane": lane,
                "provider": lane,
                "model": None if slug == "auto" else slug,
                "family": registry.family(lane),
                "familyKnown": True,
                "familySource": "lane",
                "label": ("codex default (from ~/.codex/config.toml)"
                          if lane == "codex" and slug == "auto"
                          else "lead session (dsh)" if lane == "dsh" and slug == "auto"
                          else slug),
                "format": "cli",
                "effort": lane_cfg.get("effort"),
                "source": "default" if slug == "auto" else "config",
                "configured": enabled,
                "usable": enabled,
                "reason": None if enabled else f"lane {lane!r} is disabled",
                "inTeam": True,
                "capabilities": {
                    "vision": lane == "codex",
                    "reasoning": True,
                    "contextWindow": 400000 if lane == "codex" else 131072,
                    "maxOutput": 128000 if lane == "codex" else 32768,
                },
            }
            entry["planning"] = planning_scores(entry, cfg)
            entry["planningSource"] = entry["planning"]["source"]
            entries.append(entry)

    provider_ids = []
    for pid, setting in (cfg.get("providers") or {}).items():
        if not isinstance(setting, dict) or setting.get("enabled") is False:
            continue
        if registry.provider(pid) is not None or setting.get("baseUrl"):
            provider_ids.append(pid)
    for pid in provider_ids:
        setting = setting_for(pid, cfg)
        record = provider_record(pid, cfg)
        if record is None:
            continue
        models, source = _models_for(pid, record, setting, state)
        blocked = registry.undrivable(record)
        key_source = None
        if blocked is None:
            # `execute=False`: listing/route/doctor must not run a credential command
            # from a project config. A declared command counts as configured; it
            # runs only for an explicit call or test.
            key, key_source, key_error = cfg_mod.api_key(
                record, setting, project, store, execute=False)
            auth_kind = str((record.get("auth") or {}).get("kind") or "none")
            needs_key = auth_kind in ("apiKey", "token")
            declared = cfg_mod.credential_declared(setting)
            if key_error:
                blocked = key_error
            elif needs_key and not key and not declared:
                blocked = (f"no credential for {pid}: run `models.py key set {pid}` or "
                           f"expose {((record.get('auth') or {}).get('env')) or 'a key env var'}")
        else:
            key = None
        endpoint = registry.chat_url(record, setting.get("baseUrl"))
        if blocked is None and endpoint is None:
            blocked = (f"no dialable endpoint for {pid}; set `baseUrl` in "
                       f"_work/models/config.json")
        budget = cfg_mod.budget_block(setting, state, pid)
        if blocked is None and budget:
            blocked = budget
        provider_cooling = cfg_mod.cooling(state, pid) if blocked is None else 0
        if blocked is None and provider_cooling:
            health = cfg_mod.provider_health(state, pid)
            blocked = (f"cooling down for {cfg_mod.seconds_to_text(provider_cooling)} after "
                       f"{health.get('kind') or 'a failure'}: {health.get('reason') or ''}".strip())
        allow_protected = setting.get("allowProtectedFamily") is True
        for model_id in models:
            caps = _caps(record, model_id, setting)
            override = ((setting.get("overrides") or {}).get(model_id) or {})
            inferred = registry.family_info(pid, record, model_id)
            # The protected bit is inferred from the model/provider only. A family
            # override may relabel a row for independence analysis, but it must not
            # be able to make an HTTP GPT/DeepSeek call look unprotected.
            protected_family = inferred["family"] in ("openai", "deepseek")
            family, family_known, family_source = (inferred["family"], inferred["known"],
                                                   inferred["source"])
            if isinstance(override.get("family"), str) and override["family"].strip():
                family, family_known, family_source = override["family"].strip(), True, "config"
            reason = blocked
            entry_id = f"{pid}/{model_id}"
            in_team = members is None or entry_id in members
            if reason is None and not in_team:
                reason = ("not in this task's team; run `models.py team scan` and approve "
                          "it with `models.py team add <id>` (or `team decline <id>`)")
            if reason is None and protected_family and not allow_protected:
                reason = (f"protected family {inferred['family']!r}: use the "
                          f"{'codex' if inferred['family'] == 'openai' else 'dsh'} lane, "
                          f"or set `allowProtectedFamily: true` on {pid} to call it over "
                          f"HTTP deliberately")
            if reason is None:
                left = cfg_mod.cooling(state, pid, model_id)
                if left:
                    model_health = (state.get("models", {})
                                    .get(f"{pid}/{model_id}") or {})
                    reason = (f"model cooling down for {cfg_mod.seconds_to_text(left)} "
                              f"after {model_health.get('kind') or 'a failure'}")
            entry = {
                "id": f"{pid}/{model_id}",
                "lane": "http",
                "provider": pid,
                "model": model_id,
                "inTeam": in_team,
                "family": family,
                "familyKnown": family_known,
                "familySource": family_source,
                "protectedFamily": protected_family,
                "label": _text((registry.model_row(record, model_id) or {}).get("name"))
                         or model_id,
                "format": record.get("format"),
                "source": source,
                "configured": True,
                "usable": reason is None,
                "reason": reason,
                "capabilities": caps,
                "planning": planning_scores(
                    {"lane": "http", "family": family, "provider": pid,
                     "model": model_id}, cfg),
                "credentialSource": key_source,
                "category": record.get("category"),
                "local": bool(record.get("local")),
                "allowProtectedFamily": allow_protected,
            }
            entry["planningSource"] = entry["planning"]["source"]
            entries.append(entry)
    return entries


def find(entries, model_id):
    text = _text(model_id)
    for entry in entries:
        if entry["id"] == text:
            return entry
    return None


def usable_entries(entries, needs=None):
    """Filter by usability and capability, keeping excluded entries with reasons."""
    needs = needs or {}
    kept, dropped = [], []
    for entry in entries:
        if not entry.get("usable"):
            dropped.append((entry["id"], entry.get("reason") or "unusable"))
            continue
        caps = entry.get("capabilities") or {}
        if needs.get("vision") and not caps.get("vision"):
            dropped.append((entry["id"], "declares no vision capability"))
            continue
        if needs.get("reasoning") and not caps.get("reasoning"):
            dropped.append((entry["id"], "declares no reasoning capability"))
            continue
        if needs.get("minContext") and int(caps.get("contextWindow") or 0) < int(needs["minContext"]):
            dropped.append((entry["id"], f"context window {caps.get('contextWindow')} "
                                         f"< required {needs['minContext']}"))
            continue
        kept.append(entry)
    return kept, dropped


def _health_rank(state, entry):
    if entry["lane"] in ("codex", "dsh"):
        return 2  # the host tool either works or the session would not be running
    health = cfg_mod.provider_health(state, entry["provider"])
    if health["status"] == "ok":
        return 2
    return 1  # unknown: usable but unverified


def _budget_rank(cfg, state, entry):
    """1.0 when unconstrained, else remaining share of the declared daily limit."""
    if entry["lane"] in ("codex", "dsh"):
        return 1.0
    setting = setting_for(entry["provider"], cfg)
    limits = setting.get("limits") or {}
    used = cfg_mod.usage_today(state, entry["provider"])
    ranks = []
    calls = limits.get("callsPerDay")
    if isinstance(calls, int) and calls > 0:
        ranks.append(max(0.0, 1.0 - used["calls"] / calls))
    tokens = limits.get("tokensPerDay")
    if isinstance(tokens, int) and tokens > 0:
        ranks.append(max(0.0, 1.0 - used["tokens"] / tokens))
    return min(ranks) if ranks else 1.0


def _preference(cfg, entry):
    order = [_text(x) for x in (cfg.get("preference") or [])]
    for index, item in enumerate(order):
        if item in (entry["provider"], entry["id"]):
            return index
    return len(order) + 1


def quota_snapshot(cfg, state, project, read=True, force=False):
    """Codex quota reading, memoised in state for two minutes.

    The live reader spawns `codex app-server` (~2.5 s); a routing decision must
    not pay that on every call. An expired memo is not extrapolated: the caller
    gets the reading with its own timestamp and the tier it implies.
    """
    memo = ((state.get("quota") or {}).get("codex")) or {}
    at = cfg_mod.parse_ts(memo.get("at"))
    if not force and at and (cfg_mod.now_utc() - at).total_seconds() < 120:
        return memo.get("data") or {}
    if not read:
        return memo.get("data") or {}
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import quota as quota_mod  # noqa: PLC0415
        data = quota_mod.collect("auto")
        tier, pace, cap = quota_mod.classify(
            data.get("remaining"), data.get("hours_left"), data.get("weekly_remaining"))
        data = dict(data, tier=tier[0], tierLabel=tier[2], pace=pace, weeklyCap=cap)
    except Exception:  # noqa: BLE001 - routing must survive a broken quota reader
        return memo.get("data") or {}
    fresh = {"at": cfg_mod.iso(), "data": data}
    cfg_mod.mutate(project, lambda current: current.setdefault("quota", {})
                   .__setitem__("codex", fresh))
    state.setdefault("quota", {})["codex"] = fresh
    return data


def route(cfg, state, role, needs=None, exclude_families=(), pin=None, project=".",
          read_quota=True, force_quota=False, max_fallback=None,
          allow_unknown_family=False):
    """Pick a primary model and an ordered fallback list for one role.

    Returns ``{"role", "primary", "fallback", "notes", "excluded", "quota"}``.
    Raises RouteError when nothing usable remains, listing why each candidate fell.
    """
    if role not in ROLE_NAMES:
        raise RouteError(f"unknown role {role!r} (known: {', '.join(ROLE_NAMES)})")
    if role == "plan":
        # Planning is scored, not budgeted: reading the codex pool here would only
        # add a subprocess and a state write to a decision quota does not affect.
        read_quota = False
    needs = dict(needs or {})
    notes = []
    entries = catalog(cfg, state, project)
    defaults = cfg.get("defaults") or {}
    limit = int(max_fallback if max_fallback is not None
                else defaults.get("maxFallback") or 3)

    quota = quota_snapshot(cfg, state, project, read=read_quota, force=force_quota)
    tier = quota.get("tier")

    role_model = _text(((cfg.get("roles") or {}).get(role) or {}).get("model"))
    if pin:
        preferred = _text(pin)
    elif role_model and role_model != "auto":
        preferred = role_model
    else:
        preferred = None

    if role == "produce" and preferred is None:
        # The quota tier decides who produces, exactly as quota-routing.md says:
        # surplus/normal means GPT produces; conserve and below means the lead does.
        if tier in ("S", "A") and _lane_enabled(cfg, "codex"):
            preferred = _codex_default(cfg)
            notes.append(f"tier {tier}: codex produces; the lead integrates")
        else:
            preferred = _dsh_default(cfg)
            notes.append(f"tier {tier or 'U'}: the lead produces"
                         + ("; text review waits for a reset" if tier in ("C", "R")
                            else ""))
    if preferred is None:
        lane = PROTECTED.get(role)
        preferred = _codex_default(cfg) if lane == "codex" else \
            _dsh_default(cfg) if lane == "dsh" else None

    if role == "vision":
        needs["vision"] = True

    kept, dropped = usable_entries(entries, needs)
    excluded = list(dropped)

    if role == "review":
        # A review is the one gate that cannot be "best effort": an untested HTTP
        # provider may advertise a model that fails on contact, so it is not a
        # review default until `models.py test` or a successful call says so.
        unverified = [entry for entry in kept
                      if entry["lane"] == "http"
                      and cfg_mod.provider_health(state, entry["provider"])["status"] != "ok"]
        for entry in unverified:
            excluded.append((entry["id"], "provider not verified; run "
                                         f"`models.py test {entry['provider']}`"))
        kept = [entry for entry in kept if entry not in unverified]

    # Independence cannot be asserted for an unknown family. A conservative
    # router excludes those rows from `review` rather than letting a GPT row
    # behind an aggregator pose as an independent reviewer.
    if role == "review" and not allow_unknown_family:
        unknown = [entry for entry in kept if entry.get("familyKnown") is False]
        for entry in unknown:
            excluded.append((entry["id"], "family unknown, so independence cannot be "
                                         "proven; declare overrides.<model>.family or "
                                         "pass --allow-unknown-family"))
        kept = [entry for entry in kept if entry.get("familyKnown") is not False]

    def family_ok(entry):
        return entry["family"] not in set(exclude_families)

    if exclude_families:
        blocked = [entry for entry in kept if not family_ok(entry)]
        for entry in blocked:
            excluded.append((entry["id"], "same family as the producer "
                                          f"({entry['family']}); not an independent review"))
        kept = [entry for entry in kept if family_ok(entry)]

    if pin:
        target = _text(pin)
        entry = find(entries, target)
        if entry is None:
            raise RouteError(f"--pin {target!r} is not in this project's catalog; "
                             f"`models.py list` shows the ids")
        if not entry.get("usable"):
            raise RouteError(f"--pin {target!r} is unusable: {entry.get('reason')}")
        reason = next((why for mid, why in excluded if mid == target), None)
        if reason:
            raise RouteError(f"--pin {target!r} is filtered out: {reason}")
        if needs.get("vision") and not (entry.get("capabilities") or {}).get("vision"):
            raise RouteError(f"--pin {target!r} cannot see images")
        if needs.get("reasoning") and not (entry.get("capabilities") or {}).get("reasoning"):
            raise RouteError(f"--pin {target!r} declares no reasoning capability")
        if needs.get("minContext") and int((entry.get("capabilities") or {})
                                           .get("contextWindow") or 0) < int(needs["minContext"]):
            raise RouteError(f"--pin {target!r} has a smaller context window than "
                             f"{needs['minContext']}")
        notes.append(f"pinned to {target} exactly; no fallback is offered")
        return {
            "role": role,
            "needs": needs,
            "primary": entry,
            "fallback": [],
            "notes": notes,
            "excluded": [{"id": mid, "reason": why} for mid, why in excluded],
            "quota": {key: quota.get(key) for key in
                      ("source", "remaining", "hours_left", "weekly_remaining", "tier",
                       "stale", "stale_reason")} if quota else {},
        }

    if preferred:
        preferred_entry = find(entries, preferred)
        if preferred_entry is None:
            notes.append(f"configured model {preferred!r} is not in the catalog")
        elif not preferred_entry.get("usable"):
            notes.append(f"{preferred} is unusable: {preferred_entry.get('reason')}")
        elif preferred_entry not in kept:
            reason = next((why for mid, why in excluded if mid == preferred), "filtered out")
            notes.append(f"{preferred} is filtered out: {reason}")
        elif kept:
            kept = [preferred_entry] + [entry for entry in kept
                                        if entry["id"] != preferred_entry["id"]]
            notes.append(f"primary follows the configured model {preferred}")

    if role == "plan" and not pin:
        # Planning is scored, not inherited: reasoning, design, agentic and coding
        # decide the planner. An explicit role model still wins (it is the user's
        # decision), but nothing else is reordered by lane or quota here.
        chosen = find(entries, preferred) if preferred else None
        if chosen is not None and chosen.get("usable") and chosen in kept:
            ordered = [chosen] + sorted(
                [entry for entry in kept if entry["id"] != chosen["id"]],
                key=lambda entry: (-entry["planning"]["weighted"],
                                   -_health_rank(state, entry),
                                   _preference(cfg, entry), entry["id"]))
            notes.append(f"planner follows the configured model {preferred}")
        else:
            ordered = sorted(
                kept,
                key=lambda entry: (-entry["planning"]["weighted"],
                                   -_health_rank(state, entry),
                                   _preference(cfg, entry), entry["id"]))
            if ordered:
                notes.append("planner chosen by reasoning/design/agentic/coding scores")
        if not ordered:
            detail = "; ".join(f"{mid}: {why}" for mid, why in excluded[:8]) or \
                "no providers configured"
            raise RouteError(f"no usable planner ({detail})")
        primary = ordered[0]
        fallback = [entry for entry in ordered[1:1 + max(0, limit)]
                    if entry["lane"] != "dsh"]
        if primary["lane"] == "dsh":
            notes.append("the planner is the lead session; models.py plan will write the "
                         "brief for the session to draft, not spawn a caller")
        scores = primary.get("planning") or {}
        notes.append(f"scores r={scores.get('reasoning')} d={scores.get('design')} "
                     f"a={scores.get('agentic')} c={scores.get('coding')} "
                     f"(weighted {scores.get('weighted')}, source "
                     f"{scores.get('source')})")
        return {
            "role": role,
            "needs": needs,
            "primary": primary,
            "fallback": fallback,
            "notes": notes,
            "excluded": [{"id": mid, "reason": why} for mid, why in excluded],
            "quota": {key: quota.get(key) for key in
                      ("source", "remaining", "hours_left", "weekly_remaining", "tier",
                       "stale", "stale_reason")} if quota else {},
        }

    protected_lane = PROTECTED.get(role)
    # The protected lane keeps the head only when the user has not pointed the role
    # somewhere else. An explicit `models.py use review <id>` wins; moving codex
    # back in front would make the switch command a lie.
    preferred_entry = find(entries, preferred) if preferred else None
    preferred_lane = preferred_entry["lane"] if preferred_entry else None
    if not pin and protected_lane and preferred_lane in (None, protected_lane):
        head = [entry for entry in kept if entry["lane"] == protected_lane]
        rest = [entry for entry in kept if entry["lane"] != protected_lane]
        if head and kept and kept[0]["lane"] != protected_lane:
            notes.append(f"{protected_lane} lane kept ahead of other lanes for role {role!r}")
        kept = head + rest
    elif not pin and protected_lane and preferred_lane != protected_lane:
        notes.append(f"role {role!r} is pointed at {preferred}; the protected "
                     f"{protected_lane} lane is available but not used")

    def sort_key(entry):
        return (
            -_health_rank(state, entry),
            -_budget_rank(cfg, state, entry),
            _preference(cfg, entry),
            -int(bool((entry.get("capabilities") or {}).get("vision"))),
            -int((entry.get("capabilities") or {}).get("contextWindow") or 0),
            entry["id"],
        )

    # The preferred entry keeps the head position; the tail is ranked.
    head = kept[:1]
    tail = sorted(kept[1:], key=sort_key) if len(kept) > 1 else []
    ordered = head + tail

    if not ordered:
        detail = "; ".join(f"{mid}: {why}" for mid, why in excluded[:8]) or "no providers configured"
        raise RouteError(f"no usable model for role {role!r} ({detail})")

    primary = ordered[0]
    fallback = ordered[1:1 + max(0, limit)]
    # A script cannot call the lead session. A fallback that cannot be called is
    # not a fallback, and substituting a different model for the lead role would
    # silently change who owns the deliverable.
    if role in ("lead", "mechanical") or primary["lane"] == "dsh":
        if fallback:
            notes.append("no script-side fallback: the lead role is the host session "
                         "itself, not a model a script can call")
        fallback = []
    else:
        callable_fallback = [entry for entry in fallback if entry["lane"] != "dsh"]
        if len(callable_fallback) != len(fallback):
            notes.append("dsh:auto dropped from the fallback: a script cannot call the "
                         "lead session")
        fallback = callable_fallback
    if primary.get("lane") != protected_lane and protected_lane and not pin:
        notes.append(f"protected lane {protected_lane!r} is unavailable for this role; "
                     f"the fallback is recorded in the call log")
    if needs.get("vision") and not (primary.get("capabilities") or {}).get("vision"):
        raise RouteError(f"{primary['id']} cannot see images but the task needs vision")
    return {
        "role": role,
        "needs": needs,
        "primary": primary,
        "fallback": fallback,
        "notes": notes,
        "excluded": [{"id": mid, "reason": why} for mid, why in excluded],
        "quota": {key: quota.get(key) for key in
                  ("source", "remaining", "hours_left", "weekly_remaining", "tier",
                   "stale", "stale_reason")} if quota else {},
    }


def _lane_enabled(cfg, lane):
    return ((cfg.get("lanes") or {}).get(lane) or {}).get("enabled") is not False


def _codex_default(cfg):
    models = ((cfg.get("lanes") or {}).get("codex") or {}).get("models") or ["auto"]
    return f"codex:{_text(models[0]) or 'auto'}"


def _dsh_default(cfg):
    models = ((cfg.get("lanes") or {}).get("dsh") or {}).get("models") or ["auto"]
    return f"dsh:{_text(models[0]) or 'auto'}"


def chain_models(name, cfg):
    chain = (cfg.get("chains") or {}).get(name)
    if not isinstance(chain, dict):
        raise RouteError(f"no chain named {name!r}; add one with "
                         f"`models.py chains set {name} --models a,b`")
    models = [_text(m) for m in (chain.get("models") or []) if _text(m)]
    if not models:
        raise RouteError(f"chain {name!r} has no links")
    return models, _text(chain.get("strategy")) or "fallback", int(chain.get("stickyLimit") or 1)


def plan_chain(name, cfg, state, project=".", read_quota=True):
    """Resolve a chain into an ordered list of ids plus the reasons links dropped.

    Strategies mirror the reference project: `fallback` is a fixed quality ladder;
    `round-robin` rotates the head and stays put for `stickyLimit` turns;
    `smart` demotes anything cooling or over budget; `pace` additionally uses the
    codex quota reading to put the lane with the most headroom first.
    """
    models, strategy, sticky = chain_models(name, cfg)
    entries = catalog(cfg, state, project)
    links, dropped = [], []
    for model_id in models:
        entry = find(entries, model_id)
        if entry is None:
            dropped.append({"id": model_id, "reason": "not in the catalog"})
            continue
        if entry["lane"] == "dsh":
            # A named chain is still executed by `models.py call`, which cannot be
            # the lead session. Accepting the link and skipping it at call time
            # would make the chain look longer than it is.
            dropped.append({"id": model_id,
                            "reason": "the lead session cannot be called from a script; "
                                      "run that step in the session"})
            continue
        if not entry.get("usable"):
            dropped.append({"id": model_id, "reason": entry.get("reason")})
            continue
        cooling = 0 if entry["lane"] in ("codex", "dsh") else \
            cfg_mod.cooling(state, entry["provider"],
                            None if entry["model"] is None else entry["model"])
        if cooling:
            dropped.append({"id": model_id,
                            "reason": f"cooling down for {cfg_mod.seconds_to_text(cooling)}"})
            continue
        links.append(entry)

    if strategy == "round-robin" and len(links) > 1:
        key = f"chain:{name}"
        selected = {}

        def advance(current, selected=selected, links=links):
            # Read, choose and increment inside one locked transaction: two
            # concurrent planners must never both read the same cursor.
            cursor = cfg_mod.rotation(current, key)
            index = min(cursor["index"], len(links) - 1)
            selected["index"] = index
            used = cursor["used"] + 1
            if used >= max(1, sticky):
                cfg_mod.set_rotation(current, key, (index + 1) % len(links), 0)
            else:
                cfg_mod.set_rotation(current, key, index, used)

        cfg_mod.mutate(project, advance)
        index = selected.get("index", 0)
        links = links[index:] + links[:index]
    elif strategy in ("smart", "pace"):
        quota = quota_snapshot(cfg, state, project, read=read_quota)
        remaining = quota.get("remaining")

        def key(entry, index):
            health = _health_rank(state, entry)
            headroom = _budget_rank(cfg, state, entry)
            pace = 0.0
            if entry["lane"] == "codex" and isinstance(remaining, (int, float)):
                hours = quota.get("hours_left")
                pace = remaining / hours if isinstance(hours, (int, float)) and hours > 0 \
                    else remaining / 100.0
            return (-health, -headroom, -pace if strategy == "pace" else 0, index)

        links = [entry for _, entry in sorted(enumerate(links), key=lambda pair: key(*pair))]

    return {
        "name": name,
        "strategy": strategy,
        "links": links,
        "dropped": dropped,
    }
