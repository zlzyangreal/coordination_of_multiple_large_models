#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Unified model access and management for the multi-model coordination skill.

    python scripts/models.py init                       # per-project config
    python scripts/models.py providers --search deep    # what could be plugged in
    python scripts/models.py add deepseek               # configure a provider
    python scripts/models.py key set deepseek           # secret -> 0600 store (stdin)
    python scripts/models.py discover deepseek          # live model listing
    python scripts/models.py list                       # catalog + availability
    python scripts/models.py use review codex:auto      # switch a role's model
    python scripts/models.py route --role review --producer deepseek/deepseek-v4-pro
    python scripts/models.py call --chain review --prompt-file brief.md --output out.md
    python scripts/models.py health | budget | doctor

The two protected lanes are not reimplemented here, on purpose:

    codex:<slug>  ->  delegates to scripts/run_gpt.py (the one GPT call path)
    dsh:<slug>    ->  refused: the lead session *is* the dsh model, and a second
                      caller spawned by a script would be unmanaged and unrecorded

Everything else is additive. With no ``_work/models/config.json`` the behaviour
of the skill is unchanged; the default roles still point at ``codex:auto`` and
``dsh:auto``.

Exit codes: 0 ok, 1 failure, 2 misuse or unavailable, 3 no usable candidate,
4 request-scoped defect (do not fall back), 5 every link of the chain failed.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import model_config as cfg_mod  # noqa: E402
import model_http  # noqa: E402
import model_registry as registry  # noqa: E402
import model_route  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SKILL_VERSION = "1.7.0"

#: Failures that must move to the next link instead of retrying the same one.
#: Retrying a 429 or an auth refusal spends a call to learn the same thing.
NO_INLINE_RETRY = {"rate_limit", "auth", "billing", "permission", "model_missing",
                   "endpoint_missing", "invalid_request"}


# --------------------------------------------------------------------------- helpers
def emit(text=""):
    print(text)


def warn(text):
    print(f"[WARN] {text}", file=sys.stderr)


def fail(text, code=1):
    print(f"[FAIL] {text}", file=sys.stderr)
    return code


def project_of(args):
    return os.path.abspath(getattr(args, "project", ".") or ".")


def load(args):
    return cfg_mod.load_config(project_of(args))


def _read_raw(path):
    data, err = cfg_mod.read_json(path)
    if err:
        raise cfg_mod.ConfigError(err)
    return data if isinstance(data, dict) else {}


def _save_raw(path, data):
    data.setdefault("version", 1)
    cfg_mod.atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _scope_path(args, kind="config"):
    if args.scope == "user":
        return cfg_mod.user_paths()[kind]
    return cfg_mod.paths(project_of(args))[kind]


def _setting(pid, cfg):
    return (cfg.get("providers") or {}).get(pid) or {}


def _record(pid, cfg):
    return model_route.provider_record(pid, cfg)


def _json(args):
    return bool(getattr(args, "json", False))


def _table(rows, headers):
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))
    line = "  ".join(h.ljust(widths[index]) for index, h in enumerate(headers))
    emit(line)
    emit("  ".join("-" * width for width in widths))
    for row in rows:
        emit("  ".join(str(cell).ljust(widths[index]) for index, cell in enumerate(row)))


def _parse_csv(values):
    out = []
    for value in values or []:
        for part in str(value).split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


def _needs_from_args(args):
    needs = {}
    for need in _parse_csv(args.needs):
        if need == "vision":
            needs["vision"] = True
        elif need.startswith("min-context="):
            needs["minContext"] = int(need.split("=", 1)[1])
        elif need.startswith("context="):
            needs["minContext"] = int(need.split("=", 1)[1])
        elif need == "reasoning":
            needs["reasoning"] = True
        else:
            raise cfg_mod.ConfigError(f"unknown --needs value {need!r} "
                                      f"(vision, reasoning, min-context=NNN)")
    return needs


def _lanes_report(cfg):
    out = {}
    for lane in ("codex", "dsh"):
        lane_cfg = (cfg.get("lanes") or {}).get(lane) or {}
        out[lane] = {"enabled": lane_cfg.get("enabled") is not False,
                     "models": lane_cfg.get("models") or ["auto"]}
    return out


def _codex_path():
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "OpenAI", "Codex", "bin")
    hits = []
    if os.path.isdir(base):
        for name in os.listdir(base):
            candidate = os.path.join(base, name, "codex.exe")
            if os.path.isfile(candidate):
                hits.append(candidate)
    return max(hits, key=os.path.getmtime) if hits else None


# --------------------------------------------------------------------------- init
def cmd_init(args):
    path, created = cfg_mod.write_default_config(project_of(args), force=args.force)
    emit(f"{'created' if created else 'kept'}  {path}")
    models_dir = cfg_mod.paths(project_of(args))["models_dir"]
    if args.with_local:
        cfg, _ = load(args)
        project = project_of(args)
        added = []
        for pid, probe in (("ollama", ("127.0.0.1", 11434)),
                           ("lmstudio", ("127.0.0.1", 1234))):
            try:
                with socket.create_connection(probe, timeout=1):
                    pass
            except OSError:
                continue
            raw = _read_raw(cfg_mod.paths(project)["config"])
            raw.setdefault("providers", {})[pid] = {"enabled": True}
            _save_raw(cfg_mod.paths(project)["config"], raw)
            added.append(pid)
        if added:
            emit(f"local providers detected and enabled: {', '.join(added)} "
                 f"(run `models.py discover <id>`)")
        else:
            emit("no local provider answered on 11434/1234; nothing enabled")
    emit(f"model state: {models_dir}")
    if created:
        emit("next: `models.py providers` -> `models.py add <provider>` -> "
             "`models.py key set <provider>` -> `models.py discover <provider>`")
    return 0


# --------------------------------------------------------------------------- providers
def cmd_providers(args):
    cfg, meta = load(args)
    configured = cfg.get("providers") or {}
    rows = []
    for record in registry.providers(category=args.category, format=args.format,
                                     auth_kind=args.auth, search=args.search):
        setting = configured.get(record["id"]) or {}
        if args.configured and not setting:
            continue
        status = []
        if setting.get("enabled") is False:
            status.append("disabled")
        elif setting:
            status.append("configured")
        if registry.undrivable(record):
            status.append("not drivable")
        rows.append([
            record["id"], record.get("category"), record.get("format"),
            (record.get("auth") or {}).get("kind"),
            len(record.get("models") or []),
            "/".join(status) or "-",
        ])
    if _json(args):
        emit(json.dumps([dict(zip(
            ["id", "category", "format", "authKind", "modelCount", "status"], row))
            for row in rows], ensure_ascii=False, indent=2))
        return 0
    emit(f"registry: {len(registry.providers())} providers "
         f"(vendored; see assets/providers.registry.json)")
    if rows:
        _table(rows, ["id", "category", "format", "auth", "models", "state"])
    else:
        emit("no provider matched")
    if meta["warnings"]:
        emit("")
        for warning in meta["warnings"]:
            warn(warning)
    return 0


def _write_provider(args, pid, setting, scope):
    path = _scope_path(args, "config")
    raw = _read_raw(path)
    providers = raw.setdefault("providers", {})
    previous = providers.get(pid) if isinstance(providers.get(pid), dict) else {}
    merged = dict(previous)
    merged.update({k: v for k, v in setting.items() if v is not None})
    merged["enabled"] = bool(setting.get("enabled", True)) and not args.no_enable
    providers[pid] = merged
    _save_raw(path, raw)
    return path


def cmd_add(args):
    cfg, _ = load(args)
    pid = args.provider.strip()
    record = registry.provider(pid)
    if record is None:
        return fail(f"unknown provider {pid!r}; `models.py providers --search {pid}` "
                    f"shows the registry, or use `models.py add-custom`", 2)
    setting = {"enabled": True}
    if args.models:
        setting["models"] = _parse_csv(args.models)
    if args.base_url:
        setting["baseUrl"] = args.base_url
    if args.format:
        if args.format not in registry.FORMATS:
            return fail(f"unknown format {args.format!r}", 2)
        setting["format"] = args.format
    if args.api_key_env:
        setting["apiKeyEnv"] = args.api_key_env
    path = _write_provider(args, pid, setting, args.scope)
    emit(f"configured {pid} -> {path}")
    _log_change(project_of(args), f"add {pid}")
    kind = (record.get("auth") or {}).get("kind")
    if kind in ("apiKey", "token"):
        env_name = args.api_key_env or (record.get("auth") or {}).get("env")
        emit(f"credential: `models.py key set {pid}` (stdin) or set {env_name}")
    else:
        emit("credential: none required")
    blocked = registry.undrivable(record)
    if blocked:
        warn(blocked)
    emit(f"next: `models.py discover {pid}` then `models.py list`")
    return 0


def cmd_add_custom(args):
    pid = args.id.strip()
    if not pid or "/" in pid or ":" in pid:
        return fail("custom provider id must be non-empty and contain neither '/' nor ':'", 2)
    if registry.provider(pid) is not None:
        return fail(f"{pid!r} is already a registry provider; use `models.py add {pid}`", 2)
    if not args.base_url:
        return fail("--base-url is required for a custom provider", 2)
    fmt = args.format or "openai-chat"
    if fmt not in registry.FORMATS:
        return fail(f"unknown format {fmt!r}", 2)
    setting = {
        "enabled": True,
        "name": args.name or pid,
        "baseUrl": args.base_url.rstrip("/"),
        "format": fmt,
        "auth": {"kind": args.auth},
        "models": _parse_csv(args.models),
        "local": args.local,
    }
    if args.models_url:
        setting["modelsUrl"] = args.models_url
    if args.chat_url:
        setting["chatUrl"] = args.chat_url
    if args.chat_path:
        setting["chatPath"] = args.chat_path
    if args.api_key_env:
        setting["apiKeyEnv"] = args.api_key_env
    path = _write_provider(args, pid, setting, args.scope)
    emit(f"configured custom provider {pid} ({fmt}) -> {path}")
    _log_change(project_of(args), f"add-custom {pid} ({fmt})")
    if args.auth in ("apiKey", "token"):
        emit(f"credential: `models.py key set {pid}` (stdin)")
    emit(f"next: `models.py discover {pid}` (or set `models` explicitly) then "
         f"`models.py test {pid}`")
    return 0


def cmd_enable(args):
    return _toggle(args, enabled=args.enable)


def _toggle(args, enabled):
    cfg, _ = load(args)
    pid = args.provider.strip()
    if pid not in (cfg.get("providers") or {}):
        return fail(f"{pid!r} is not configured in this project", 2)
    raw = _read_raw(_scope_path(args, "config"))
    raw.setdefault("providers", {}).setdefault(pid, {})["enabled"] = enabled
    _save_raw(_scope_path(args, "config"), raw)
    emit(f"{pid}: {'enabled' if enabled else 'disabled'}")
    return 0


def cmd_remove(args):
    cfg, _ = load(args)
    pid = args.provider.strip()
    if pid not in (cfg.get("providers") or {}):
        return fail(f"{pid!r} is not configured in this project", 2)
    raw = _read_raw(_scope_path(args, "config"))
    (raw.get("providers") or {}).pop(pid, None)
    _save_raw(_scope_path(args, "config"), raw)
    emit(f"removed provider config: {pid}")
    _log_change(project_of(args), f"remove {pid}")
    if args.purge_key:
        path = _scope_path(args, "credentials")
        data = _read_raw(path)
        if (data.get("providers") or {}).pop(pid, None) is not None:
            _save_raw(path, data)
            emit(f"removed stored credential for {pid}")
    else:
        emit(f"stored credential kept; `models.py key clear {pid}` removes it")
    return 0


# --------------------------------------------------------------------------- keys
def _clear_auth_cooldown(project, pid):
    """A rotated credential invalidates the old auth/billing cooldown.

    Without this, fixing a key leaves the provider unusable for hours and the
    only advice is "wait" — the recovery path the checklist asks for.
    """
    def clear(state):
        row = (state.get("providers") or {}).get(pid) or {}
        if row.get("kind") in ("auth", "billing", "permission"):
            row.update({"status": "unknown", "until": None, "kind": None,
                        "reason": "credential rotated or account re-authenticated"})
            state.setdefault("providers", {})[pid] = row

    cfg_mod.mutate(project, clear)


def cmd_key(args):
    cfg, _ = load(args)
    pid = args.provider.strip()
    record = _record(pid, cfg)
    if record is None:
        return fail(f"unknown provider {pid!r}", 2)
    if args.key_command == "set":
        if args.from_env:
            secret = os.environ.get(args.from_env, "").strip()
            if not secret:
                return fail(f"environment variable {args.from_env} is unset or empty", 2)
        else:
            if sys.stdin.isatty():
                emit(f"paste the credential for {pid}, then Enter (input is not echoed "
                     f"by the shell? use --from-env to be safe)")
            secret = sys.stdin.readline().strip()
        if not secret:
            return fail("no credential received on stdin", 2)
        path = cfg_mod.set_key(pid, secret, project_of(args), scope=args.scope)
        emit(f"stored {pid} credential in {path}")
        emit(f"fingerprint: {cfg_mod.fingerprint(secret)} (the value is never printed)")
        acl = cfg_mod.acl_status(path)
        if not acl["ok"]:
            warn(f"could not confirm that the credential file is private "
                 f"({acl['detail']}); `models.py doctor` reports the effective ACL")
        _clear_auth_cooldown(project_of(args), pid)
        return 0
    if args.key_command == "clear":
        path = cfg_mod.set_key(pid, None, project_of(args), scope=args.scope)
        emit(f"cleared {pid} credential in {path}")
        return 0
    # status
    setting = _setting(pid, cfg)
    status = cfg_mod.key_status(pid, record, setting, project_of(args))
    if _json(args):
        emit(json.dumps(status, ensure_ascii=False, indent=2))
        return 0
    if status["error"]:
        emit(f"{pid}: credential error: {cfg_mod.redact(status['error'], [])}")
        return 1
    if status["hasKey"] and status.get("minted"):
        label = "key present " + str(status["fingerprint"])
    elif status["hasKey"]:
        label = "credential declared (minted by its command on use)"
    else:
        label = "no key"
    emit(f"{pid}: {label} (source {status['source'] or 'none'})")
    return 0


# --------------------------------------------------------------------------- accounts
def _account_setting(pid, cfg):
    return (_setting(pid, cfg).get("account") or {})


def _run_account_command(argv, timeout, capture):
    """Run one vendor login/logout/status command without a shell.

    The login itself belongs to the vendor client (browser OAuth, device code,
    whatever it needs); this skill only launches it and records the outcome. It
    never sees or stores the password, and a token it later needs is minted by
    the configured `credential` command.
    """
    if not (isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv)):
        return None, "no argv configured (expected a list of strings)"
    try:
        done = subprocess.run(argv, shell=False, capture_output=capture, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"could not run {argv[0]}: {exc}"
    if capture:
        return done, None
    return done, None


def cmd_account(args):
    cfg, _ = load(args)
    project = project_of(args)
    if args.account_command == "list":
        rows = []
        for pid, setting in (cfg.get("providers") or {}).items():
            account = (setting or {}).get("account") or {}
            if not isinstance(account, dict) or not account:
                continue
            state = cfg_mod.load_state(project)
            row = (state.get("providers", {}).get(pid) or {}).get("account") or {}
            rows.append([pid, "yes" if row.get("loggedIn") else "no",
                         row.get("at") or "-", "configured"])
        if _json(args):
            emit(json.dumps(rows, ensure_ascii=False, indent=2))
        elif rows:
            _table(rows, ["provider", "logged in", "at", "account"])
        else:
            emit("no providers declare an `account` section; add loginArgv/"
                 "logoutArgv/statusArgv to one in _work/models/config.json")
        return 0

    pid = (args.provider or "").strip()
    record = _record(pid, cfg)
    if record is None:
        return fail(f"unknown provider {pid!r}", 2)
    account = _account_setting(pid, cfg)
    action = args.account_command
    argv = account.get(f"{action}Argv")
    if action == "login" and not argv:
        return fail(f"{pid} has no account.loginArgv; account login is delegated to "
                    f"the vendor CLI (for example [\"qoder\", \"login\"])", 2)
    if action == "logout" and not argv:
        return fail(f"{pid} has no account.logoutArgv", 2)

    timeout = int(account.get("timeoutSeconds") or 300)
    capture = action == "status"
    done, error = _run_account_command(argv, timeout, capture)
    if error:
        return fail(f"{pid}: {error}", 2)
    output = "" if not capture else ((done.stdout or "") + (done.stderr or "")).strip()
    key, _, _ = cfg_mod.api_key(record, _setting(pid, cfg), project, execute=True)
    safe = cfg_mod.redact(output, [key] if key else [])
    ok = done.returncode == 0

    def remember(state):
        row = state.setdefault("providers", {}).setdefault(pid, {})
        entry = row.get("account") or {}
        entry.update({"at": cfg_mod.iso(), "status": safe[:200] or None})
        if action == "login":
            entry["loggedIn"] = ok
        elif action == "logout":
            entry["loggedIn"] = False
        row["account"] = entry

    cfg_mod.mutate(project, remember)
    if action == "login" and ok:
        _clear_auth_cooldown(project, pid)
    if action == "logout" and ok:
        for scope in ("project", "user"):
            try:
                cfg_mod.set_key(pid, None, project, scope=scope)
            except OSError:
                pass
        emit(f"{pid}: logged out; the stored token was cleared")
        _log_change(project, f"account logout {pid}")
        return 0
    if capture:
        emit(f"{pid}: {'logged in' if ok else 'not logged in'}")
        if safe:
            emit(safe[:500])
    _log_change(project, f"account {action} {pid}")
    return 0 if ok else 1


# --------------------------------------------------------------------------- catalog
def _catalog_rows(entries, only_usable=False):
    rows = []
    for entry in entries:
        if only_usable and not entry.get("usable"):
            continue
        caps = entry.get("capabilities") or {}
        flags = ("V" if caps.get("vision") else "-") + ("R" if caps.get("reasoning") else "-")
        rows.append([
            entry["id"], entry["lane"], entry.get("format"), flags,
            caps.get("contextWindow"),
            "ok" if entry.get("usable") else "no",
            entry.get("source"),
            "" if entry.get("usable") else str(entry.get("reason") or "")[:60],
        ])
    return rows


def cmd_list(args):
    cfg, _ = load(args)
    state = cfg_mod.load_state(project_of(args))
    entries = model_route.catalog(cfg, state, project_of(args))
    if args.provider:
        entries = [e for e in entries if e["provider"] == args.provider]
    if _json(args):
        emit(json.dumps(entries, ensure_ascii=False, indent=2))
        return 0
    rows = _catalog_rows(entries, only_usable=args.usable)
    if rows:
        _table(rows, ["model", "lane", "format", "cap", "context", "usable", "source", "why"])
    else:
        emit("no models configured; `models.py init` then `models.py add <provider>`")
    return 0


def cmd_show(args):
    cfg, _ = load(args)
    state = cfg_mod.load_state(project_of(args))
    entries = model_route.catalog(cfg, state, project_of(args))
    entry = model_route.find(entries, args.model)
    if entry is None:
        return fail(f"{args.model!r} is not in this project's catalog; `models.py list`", 2)
    status = None
    if entry["lane"] == "http":
        record = _record(entry["provider"], cfg)
        status = cfg_mod.key_status(entry["provider"], record,
                                    _setting(entry["provider"], cfg),
                                    project_of(args))
    payload = dict(entry)
    if status:
        payload["credential"] = status
    if _json(args):
        emit(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    emit(f"model      {entry['id']}")
    emit(f"lane       {entry['lane']}  ({entry.get('format')})")
    emit(f"family     {entry['family']}")
    emit(f"capability vision={entry['capabilities'].get('vision')} "
         f"reasoning={entry['capabilities'].get('reasoning')} "
         f"context={entry['capabilities'].get('contextWindow')}")
    emit(f"usable     {'yes' if entry.get('usable') else 'no: ' + str(entry.get('reason'))}")
    if status:
        if status.get("minted"):
            label = "present " + str(status.get("fingerprint"))
        elif status.get("declared"):
            label = "declared (minted by its command on use)"
        else:
            label = "missing"
        emit(f"credential {label} (source {status.get('source') or 'none'})")
    return 0


# --------------------------------------------------------------------------- discovery / probes
def _resolve_key(pid, cfg, args):
    record = _record(pid, cfg)
    if record is None:
        return None, None, f"unknown provider {pid!r}"
    setting = _setting(pid, cfg)
    key, source, error = cfg_mod.api_key(record, setting, project_of(args))
    kind = (record.get("auth") or {}).get("kind")
    if error:
        return record, None, error
    if kind in ("apiKey", "token") and not key:
        env = (record.get("auth") or {}).get("env")
        return record, None, (f"no credential for {pid}: `models.py key set {pid}` "
                              f"or set {env}" if env else
                              f"no credential for {pid}: `models.py key set {pid}`")
    return record, key, None


def _listing_record(record, setting):
    """Where a live listing is read from.

    A vendored provider without a declared listing gets none 鈥?a guessed URL is
    worse than no URL. A *custom* OpenAI-compatible endpoint is different: the
    user created the row, `GET <base>/models` is the de-facto contract, and
    `discover` is an explicit action, so deriving it is not a silent guess.
    """
    if registry.models_url(record, setting.get("baseUrl")):
        return record
    if record.get("category") == "custom" and record.get("format") == "openai-chat":
        base = registry.base_url(record, setting.get("baseUrl"))
        if base:
            return dict(record, modelsUrl=base.rstrip("/") + "/models")
    return record


def cmd_discover(args):
    cfg, _ = load(args)
    project = project_of(args)
    pid = args.provider.strip()
    record, key, error = _resolve_key(pid, cfg, args)
    if error:
        return fail(error, 2)
    setting = _setting(pid, cfg)
    result = model_http.list_models(_listing_record(record, setting), key,
                                    setting.get("baseUrl"), timeout=args.timeout)
    message = cfg_mod.redact(result["message"], [key] if key else [])
    secrets = [key] if key else []
    # Listing ids are provider-controlled data: a broken or hostile endpoint can
    # echo the Authorization value as a "model id". Treat them as tainted too.
    ids = [cfg_mod.redact(str(model_id), secrets) for model_id in (result["ids"] or [])]
    if result["ok"]:
        def remember(state):
            cfg_mod.mark_probe(state, pid, True, message, len(ids))
            cfg_mod.set_discovered(state, pid, ids)

        cfg_mod.mutate(project, remember)
        emit(f"{pid}: {message}")
        for model_id in ids:
            emit(f"  {model_id}")
        return 0

    def remember(state):
        cfg_mod.mark_probe(state, pid, False, message)

    cfg_mod.mutate(project, remember)
    return fail(f"{pid}: {message}", 1)


def cmd_test(args):
    cfg, _ = load(args)
    project = project_of(args)
    state = cfg_mod.load_state(project)
    target = args.target.strip()
    entries = model_route.catalog(cfg, state, project)
    entry = model_route.find(entries, target)
    if entry is not None:
        if entry["lane"] == "dsh":
            emit("dsh lane: the lead session is the process running this command; "
                 "no separate probe exists and none is needed")
            return 0
        if entry["lane"] == "codex":
            return _codex_probe(args, project)
        if entry.get("protectedFamily") and not entry.get("allowProtectedFamily"):
            # A probe is still a call: `test --generate` must not become the
            # protected-family bypass that `call` refuses.
            return fail(f"{target!r} is a protected-family row over HTTP; set "
                        f"allowProtectedFamily on its provider if that is deliberate "
                        f"({entry.get('reason')})", 2)
        if not entry.get("usable") and "protected family" in str(entry.get("reason")):
            return fail(f"{target!r} is not usable: {entry.get('reason')}", 2)
        pid = entry["provider"]
        model_id = entry["model"]
    elif target in (cfg.get("providers") or {}) or registry.provider(target):
        pid, model_id = target, None
    else:
        return fail(f"{target!r} is neither a catalog model nor a configured provider", 2)

    record, key, error = _resolve_key(pid, cfg, args)
    if error:
        return fail(error, 2)
    setting = _setting(pid, cfg)
    result = model_http.list_models(_listing_record(record, setting), key,
                                    setting.get("baseUrl"), timeout=args.timeout)
    message = cfg_mod.redact(result["message"], [key] if key else [])

    def probe(state):
        cfg_mod.mark_probe(state, pid, result["ok"], message,
                           len(result["ids"]) if result["ids"] is not None else None)

    cfg_mod.mutate(project, probe)
    state = cfg_mod.load_state(project)
    emit(f"{pid}: {'ok' if result['ok'] else 'failed'}: {message}")
    if not result["ok"] and not args.generate:
        emit("hint: `--generate` spends one small completion, which is the only way "
             "to verify a provider that publishes no model listing")
    if not result["ok"] and result["ids"] is None and args.generate:
        if not model_id:
            candidates = ([m for m in (setting.get("models") or []) if str(m).strip()]
                          or cfg_mod.discovered_models(state, pid)
                          or [row.get("id") for row in (record.get("models") or [])])
            model_id = next((m for m in candidates if m), None)
        if not model_id:
            return fail("no model id to generate with; pass a model id as the target", 2)
        # A provider-level target must not become a bypass: resolve the exact model
        # row and apply the same eligibility rules as a direct call.
        target_entry = model_route.find(entries, f"{pid}/{model_id}")
        if target_entry is None:
            return fail(f"{pid}/{model_id} is not in the catalog; run `models.py list`", 2)
        if target_entry.get("protectedFamily") and not target_entry.get(
                "allowProtectedFamily"):
            return fail(f"{pid}/{model_id} is a protected-family row over HTTP; set "
                        f"allowProtectedFamily deliberately to probe it", 2)
        if not target_entry.get("usable"):
            reason = str(target_entry.get("reason") or "")
            code = 3 if ("limit reached" in reason or "cooling" in reason) else 2
            return fail(f"{pid}/{model_id} is not usable: {reason}", code)
        try:
            answer = model_http.chat(record, model_id, "Reply with the single word OK.",
                                     api_key=key, base_override=setting.get("baseUrl"),
                                     max_output=16, timeout=args.timeout)
        except model_http.CallFailure as exc:
            return fail(f"{pid}: generation failed ({exc.kind}): "
                        f"{cfg_mod.redact(exc.message, [key] if key else [])}", 1)
        shown = cfg_mod.redact(answer["text"][:80], [key] if key else [])
        emit(f"{pid}: generation ok ({model_id}): {shown!r}")

        def generated(state):
            cfg_mod.mark_probe(state, pid, True,
                               f"generation answered with {model_id}", 1)

        cfg_mod.mutate(project, generated)
        return 0
    return 0 if result["ok"] else 1


def _codex_probe(args, project):
    """Connectivity probe for the codex lane — through the runner, never direct.

    A direct `codex exec` here used to be a second GPT call path. `run_gpt.py
    --probe` already owns that shape (ephemeral, no history, recorded), so the
    skill keeps exactly one way to reach GPT.
    """
    run_gpt = os.environ.get("COORD_RUN_GPT") or os.path.join(HERE, "run_gpt.py")
    if not os.path.isfile(run_gpt):
        return fail(f"runner missing: {run_gpt}", 2)
    cmd = [sys.executable, run_gpt, "--cd", project, "--probe", "--no-ledger",
           "--timeout", str(args.timeout), "ok"]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=args.timeout + 60)
    except subprocess.TimeoutExpired:
        return fail(f"codex probe timed out after {args.timeout}s", 1)
    record = None
    for line in reversed((done.stdout or "").strip().splitlines()):
        if line.strip().startswith("{"):
            try:
                record = json.loads(line)
                break
            except ValueError:
                continue
    answer = str((record or {}).get("final_message") or "").strip().lower()
    good = done.returncode == 0 and answer.rstrip(".!") == "ok"
    emit(f"codex lane: {'ok' if good else 'failed'} (exit={done.returncode})")
    if not good:
        tail = (done.stderr or "").strip().splitlines()
        emit(f"  {tail[-1][:200] if tail else 'no runner output'}")
    return 0 if good else 1


# --------------------------------------------------------------------------- roles and routing
def cmd_use(args):
    cfg, _ = load(args)
    role = args.role.strip()
    if role not in model_route.ROLE_NAMES:
        return fail(f"unknown role {role!r} (known: {', '.join(model_route.ROLE_NAMES)})", 2)
    model = args.model.strip()
    if model != "auto":
        state = cfg_mod.load_state(project_of(args))
        entries = model_route.catalog(cfg, state, project_of(args))
        if model_route.find(entries, model) is None:
            return fail(f"{model!r} is not in this project's catalog; `models.py list`", 2)
    raw = _read_raw(_scope_path(args, "config"))
    raw.setdefault("roles", {}).setdefault(role, {})["model"] = model
    _save_raw(_scope_path(args, "config"), raw)
    emit(f"role {role}: {model}")
    _log_change(project_of(args), f"use {role} {model}")
    return 0


def cmd_route(args):
    cfg, _ = load(args)
    project = project_of(args)
    state = cfg_mod.load_state(project)
    exclude = set(_parse_csv(args.exclude_family))
    if args.producer:
        producer = model_route.find(model_route.catalog(cfg, state, project), args.producer)
        if producer is None:
            return fail(f"--producer {args.producer!r} is not in the catalog", 2)
        exclude.add(producer["family"])
    try:
        needs = _needs_from_args(args)
        plan = model_route.route(cfg, state, args.role, needs=needs,
                                 exclude_families=tuple(exclude), pin=args.pin,
                                 project=project, read_quota=not args.no_quota,
                                 allow_unknown_family=args.allow_unknown_family)
    except model_route.RouteError as exc:
        return fail(str(exc), 3)
    if _json(args):
        emit(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    quota = plan.get("quota") or {}
    if quota.get("tier"):
        emit(f"quota: tier {quota['tier']} 5h {quota.get('remaining')}% "
             f"weekly {quota.get('weekly_remaining')}% "
             f"({quota.get('source') or 'no reading'})")
    emit(f"role {plan['role']}  primary  {plan['primary']['id']}  "
         f"[{plan['primary']['lane']}]")
    for entry in plan["fallback"]:
        emit(f"                  fallback {entry['id']}  [{entry['lane']}]")
    for note in plan["notes"]:
        emit(f"note: {note}")
    for row in plan["excluded"]:
        emit(f"skipped: {row['id']} - {row['reason']}")
    return 0


# --------------------------------------------------------------------------- chains
def cmd_chains(args):
    cfg, _ = load(args)
    project = project_of(args)
    state = cfg_mod.load_state(project)
    if args.chain_command == "list":
        rows = []
        for name, chain in (cfg.get("chains") or {}).items():
            try:
                plan = model_route.plan_chain(name, cfg, state, project, read_quota=False)
                usable = len(plan["links"])
            except model_route.RouteError:
                usable = 0
            rows.append([name, chain.get("strategy") or "fallback", len(chain.get("models") or []),
                         usable])
        if _json(args):
            emit(json.dumps(rows, ensure_ascii=False))
        elif rows:
            _table(rows, ["chain", "strategy", "links", "usable"])
        else:
            emit("no chains; `models.py chains set review --models codex:auto,ollama/qwen3:8b`")
        return 0
    if args.chain_command == "set":
        name = args.name.strip()
        models = _parse_csv(args.models)
        if not name or not models:
            return fail("chains set needs a name and --models a,b,c", 2)
        strategy = args.strategy or "fallback"
        if strategy not in ("fallback", "round-robin", "smart", "pace"):
            return fail(f"unknown strategy {strategy!r}", 2)
        raw = _read_raw(_scope_path(args, "config"))
        raw.setdefault("chains", {})[name] = {
            "models": models,
            "strategy": strategy,
            "stickyLimit": int(args.sticky or 1),
        }
        _save_raw(_scope_path(args, "config"), raw)
        emit(f"chain {name}: {', '.join(models)} ({strategy})")
        return 0
    if args.chain_command == "remove":
        raw = _read_raw(_scope_path(args, "config"))
        if (raw.get("chains") or {}).pop(args.name, None) is None:
            return fail(f"no chain named {args.name!r}", 2)
        _save_raw(_scope_path(args, "config"), raw)
        emit(f"removed chain {args.name}")
        return 0
    # show
    try:
        plan = model_route.plan_chain(args.name, cfg, state, project,
                                      read_quota=not args.no_quota)
    except model_route.RouteError as exc:
        return fail(str(exc), 3)
    if _json(args):
        emit(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    emit(f"chain {args.name} ({plan['strategy']})")
    for entry in plan["links"]:
        emit(f"  ok       {entry['id']}  [{entry['lane']}]")
    for row in plan["dropped"]:
        emit(f"  dropped  {row['id']} - {row['reason']}")
    return 0


# --------------------------------------------------------------------------- calls
def _request_id():
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"M{stamp}-{os.getpid() % 1000:03d}"


def _append_call(project, record):
    path = cfg_mod.paths(project)["calls"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with cfg_mod.project_lock(project):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _log_change(project, text):
    """Append the model-change audit row `references/model-registry.md` promises."""
    path = os.path.join(cfg_mod.paths(project)["models_dir"], "model_changes.log")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with cfg_mod.project_lock(project):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"| {cfg_mod.iso()} | {text} |\n")


def _read_prompt(args):
    if args.prompt_file:
        with open(args.prompt_file, encoding="utf-8-sig") as fh:
            return fh.read()
    return args.prompt or ""


def _codex_call(args, entry, prompt, project, request_id, request_path):
    run_gpt = os.environ.get("COORD_RUN_GPT") or os.path.join(HERE, "run_gpt.py")
    if not os.path.isfile(run_gpt):
        return fail(f"runner missing: {run_gpt}", 2), None
    models_dir = cfg_mod.paths(project)["models_dir"]
    out_dir = os.path.join(models_dir, "out")
    os.makedirs(out_dir, exist_ok=True)
    payload = os.path.join(out_dir, f"{request_id}.txt")
    cmd = [sys.executable, run_gpt, "--prompt-file", request_path, "--cd", project,
           "--sandbox", args.sandbox or "read-only",
           "--output", payload, "--request-id", request_id,
           "--calls-dir", os.path.join(models_dir, "calls"),
           "--ledger", os.path.join(project, "_work", "quota_ledger.md")]
    if entry.get("model"):
        cmd += ["--model", entry["model"]]
    if entry.get("effort"):
        cmd += ["--effort", entry["effort"]]
    if args.timeout:
        cmd += ["--timeout", str(args.timeout)]
    if args.dry_run:
        emit("[dry-run] " + " ".join(cmd))
        return 0, None
    # cwd=project makes every relative default inside run_gpt.py land in the
    # selected project, not in whatever directory the caller happened to be in.
    done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=project)
    if done.stderr:
        emit(done.stderr.rstrip())
    record = None
    for line in reversed((done.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                record = json.loads(line)
                break
            except ValueError:
                continue
    text = ""
    if os.path.isfile(payload):
        with open(payload, encoding="utf-8", errors="replace") as fh:
            text = fh.read().strip()
    if not text and record:
        text = str(record.get("final_message") or "").strip()
    status = 0 if done.returncode == 0 else (done.returncode or 1)
    empty = status == 0 and text == ""
    if empty:
        # Exit 0 with no captured text is not a successful answer; treating it as
        # one would hand an empty artifact to the next gate.
        status = 5
    result = {
        "requestId": request_id,
        "status": "ok" if status == 0 else "failed",
        "model": entry["id"],
        "lane": "codex",
        "text": text,
        "usage": (record or {}).get("usage"),
        "latencySeconds": (record or {}).get("duration_s"),
        "emptyResponse": empty,
        "codexRecord": record,
        "attempts": [],
    }
    return status, result


def _http_call(args, entry, prompt, cfg, project, request_id, attempts):
    pid = entry["provider"]
    setting = _setting(pid, cfg)
    record = _record(pid, cfg)
    if record is None:
        return 2, None, "unknown provider"
    state = cfg_mod.load_state(project)
    budget = cfg_mod.budget_block(setting, state, pid)
    if budget:
        attempts.append({"model": entry["id"], "outcome": "skipped", "reason": budget})
        return 3, None, budget
    cooling = cfg_mod.cooling(state, pid, entry["model"])
    if cooling:
        reason = f"cooling down for {cfg_mod.seconds_to_text(cooling)}"
        attempts.append({"model": entry["id"], "outcome": "skipped", "reason": reason})
        return 3, None, reason
    key, source, error = cfg_mod.api_key(record, setting, project)
    if error:
        attempts.append({"model": entry["id"], "outcome": "skipped", "reason": error})
        return 2, None, error
    defaults = cfg.get("defaults") or {}
    max_output = int(args.max_tokens or defaults.get("maxOutput") or 4096)
    timeout = int(args.timeout or defaults.get("timeoutSeconds") or 180)
    retries = int(defaults.get("retryTransient") or 0)
    secrets = [key] if key else []
    last_error = None
    for attempt in range(1, max(0, retries) + 2):
        cfg_mod.mutate(project, lambda s: cfg_mod.bump_usage(s, pid, calls=1))
        started = time.time()
        try:
            answer = model_http.chat(record, entry["model"], prompt, api_key=key,
                                     base_override=setting.get("baseUrl"),
                                     system=args.system, max_output=max_output,
                                     timeout=timeout)
        except model_http.CallFailure as exc:
            message = cfg_mod.redact(exc.message, secrets)

            def mark(current, exc=exc, message=message):
                if exc.kind in ("invalid_request", "endpoint_missing"):
                    return
                if exc.kind in ("auth", "billing", "permission"):
                    cfg_mod.mark_failure(current, pid, exc.kind, exc.cooldown, message)
                else:
                    # A 429 or a 5xx blocks only this model, so the next link of
                    # the chain stays reachable.
                    cfg_mod.mark_failure(current, pid, exc.kind, exc.cooldown, message,
                                         entry["model"], scope="model")

            cfg_mod.mutate(project, mark)
            attempts.append({
                "model": entry["id"], "lane": "http", "provider": pid,
                "attempt": attempt, "outcome": "failed", "kind": exc.kind,
                "scope": exc.scope, "status": exc.status,
                "latencySeconds": round(time.time() - started, 2),
                "message": message, "cooldownSeconds": exc.cooldown,
            })
            _append_call(project, {
                "ts": cfg_mod.iso(), "requestId": request_id, "model": entry["id"],
                "lane": "http", "provider": pid, "status": "failed",
                "kind": exc.kind, "http": exc.status, "message": message,
                "latencySeconds": round(time.time() - started, 2),
            })
            last_error = exc
            if not exc.should_fallback:
                return 4, None, f"{exc.kind}: {message}"
            if exc.kind in NO_INLINE_RETRY or not exc.retryable or attempt > retries:
                break
            time.sleep(1.2)
            continue
        usage = answer["usage"]
        # Provider-controlled output is tainted too: a gateway can echo the key in
        # the answer text, and that text goes to stdout and to --output.
        answer["text"] = cfg_mod.redact(answer["text"], secrets)

        def ok(current, usage=usage):
            cfg_mod.mark_ok(current, pid, entry["model"])
            cfg_mod.bump_usage(current, pid, calls=0, tokens=usage.get("total") or 0)

        cfg_mod.mutate(project, ok)
        attempts.append({
            "model": entry["id"], "lane": "http", "provider": pid,
            "attempt": attempt, "outcome": "ok", "status": answer.get("status"),
            "latencySeconds": answer.get("latencySeconds"),
            "usage": usage, "credentialSource": source,
        })
        _append_call(project, {
            "ts": cfg_mod.iso(), "requestId": request_id, "model": entry["id"],
            "lane": "http", "provider": pid, "status": "ok",
            "latencySeconds": answer.get("latencySeconds"), "usage": usage,
        })
        return 0, answer, None
    return 5, None, (f"{last_error.kind}: {last_error.message}" if last_error
                     else "every attempt failed")


def cmd_call(args):
    cfg, _ = load(args)
    project = project_of(args)
    state = cfg_mod.load_state(project)
    prompt = _read_prompt(args)
    if not prompt.strip():
        return fail("empty prompt; pass --prompt-file or --prompt", 2)
    request_id = args.request_id or _request_id()
    request_dir = os.path.join(cfg_mod.paths(project)["models_dir"], "requests")
    os.makedirs(request_dir, exist_ok=True)
    request_path = args.prompt_file or os.path.join(request_dir, f"{request_id}.md")
    if not args.prompt_file:
        with open(request_path, "w", encoding="utf-8") as fh:
            fh.write(prompt)

    entries = model_route.catalog(cfg, state, project)
    dropped = []
    if args.chain:
        try:
            plan = model_route.plan_chain(args.chain, cfg, state, project,
                                          read_quota=not args.no_quota)
        except model_route.RouteError as exc:
            return fail(str(exc), 3)
        dropped = plan["dropped"]
        links = plan["links"]
        if not links:
            detail = "; ".join(f"{row['id']}: {row['reason']}" for row in dropped)
            return fail(f"chain {args.chain!r} has no usable link ({detail})", 3)
    elif args.role:
        try:
            plan = model_route.route(
                cfg, state, args.role, needs=_needs_from_args(args),
                exclude_families=tuple(_parse_csv(args.exclude_family)),
                project=project, read_quota=not args.no_quota,
                allow_unknown_family=args.allow_unknown_family)
        except model_route.RouteError as exc:
            return fail(str(exc), 3)
        links = [plan["primary"]] + plan["fallback"]
    elif args.model:
        entry = model_route.find(entries, args.model)
        if entry is None:
            return fail(f"{args.model!r} is not in the catalog; `models.py list`", 2)
        if not entry.get("usable"):
            # A direct id is not a bypass: the same eligibility rules as routing.
            # A spent budget or a cooldown is "nothing usable" (3); a protected
            # row or a missing credential is a refusal (2).
            reason = str(entry.get("reason") or "")
            code = 3 if ("limit reached" in reason or "cooling" in reason) else 2
            return fail(f"{args.model!r} is not usable: {reason}", code)
        links = [entry]
    else:
        return fail("choose --model, --chain or --role", 2)

    if args.dry_run:
        for entry in links:
            if entry["lane"] == "http":
                record = _record(entry["provider"], cfg)
                setting = _setting(entry["provider"], cfg)
                emit(f"[dry-run] POST {registry.chat_url(record, setting.get('baseUrl'))} "
                     f"model={entry['model']} format={record.get('format')}")
            elif entry["lane"] == "codex":
                _codex_call(args, entry, prompt, project, request_id, request_path)
            else:
                emit(f"[dry-run] {entry['id']}: the lead session owns this lane")
        return 0

    attempts = []
    final = None
    status = 5
    for entry in links:
        if entry["lane"] == "dsh":
            message = ("the dsh lane is the lead session and cannot be called from a "
                       "script: this process is not the host. Use the session itself; "
                       "routing still treats it as the lead lane.")
            attempts.append({"model": entry["id"], "outcome": "skipped", "reason": message})
            if len(links) == 1:
                return fail(message, 2)
            continue
        if entry["lane"] == "codex":
            status, result = _codex_call(args, entry, prompt, project, request_id,
                                         request_path)
            attempts.append({"model": entry["id"], "lane": "codex",
                             "outcome": "ok" if status == 0 else "failed",
                             "exit": status})
            _append_call(project, {"ts": cfg_mod.iso(), "requestId": request_id,
                                   "model": entry["id"], "lane": "codex",
                                   "status": "ok" if status == 0 else "failed"})
            if status == 0:
                final = result
                break
            if status == 3:
                # Schema-invalid is a review-contract failure, not an availability
                # problem: falling back would hide a broken result behind another model.
                break
            continue
        status, answer, message = _http_call(args, entry, prompt, cfg, project,
                                             request_id, attempts)
        if status == 0:
            final = {
                "requestId": request_id,
                "status": "ok",
                "model": entry["id"],
                "lane": "http",
                "text": answer["text"],
                "usage": answer["usage"],
                "latencySeconds": answer["latencySeconds"],
                "attempts": attempts,
                "dropped": dropped,
            }
            break
        if status == 4:
            final = {"requestId": request_id, "status": "request-defect",
                     "model": entry["id"], "attempts": attempts, "message": message}
            break

    if final is None:
        final = {"requestId": request_id, "status": "exhausted",
                 "attempts": attempts, "dropped": dropped}
        if status not in (3, 4, 5):
            status = 5

    if final.get("status") == "ok":
        final.setdefault("dropped", dropped)
        if args.output:
            os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(final["text"].rstrip() + "\n")
            final["output"] = os.path.abspath(args.output)
        if _json(args):
            emit(json.dumps(final, ensure_ascii=False, indent=2))
        else:
            emit(final["text"])
        return 0

    if _json(args):
        emit(json.dumps(final, ensure_ascii=False, indent=2))
    else:
        emit(f"call failed: {final.get('message') or 'no link produced an answer'}")
        for attempt in attempts:
            detail = attempt.get("message") or attempt.get("reason") or attempt.get("outcome")
            emit(f"  {attempt.get('model')}: {attempt.get('outcome')} - {detail}")
        for row in dropped:
            emit(f"  dropped {row['id']}: {row['reason']}")
    return status if status in (2, 3, 4, 5) else 5


# --------------------------------------------------------------------------- health / budget / doctor
def cmd_health(args):
    cfg, _ = load(args)
    project = project_of(args)
    state = cfg_mod.load_state(project)
    rows = []
    for pid, setting in (cfg.get("providers") or {}).items():
        health = cfg_mod.provider_health(state, pid)
        used = cfg_mod.usage_today(state, pid)
        cooling = cfg_mod.cooling(state, pid)
        rows.append([
            pid, health["status"], cfg_mod.seconds_to_text(cooling) if cooling else "-",
            health.get("kind") or "-",
            used["calls"], used["tokens"],
            (health.get("lastProbe") or {}).get("message") or health.get("reason") or "",
        ])
    if _json(args):
        emit(json.dumps({"providers": rows,
                         "state": cfg_mod.paths(project)["state"]},
                        ensure_ascii=False, indent=2))
        return 0
    if not rows:
        emit("no HTTP providers configured")
    else:
        _table(rows, ["provider", "health", "cooldown", "kind", "calls", "tokens", "detail"])
    codex = _codex_path()
    emit("")
    emit(f"codex lane: {'codex.exe found' if codex else 'codex.exe NOT found'}"
         f"{' (' + codex + ')' if codex else ''}")
    emit(f"dsh lane  : this session is the lead (no probe exists or is needed)")
    return 0


def cmd_budget(args):
    cfg, _ = load(args)
    project = project_of(args)
    state = cfg_mod.load_state(project)
    quota = model_route.quota_snapshot(cfg, state, project, read=not args.no_quota)
    rows = []
    for pid, setting in (cfg.get("providers") or {}).items():
        used = cfg_mod.usage_today(state, pid)
        limits = setting.get("limits") or {}
        rows.append([
            pid, used["calls"], limits.get("callsPerDay") or "-",
            used["tokens"], limits.get("tokensPerDay") or "-",
            cfg_mod.budget_block(setting, state, pid) or "ok",
        ])
    if _json(args):
        emit(json.dumps({"providers": rows,
                         "codex": {k: quota.get(k) for k in
                                   ("remaining", "weekly_remaining", "hours_left",
                                    "tier", "source", "stale")}},
                        ensure_ascii=False, indent=2))
        return 0
    if rows:
        _table(rows, ["provider", "calls today", "call limit", "tokens today",
                      "token limit", "state"])
    if quota:
        tier = quota.get("tier") or "U"
        emit(f"codex quota: 5h {quota.get('remaining')}% left "
             f"(weekly {quota.get('weekly_remaining')}%, "
             f"tier {tier}, source {quota.get('source') or 'none'})")
        if quota.get("stale"):
            warn(quota.get("stale_reason") or "stale reading")
    else:
        emit("codex quota: unknown (run `python scripts/quota.py`)")
    return 0


def cmd_doctor(args):
    cfg, meta = load(args)
    project = project_of(args)
    problems = []
    emit(f"skill     v{SKILL_VERSION}")
    emit(f"project   {project}")
    emit(f"config    {meta['project_config']}")
    for source in meta["sources"]:
        emit(f"          merged: {source}")
    emit(f"state     {cfg_mod.paths(project)['state']}")
    emit(f"python    {sys.version.split()[0]} ({sys.executable})")

    codex = _codex_path()
    # doctor always inspects the real runner; the COORD_RUN_GPT override is a test
    # seam for the delegation path, not a fact about this installation.
    run_gpt = os.path.join(HERE, "run_gpt.py")
    emit(f"codex     {codex or 'NOT FOUND'}")
    if cfg_mod.load_config(project)[0].get("lanes", {}).get("codex", {}).get("enabled") is not False:
        if not codex:
            problems.append("codex lane is enabled but codex.exe was not found")
        if not os.path.isfile(run_gpt):
            problems.append(f"protected runner missing: {run_gpt}")
    emit(f"runner    {run_gpt} ({'present' if os.path.isfile(run_gpt) else 'MISSING'})")
    emit("dsh lane  this session is the lead; scripts never spawn a second caller")

    cred_path = cfg_mod.paths(project)["credentials"]
    store = cfg_mod.load_credentials(project)
    if os.path.isfile(cred_path):
        acl = cfg_mod.acl_status(cred_path)
        emit(f"credentials {cred_path} "
             f"({len(store)} key(s); values never printed)")
        emit(f"            {acl['detail']}")
        if not acl["ok"]:
            problems.append(f"credential store is not private: {acl['detail']}")
    else:
        emit("credentials none stored in this project")
    proxy = {key: os.environ.get(key) for key in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")
             if os.environ.get(key)}
    emit(f"proxy     {proxy or 'unset (direct)'}")
    for warning in meta["warnings"]:
        emit(f"[WARN] {warning}")
    try:
        state = cfg_mod.load_state(project)
        entries = model_route.catalog(cfg, state, project)
        emit(f"catalog   {len(entries)} model(s), "
             f"{sum(1 for e in entries if e.get('usable'))} usable")
    except cfg_mod.ConfigError as exc:
        problems.append(str(exc))
    if problems:
        emit("")
        for problem in problems:
            emit(f"[FAIL] {problem}")
        return 1
    emit("verdict   ok")
    return 0


# --------------------------------------------------------------------------- parser
def _global_options(parser, suppress=False):
    """Global options live on the main parser and on every subparser.

    On a subparser the default is ``SUPPRESS``: otherwise the subparser's default
    would overwrite a value the user passed *before* the subcommand
    (``models.py -C proj init`` would silently run against the current directory).
    """
    project_default = argparse.SUPPRESS if suppress else "."
    scope_default = argparse.SUPPRESS if suppress else "project"
    json_default = argparse.SUPPRESS if suppress else False
    parser.add_argument("-C", "--project", default=project_default,
                        help="project directory (model config lives in <project>/_work/models)")
    parser.add_argument("--scope", choices=("project", "user"), default=scope_default,
                        help="which config/credential store a write targets")
    parser.add_argument("--json", action="store_true", default=json_default,
                        help="machine-readable output")
    return parser


def build_parser():
    parser = _global_options(argparse.ArgumentParser(
        prog="models.py",
        description="Unified model access and management across codex, dsh and "
                    "HTTP provider lanes."))
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, func, help_text):
        child = sub.add_parser(name, help=help_text, description=help_text)
        _global_options(child, suppress=True)
        child.set_defaults(func=func)
        return child

    p = add("init", cmd_init, "create the per-project model config")
    p.add_argument("--force", action="store_true")
    p.add_argument("--with-local", action="store_true",
                   help="enable Ollama / LM Studio when their local ports answer")

    p = add("providers", cmd_providers, "list the vendored provider registry")
    p.add_argument("--search", default="")
    p.add_argument("--category")
    p.add_argument("--format")
    p.add_argument("--auth", dest="auth")
    p.add_argument("--configured", action="store_true",
                   help="only providers this project configured")

    p = add("add", cmd_add, "configure a registry provider for this project")
    p.add_argument("provider")
    p.add_argument("--models", action="append", default=[])
    p.add_argument("--base-url", dest="base_url")
    p.add_argument("--format")
    p.add_argument("--api-key-env", dest="api_key_env")
    p.add_argument("--no-enable", action="store_true")

    p = add("add-custom", cmd_add_custom,
            "configure an endpoint the registry does not know (local / self-hosted / gateway)")
    p.add_argument("id")
    p.add_argument("--base-url", dest="base_url", required=True)
    p.add_argument("--format", default="openai-chat")
    p.add_argument("--models", action="append", default=[])
    p.add_argument("--models-url", dest="models_url")
    p.add_argument("--chat-url", dest="chat_url",
                   help="full completion endpoint (for services with no /chat/completions)")
    p.add_argument("--chat-path", dest="chat_path",
                   help="completion path appended to --base-url")
    p.add_argument("--name")
    p.add_argument("--auth", choices=("none", "apiKey", "token"), default="none")
    p.add_argument("--api-key-env", dest="api_key_env")
    p.add_argument("--local", action="store_true")
    p.add_argument("--no-enable", action="store_true")

    p = add("remove", cmd_remove, "remove a provider from this project")
    p.add_argument("provider")
    p.add_argument("--purge-key", action="store_true")

    p = add("enable", cmd_enable, "enable a configured provider")
    p.add_argument("provider")
    p.set_defaults(enable=True)
    p = add("disable", cmd_enable, "disable a configured provider")
    p.add_argument("provider")
    p.set_defaults(enable=False)

    p = add("key", cmd_key, "store / clear / inspect a provider credential (never printed)")
    p.add_argument("key_command", choices=("set", "clear", "status"))
    p.add_argument("provider")
    p.add_argument("--from-env", dest="from_env")

    p = add("account", cmd_account,
            "vendor-mediated account login / status / logout (no password handled here)")
    p.add_argument("account_command", choices=("login", "status", "logout", "list"))
    p.add_argument("provider", nargs="?", default="")

    p = add("list", cmd_list, "the project's model catalog with availability")
    p.add_argument("--provider")
    p.add_argument("--usable", action="store_true")

    p = add("show", cmd_show, "details for one model id")
    p.add_argument("model")

    p = add("discover", cmd_discover, "fetch a provider's live model listing")
    p.add_argument("provider")
    p.add_argument("--timeout", type=int, default=20)

    p = add("test", cmd_test, "verify a provider or model is reachable")
    p.add_argument("target")
    p.add_argument("--generate", action="store_true",
                   help="spend one small completion (needed when no listing exists)")
    p.add_argument("--timeout", type=int, default=30)

    p = add("use", cmd_use, "switch the model a role uses")
    p.add_argument("role")
    p.add_argument("model")

    p = add("route", cmd_route, "choose a model for a role (quota / capability / health)")
    p.add_argument("--role", required=True)
    p.add_argument("--needs", action="append", default=[],
                   help="comma list: vision, reasoning, min-context=NNN")
    p.add_argument("--exclude-family", dest="exclude_family", action="append", default=[])
    p.add_argument("--producer", help="the producing model id; its family is excluded "
                                      "from an independent review")
    p.add_argument("--pin", help="use exactly this id")
    p.add_argument("--allow-unknown-family", dest="allow_unknown_family",
                   action="store_true",
                   help="let a model whose family cannot be established serve as an "
                        "independent review (it cannot be trusted to be independent)")
    p.add_argument("--no-quota", action="store_true",
                   help="do not read the codex quota pool (no subprocess)")

    p = add("chains", cmd_chains, "named fallback chains")
    p.add_argument("chain_command", choices=("list", "show", "set", "remove"))
    p.add_argument("name", nargs="?", default="")
    p.add_argument("--models", action="append", default=[])
    p.add_argument("--strategy", choices=("fallback", "round-robin", "smart", "pace"))
    p.add_argument("--sticky", type=int, default=1)
    p.add_argument("--no-quota", action="store_true")

    p = add("call", cmd_call, "call one model, a chain, or the routed model for a role")
    p.add_argument("--model")
    p.add_argument("--chain")
    p.add_argument("--role")
    p.add_argument("--prompt-file", dest="prompt_file")
    p.add_argument("--prompt")
    p.add_argument("--system")
    p.add_argument("--output")
    p.add_argument("--max-tokens", dest="max_tokens", type=int)
    p.add_argument("--timeout", type=int)
    p.add_argument("--sandbox", choices=("read-only", "workspace-write", "danger-full-access"))
    p.add_argument("--request-id", dest="request_id")
    p.add_argument("--needs", action="append", default=[])
    p.add_argument("--exclude-family", dest="exclude_family", action="append", default=[])
    p.add_argument("--allow-unknown-family", dest="allow_unknown_family",
                   action="store_true")
    p.add_argument("--no-quota", action="store_true")
    p.add_argument("--dry-run", dest="dry_run", action="store_true")

    add("health", cmd_health, "provider health, cooldowns and usage")
    p = add("budget", cmd_budget, "declared budgets and the codex quota pool")
    p.add_argument("--no-quota", action="store_true")
    add("doctor", cmd_doctor, "check config, credentials, lanes and the protected paths")
    return parser


def main():
    args = build_parser().parse_args()
    try:
        cfg_mod.ensure_project(project_of(args))
        return args.func(args)
    except cfg_mod.ConfigError as exc:
        return fail(str(exc), 2)
    except model_route.RouteError as exc:
        return fail(str(exc), 3)
    except KeyboardInterrupt:
        return fail("interrupted", 1)


if __name__ == "__main__":
    sys.exit(main())

