#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Project-scoped model configuration, credential store, runtime state.

Three files, three different lifetimes, all under ``<project>/_work/models/``:

    config.json       what the user configured        (safe to read, share, diff)
    credentials.json  secrets                          (0600, never printed)
    state.json        health, cooldowns, usage, rotation (runtime, disposable)

Per-project isolation is the point: a second project has its own ``_work/``,
so a chain created for one deliverable cannot silently reroute another. A
user-level store (``~/.coord-models``, overridable with ``COORD_MODELS_HOME``)
can share provider rosters and keys *on request* (``--scope user``), and the
project file always wins on a conflict.

Credential resolution order for one provider (first hit wins):

    1. ``credential`` spec in the provider's setting (env / file / command)
    2. ``apiKey`` written inline in the setting      (warned: it is a file)
    3. the credential store written by ``models.py key set`` (project, then user)
    4. the provider's declared environment variable (registry ``auth.env``)

Nothing here ever prints a secret. ``fingerprint()`` is the only representation
that may reach a terminal, a log or a JSON document.
"""
import contextlib
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_VERSION = 1

DEFAULT_CONFIG = {
    "version": CONFIG_VERSION,
    "comment": ("Project-scoped model access. Managed by scripts/models.py; "
                "see references/model-access.md. Secrets belong in credentials.json "
                "(models.py key set), not in this file."),
    "providers": {},
    "lanes": {
        # `auto` means "whatever the host tool is configured with", which is what
        # protects the existing call paths: codex resolves its model/effort from
        # ~/.codex/config.toml, and dsh is the lead session itself.
        "codex": {"enabled": True, "models": ["auto"], "effort": None,
                  "sandbox": "read-only"},
        "dsh": {"enabled": True, "models": ["auto"]},
    },
    "roles": {
        "lead": {"model": "dsh:auto"},
        "produce": {"model": "auto"},
        "review": {"model": "codex:auto"},
        "vision": {"model": "codex:auto"},
        "mechanical": {"model": "dsh:auto"},
    },
    "chains": {},
    "preference": [],
    "defaults": {
        "maxOutput": 4096,
        "timeoutSeconds": 180,
        "maxFallback": 3,
        "retryTransient": 1,
    },
}

ROLE_NAMES = ("lead", "produce", "review", "vision", "mechanical")


# --------------------------------------------------------------------------- paths
def user_home():
    return os.path.expanduser(os.environ.get("COORD_MODELS_HOME") or "~/.coord-models")


def paths(project="."):
    """Every path this layer owns for one project (absolute)."""
    root = os.path.abspath(project)
    models_dir = os.path.join(root, "_work", "models")
    return {
        "project": root,
        "models_dir": models_dir,
        "config": os.path.join(models_dir, "config.json"),
        "credentials": os.path.join(models_dir, "credentials.json"),
        "state": os.path.join(models_dir, "state.json"),
        "calls": os.path.join(models_dir, "model_calls.jsonl"),
        "team": os.path.join(models_dir, "team.json"),
        "team_scan": os.path.join(models_dir, "team_scan.json"),
    }


def user_paths():
    home = user_home()
    return {
        "home": home,
        "config": os.path.join(home, "config.json"),
        "credentials": os.path.join(home, "credentials.json"),
    }


def ensure_project(project="."):
    p = paths(project)
    os.makedirs(p["models_dir"], exist_ok=True)
    return p


# --------------------------------------------------------------------------- json io
def read_json(path):
    """Read JSON, returning (value, error). Missing file is (None, None).

    A concurrent writer replaces the file atomically, but on Windows a reader
    can still lose the race against `os.replace` with a transient PermissionError.
    Retry those briefly instead of reporting a corrupt state file.
    """
    for attempt in range(6):
        try:
            with open(path, encoding="utf-8-sig") as fh:
                return json.load(fh), None
        except FileNotFoundError:
            return None, None
        except PermissionError:
            if attempt == 5:
                return None, f"{path}: permission denied while another process writes"
            time.sleep(0.03)
        except OSError as exc:
            if attempt == 5:
                return None, f"{path}: {exc}"
            time.sleep(0.03)
        except ValueError as exc:
            return None, f"{path}: {exc}"
    return None, f"{path}: could not be read"


def atomic_write(path, text, mode=0o600):
    """Write atomically, then restrict permissions as far as the OS allows.

    Windows ignores POSIX mode bits, so an ACL restriction is attempted with
    ``icacls``. A failure there is not fatal (the file is still written), but it
    is reported by ``models.py doctor`` and by ``key set`` so the user knows the
    store is only as private as the profile directory.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        # Windows rejects os.replace while another process holds the destination
        # open without FILE_SHARE_DELETE. Concurrent state transactions are
        # expected, so retry briefly instead of failing the call.
        for attempt in range(12):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if attempt == 11:
                    raise
                time.sleep(0.02 + 0.01 * attempt)
    finally:
        if os.path.exists(temp):
            try:
                os.remove(temp)
            except OSError:
                pass
    try:
        os.chmod(path, mode)
    except OSError:
        pass
    if os.name == "nt":
        harden_acl(path)


def harden_acl(path):
    """Remove inherited ACEs and grant the current user full control.

    Returns True only when ``icacls`` actually succeeded. A silent failure used
    to leave the credential file readable by any local principal while the CLI
    still reported success; the return value is what ``doctor`` and ``key set``
    now use.
    """
    user = os.environ.get("USERNAME") or ""
    if not user:
        return False
    try:
        done = subprocess.run(
            ["icacls", path, "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def acl_status(path):
    """Read the effective permissions of a credential file.

    On POSIX this is the mode bits. On Windows it parses ``icacls`` and accepts
    only the current user, SYSTEM and Administrators; any other principal with
    access means the store is not private and ``doctor`` reports it.
    """
    if not os.path.isfile(path):
        return {"checked": False, "ok": False, "detail": "file missing"}
    if os.name != "nt":
        mode = os.stat(path).st_mode & 0o777
        return {"checked": True, "ok": not (mode & 0o077), "detail": f"mode {oct(mode)}"}
    try:
        done = subprocess.run(["icacls", path], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"checked": False, "ok": False, "detail": f"icacls unavailable: {exc}"}
    if done.returncode != 0:
        return {"checked": False, "ok": False,
                "detail": (done.stderr or "icacls failed").strip()[:200]}
    user = (os.environ.get("USERNAME") or "").lower()
    domain = (os.environ.get("USERDOMAIN") or "").lower()
    allowed = {"nt authority\\system", "builtin\\administrators",
               "nt authority\\local service", "nt authority\\network service"}
    if user:
        allowed.add(user)
        if domain:
            allowed.add(f"{domain}\\{user}")
    foreign = []
    for line in (done.stdout or "").splitlines():
        for match in re.finditer(r"([^\s:]+(?::\\[^\s:]+)?):\(([^)]*)\)", line):
            principal = match.group(1).lower()
            if principal.startswith("nt authority\\") or principal.startswith("builtin\\"):
                pass
            if principal in allowed:
                continue
            if principal.endswith("\\" + user):
                continue
            if re.fullmatch(r"[a-z]:", principal):
                continue
            foreign.append(match.group(1))
    foreign = sorted(set(foreign))
    if foreign:
        return {"checked": True, "ok": False,
                "detail": "other principals can read it: " + ", ".join(foreign[:3])}
    return {"checked": True, "ok": True, "detail": "ACL restricted to the current user"}


def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(moment=None):
    return (moment or now_utc()).isoformat()


def parse_ts(text):
    """Parse an ISO timestamp; None on anything unparsable."""
    if not isinstance(text, str) or text.strip() == "":
        return None
    try:
        value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def seconds_to_text(seconds):
    seconds = int(max(0, seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"
    if seconds >= 60:
        return f"{seconds // 60}m"
    return f"{seconds}s"


# --------------------------------------------------------------------------- config
def _merge(base, override):
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for key, value in override.items():
            out[key] = _merge(out.get(key), value) if key in out else value
        return out
    return override


def load_config(project="."):
    """Merged config: defaults <- user file <- project file. Returns (cfg, meta)."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    sources = []
    user, user_err = read_json(user_paths()["config"])
    if user_err:
        raise ConfigError(user_err)
    if isinstance(user, dict):
        cfg = _merge(cfg, user)
        sources.append(user_paths()["config"])
    project_cfg, project_err = read_json(paths(project)["config"])
    if project_err:
        raise ConfigError(project_err)
    if isinstance(project_cfg, dict):
        cfg = _merge(cfg, project_cfg)
        sources.append(paths(project)["config"])
    cfg.setdefault("providers", {})
    cfg.setdefault("lanes", json.loads(json.dumps(DEFAULT_CONFIG["lanes"])))
    cfg.setdefault("roles", json.loads(json.dumps(DEFAULT_CONFIG["roles"])))
    cfg.setdefault("chains", {})
    cfg.setdefault("preference", [])
    cfg.setdefault("defaults", dict(DEFAULT_CONFIG["defaults"]))
    meta = {
        "sources": sources,
        "project_config": paths(project)["config"],
        "user_config": user_paths()["config"],
        "warnings": validate_config(cfg),
    }
    return cfg, meta


class ConfigError(Exception):
    """A config file exists but cannot be used (invalid JSON, unknown format...)."""


def validate_config(cfg):
    """Collect non-fatal problems an agent should surface before routing."""
    warnings = []
    providers = cfg.get("providers") or {}
    if not isinstance(providers, dict):
        raise ConfigError("config: `providers` must be an object")
    for pid, setting in providers.items():
        if not isinstance(setting, dict):
            warnings.append(f"provider {pid!r}: setting must be an object")
            continue
        if pid.strip() == "" or "/" in pid:
            warnings.append(f"provider {pid!r}: ids may not contain '/'")
        fmt = setting.get("format")
        if fmt is not None and fmt not in ("openai-chat", "openai-responses",
                                          "anthropic-messages"):
            warnings.append(f"provider {pid!r}: unknown format {fmt!r}")
        auth = setting.get("auth")
        if auth is not None and not isinstance(auth, dict):
            warnings.append(f"provider {pid!r}: `auth` must be an object")
        models = setting.get("models")
        if models is not None and not (isinstance(models, list)
                                       and all(isinstance(m, str) and m.strip()
                                               for m in models)):
            warnings.append(f"provider {pid!r}: `models` must be a list of strings")
        limits = setting.get("limits")
        if limits is not None:
            if not isinstance(limits, dict):
                warnings.append(f"provider {pid!r}: `limits` must be an object")
            else:
                for key in ("callsPerDay", "tokensPerDay"):
                    value = limits.get(key)
                    if value is not None and (not isinstance(value, int) or value < 0):
                        warnings.append(f"provider {pid!r}: limits.{key} must be >= 0")
        if str(setting.get("apiKey") or "").strip():
            warnings.append(f"provider {pid!r}: an inline `apiKey` is readable by "
                            f"anything that opens this file; move it to `models.py key set`")
    for role, setting in (cfg.get("roles") or {}).items():
        if role not in ROLE_NAMES:
            warnings.append(f"role {role!r}: unknown role (known: {', '.join(ROLE_NAMES)})")
        elif not isinstance(setting, dict) or not isinstance(setting.get("model"), str):
            warnings.append(f"role {role!r}: needs {{'model': '<id>'}}")
    for name, chain in (cfg.get("chains") or {}).items():
        if not isinstance(chain, dict):
            warnings.append(f"chain {name!r}: must be an object")
            continue
        models = chain.get("models")
        if not (isinstance(models, list) and models
                and all(isinstance(m, str) and m.strip() for m in models)):
            warnings.append(f"chain {name!r}: `models` must be a non-empty list")
        strategy = chain.get("strategy", "fallback")
        if strategy not in ("fallback", "round-robin", "smart", "pace"):
            warnings.append(f"chain {name!r}: unknown strategy {strategy!r}")
    return warnings


def save_config(cfg, project=".", scope="project"):
    """Write the project (default) or user config file."""
    target = paths(project)["config"] if scope == "project" else user_paths()["config"]
    atomic_write(target, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", mode=0o600)
    return target


def write_default_config(project=".", force=False):
    target = paths(project)["config"]
    if os.path.exists(target) and not force:
        return target, False
    ensure_project(project)
    atomic_write(target, json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n",
                 mode=0o600)
    return target, True


# --------------------------------------------------------------------------- credentials
def fingerprint(secret):
    """`…abcd` — the only shape a secret may take in output."""
    text = str(secret or "")
    if text == "":
        return None
    return "…" + text[-4:] if len(text) > 4 else "…" + "*" * len(text)


def load_credentials(project=".", scope=None):
    """Merged credential store: {provider_id: {"key": str, "updatedAt": str}}."""
    merged = {}
    scopes = []
    if scope in (None, "user"):
        scopes.append(("user", user_paths()["credentials"]))
    if scope in (None, "project"):
        scopes.append(("project", paths(project)["credentials"]))
    for label, path in scopes:
        data, err = read_json(path)
        if err:
            raise ConfigError(err)
        for pid, row in ((data or {}).get("providers") or {}).items():
            if isinstance(row, dict) and str(row.get("key") or "").strip():
                merged[pid] = dict(row, scope=label)
    return merged


def set_key(provider_id, key, project=".", scope="project"):
    target = paths(project)["credentials"] if scope == "project" \
        else user_paths()["credentials"]
    data, err = read_json(target)
    if err:
        raise ConfigError(err)
    data = data if isinstance(data, dict) else {"version": 1, "providers": {}}
    data.setdefault("providers", {})
    if key is None or str(key).strip() == "":
        data["providers"].pop(provider_id, None)
    else:
        data["providers"][provider_id] = {
            "key": str(key).strip(),
            "updatedAt": iso(),
        }
    atomic_write(target, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return target


def _env(name):
    value = os.environ.get(str(name or ""))
    return value.strip() if value and value.strip() else None


def _credential_from_spec(spec, project="."):
    """Resolve one `credential` spec. Returns (value|None, source, error|None).

    A spec is explicit user intent, so its failure is reported rather than
    silently downgraded to "no credential".
    """
    if not isinstance(spec, dict):
        return None, None, "credential spec must be an object"
    kind = str(spec.get("source") or spec.get("kind") or "").strip()
    if kind == "env":
        name = str(spec.get("name") or "").strip()
        value = _env(name)
        if value is None:
            return None, None, f"environment variable {name} is unset or empty"
        return value, f"env:{name}", None
    if kind == "file":
        path = os.path.expanduser(str(spec.get("path") or "").strip())
        try:
            with open(path, encoding="utf-8-sig") as fh:
                raw = fh.read()
        except OSError as exc:
            return None, None, f"cannot read credential file {path}: {exc}"
        pointer = str(spec.get("json") or "").strip()
        if pointer:
            try:
                node = json.loads(raw)
                for part in pointer.split("."):
                    node = node[part]
                raw = str(node)
            except (ValueError, KeyError, TypeError) as exc:
                return None, None, f"{path}: json pointer {pointer!r} failed: {exc}"
        value = raw.strip().splitlines()[0].strip() if raw.strip() else ""
        if value == "":
            return None, None, f"credential file {path} is empty"
        return value, f"file:{path}", None
    if kind == "command":
        argv = spec.get("argv")
        command = str(spec.get("command") or "").strip()
        if isinstance(argv, list) and argv and all(isinstance(part, str) for part in argv):
            args = argv
        elif command:
            # Never `shell=True`: a project config must not be able to run an
            # arbitrary shell pipeline just because a read command touched it.
            args = shlex.split(command, posix=(os.name != "nt"))
        else:
            return None, None, "credential command is empty"
        timeout = int(spec.get("timeoutSeconds") or 30)
        try:
            done = subprocess.run(args, shell=False, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return None, None, f"credential command failed to run: {exc}"
        if done.returncode != 0:
            # stderr is withheld on purpose: a login helper is exactly the kind of
            # command that echoes a token on failure, and this message is stored in
            # state.json and printed by `key status` / `doctor`.
            return None, None, (f"credential command exited {done.returncode}; "
                                f"its stderr is withheld because it may contain the "
                                f"credential (run it yourself to see why)")
        value = (done.stdout or "").strip().splitlines()
        if not value:
            return None, None, "credential command produced no output"
        return value[0].strip(), f"command:{args[0]}", None
    return None, None, f"unknown credential source {kind!r} (env | file | command)"


def credential_declared(setting):
    """True when a credential is declared but may need a command to mint it.

    Catalog/list/route must not execute a credential command: those are read-only
    views of a project, and running a project-supplied command from them is a
    trust-boundary violation. The command runs only for an explicit call or test.
    """
    spec = (setting or {}).get("credential")
    if not isinstance(spec, dict):
        return False
    kind = str(spec.get("source") or spec.get("kind") or "").strip()
    return kind in ("env", "file", "command")


def api_key(record, setting, project=".", store=None, execute=True):
    """Resolve the credential for one provider.

    Returns ``(key|None, source|None, error|None)``. Never raises for an absent
    credential — that is a normal "unconfigured" state, not a crash.

    ``execute=False`` refuses to run a ``command`` credential spec: read-only
    views (list/route/doctor) call this with ``execute=False`` so opening a
    project cannot execute its config. Call and test paths keep the default.
    """
    setting = setting or {}
    spec = setting.get("credential")
    if isinstance(spec, dict):
        kind = str(spec.get("source") or spec.get("kind") or "").strip()
        if kind == "command" and not execute:
            return None, "command:deferred", None
        return _credential_from_spec(spec, project)
    inline = str(setting.get("apiKey") or "").strip()
    if inline:
        return inline, "config:inline", None
    if store is None:
        store = load_credentials(project)
    row = store.get(str((record or {}).get("id") or setting.get("id") or ""))
    if row:
        return str(row["key"]), f"store:{row.get('scope', 'project')}", None
    env_name = setting.get("apiKeyEnv") or ((record or {}).get("auth") or {}).get("env")
    value = _env(env_name)
    if value:
        return value, f"env:{env_name}", None
    return None, None, None


def key_status(provider_id, record, setting, project=".", execute=False):
    """Public (masked) credential state for one provider.

    Defaults to *not* running a credential command: `key status` is a read-only
    view, and the value only matters once a call is made.
    """
    store = load_credentials(project)
    value, source, error = api_key(record, setting, project, store, execute=execute)
    declared = credential_declared(setting)
    kind = str(((setting or {}).get("auth") or {}).get("kind")
               or ((record or {}).get("auth") or {}).get("kind") or "none")
    return {
        "provider": provider_id,
        "authKind": kind,
        "hasKey": bool(value) or (declared and value is None and error is None),
        "declared": declared,
        "minted": bool(value),
        "fingerprint": fingerprint(value),
        "source": source,
        "error": error,
    }


def redact(text, secrets):
    """Remove every known secret from a message before it is logged or printed.

    Providers echo rejected credentials back in error bodies; that text ends up
    in state.json and on a terminal, so the scrubbing happens at the boundary.
    """
    out = str(text or "")
    for secret in secrets:
        if secret and len(str(secret)) >= 4:
            out = out.replace(str(secret), "[redacted]")
    return out


# --------------------------------------------------------------------------- state
def default_state():
    return {
        "version": 1,
        "updatedAt": iso(),
        "providers": {},
        "models": {},
        "discovered": {},
        "usage": {},
        "rotation": {},
        "quota": {},
    }


def load_state(project="."):
    path = paths(project)["state"]
    data, err = read_json(path)
    if err:
        raise ConfigError(err)
    if not isinstance(data, dict):
        return default_state()
    state = default_state()
    state.update(data)
    for key in ("providers", "models", "discovered", "usage", "rotation", "quota"):
        if not isinstance(state.get(key), dict):
            state[key] = {}
    return state


def save_state(state, project="."):
    state["updatedAt"] = iso()
    atomic_write(paths(project)["state"],
                 json.dumps(state, ensure_ascii=False, indent=2) + "\n", mode=0o600)


_HELD_LOCKS = set()


@contextlib.contextmanager
def project_lock(project=".", timeout=30.0, stale_after=120.0):
    """A small cross-process lock for state transactions.

    Atomic replace keeps the file readable, but a load-modify-save from two
    processes still loses one update (usage, cooldown, rotation). The lock is an
    exclusive file next to the state; a lock older than two minutes is treated as
    abandoned (a killed process must not wedge the project).

    Re-entering the same lock **within one process** is an error, not a wait: it
    would block until the timeout and then report a busy project while the holder
    is ourselves.
    """
    lock_path = os.path.join(paths(project)["models_dir"], ".state.lock")
    key = os.path.normcase(os.path.abspath(lock_path))
    if key in _HELD_LOCKS:
        raise ConfigError("nested state transaction: mutate() must not call itself")
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    deadline = time.time() + timeout
    handle = None
    while True:
        try:
            handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(lock_path)
            except OSError:
                age = 0.0
            if age > stale_after:
                try:
                    os.remove(lock_path)
                except OSError:
                    pass
                continue
            if time.time() > deadline:
                raise ConfigError(f"state lock is busy: {lock_path} "
                                  f"(remove it if no process is running)")
            time.sleep(0.05)
    _HELD_LOCKS.add(key)
    try:
        os.write(handle, str(os.getpid()).encode("ascii", errors="replace"))
        os.close(handle)
        handle = None
        yield
    finally:
        if handle is not None:
            os.close(handle)
        _HELD_LOCKS.discard(key)
        try:
            os.remove(lock_path)
        except OSError:
            pass


def mutate(project, fn):
    """Apply one state mutation under the project lock; return the fresh state."""
    with project_lock(project):
        state = load_state(project)
        fn(state)
        save_state(state, project)
        return state


# --------------------------------------------------------------------------- task team
def load_team(project="."):
    """The task's approved team, or None when no team file exists.

    **Existence is the switch.** A missing file is the backward-compatible mode
    (every configured provider participates); any existing team document is an
    allowlist, even `{}` or `{"active": false}`. Treating a present-but-empty file
    as "no team" would silently re-open every provider after the user declined
    them. Malformed content fails closed by raising.
    """
    path = paths(project)["team"]
    if not os.path.isfile(path):
        return None
    data, err = read_json(path)
    if err:
        raise ConfigError(err)
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: a team file must be a JSON object")
    data.setdefault("added", {})
    data.setdefault("declined", {})
    if not isinstance(data["added"], dict) or not isinstance(data["declined"], dict):
        raise ConfigError(f"{path}: `added` and `declined` must be objects")
    data["active"] = True
    return data


def team_members(project="."):
    team = load_team(project)
    return set((team or {}).get("added") or {})


def update_team(project, fn):
    """Read-modify-write the team file (created on first call)."""
    path = paths(project)["team"]
    data, err = read_json(path)
    if err:
        raise ConfigError(err)
    data = data if isinstance(data, dict) else {}
    data.setdefault("version", 1)
    data.setdefault("added", {})
    data.setdefault("declined", {})
    data["active"] = True
    fn(data)
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return data


def save_team_scan(candidates, project="."):
    atomic_write(paths(project)["team_scan"],
                 json.dumps({"at": iso(), "candidates": candidates},
                            ensure_ascii=False, indent=2) + "\n")


def load_team_scan(project="."):
    data, err = read_json(paths(project)["team_scan"])
    if err:
        raise ConfigError(err)
    return (data or {}).get("candidates") or []


def provider_health(state, provider_id):
    row = (state.get("providers") or {}).get(provider_id) or {}
    return {
        "status": row.get("status") or "unknown",
        "kind": row.get("kind"),
        "reason": row.get("reason"),
        "until": row.get("until"),
        "failures": int(row.get("failures") or 0),
        "lastOkAt": row.get("lastOkAt"),
        "lastProbe": row.get("lastProbe"),
    }


def cooling(state, provider_id, model_id=None, now=None):
    """Is this provider (or model) inside a failure cooldown? Returns seconds left."""
    now = now or now_utc()
    candidates = []
    row = (state.get("providers") or {}).get(provider_id) or {}
    candidates.append(row.get("until"))
    if model_id:
        per_model = (state.get("models") or {}).get(f"{provider_id}/{model_id}") or {}
        candidates.append(per_model.get("until"))
    left = 0
    for text in candidates:
        moment = parse_ts(text)
        if moment and moment > now:
            left = max(left, int((moment - now).total_seconds()))
    return left


def mark_ok(state, provider_id, model_id=None, note=None):
    """Record a success.

    With a model id, only that model's cooldown is cleared: a successful sibling
    must never erase a provider-wide auth/billing/permission cooldown, or the next
    call walks straight back into the same credential wall. Without one, the
    provider itself is healthy.
    """
    row = (state.get("providers") or {}).get(provider_id) or {}
    now = now_utc()
    until = parse_ts(row.get("until"))
    if model_id:
        if not (until and until > now):
            row.update({"status": "ok", "kind": None, "reason": None, "until": None,
                        "failures": 0})
        row["lastOkAt"] = iso()
        state.setdefault("providers", {})[provider_id] = row
        state.setdefault("models", {}).pop(f"{provider_id}/{model_id}", None)
        return state
    row.update({"status": "ok", "kind": None, "reason": note or None, "until": None,
                "failures": 0, "lastOkAt": iso()})
    state.setdefault("providers", {})[provider_id] = row
    return state


def mark_failure(state, provider_id, kind, cooldown_seconds, reason, model_id=None,
                 scope="provider"):
    """Record one failure.

    `scope="provider"` (credential-level: auth, billing, permission) blocks the
    whole provider. `scope="model"` blocks only that model: a 429 or a 5xx on one
    model says nothing about the others behind the same endpoint, and a chain
    inside one provider must still be able to fall over.
    """
    until = iso(now_utc() + timedelta(seconds=max(0, int(cooldown_seconds))))
    row = (state.get("providers") or {}).get(provider_id) or {}
    failures = int(row.get("failures") or 0) + 1
    if scope == "provider":
        row.update({
            "status": "cooling" if cooldown_seconds > 0 else "error",
            "kind": kind, "reason": reason, "failures": failures,
            "until": until if cooldown_seconds > 0 else None,
        })
    else:
        row.update({"status": "degraded", "kind": kind, "reason": reason,
                    "failures": failures})
    state.setdefault("providers", {})[provider_id] = row
    if model_id and cooldown_seconds > 0:
        state.setdefault("models", {})[f"{provider_id}/{model_id}"] = {
            "kind": kind, "reason": reason, "until": until,
        }
    return state


def mark_probe(state, provider_id, ok, message, model_count=None):
    row = (state.get("providers") or {}).get(provider_id) or {}
    row["lastProbe"] = {
        "at": iso(),
        "ok": bool(ok),
        "message": message,
        "modelCount": model_count,
    }
    if ok:
        row.update({"status": "ok", "kind": None, "reason": None, "until": None,
                    "failures": 0, "lastOkAt": iso()})
    state.setdefault("providers", {})[provider_id] = row
    return state


def discovered_models(state, provider_id):
    row = (state.get("discovered") or {}).get(provider_id) or {}
    ids = row.get("ids")
    return list(ids) if isinstance(ids, list) else []


def set_discovered(state, provider_id, ids):
    state.setdefault("discovered", {})[provider_id] = {"ids": list(ids), "at": iso()}
    return state


def usage_today(state, provider_id, today=None):
    today = today or now_utc().date().isoformat()
    row = ((state.get("usage") or {}).get(today) or {}).get(provider_id) or {}
    return {"calls": int(row.get("calls") or 0), "tokens": int(row.get("tokens") or 0)}


def bump_usage(state, provider_id, calls=1, tokens=0, today=None):
    today = today or now_utc().date().isoformat()
    day = state.setdefault("usage", {}).setdefault(today, {})
    row = day.setdefault(provider_id, {"calls": 0, "tokens": 0})
    row["calls"] = int(row.get("calls") or 0) + int(calls)
    row["tokens"] = int(row.get("tokens") or 0) + int(tokens or 0)
    # Keep the file bounded: 60 days of counters is plenty for a routing decision.
    if len(state["usage"]) > 60:
        for key in sorted(state["usage"])[:-60]:
            state["usage"].pop(key, None)
    return state


def budget_block(setting, state, provider_id):
    """Reason string when a declared daily limit is exhausted, else None."""
    limits = (setting or {}).get("limits") or {}
    used = usage_today(state, provider_id)
    calls = limits.get("callsPerDay")
    if isinstance(calls, int) and calls >= 0 and used["calls"] >= calls:
        return f"daily call limit reached ({used['calls']}/{calls})"
    tokens = limits.get("tokensPerDay")
    if isinstance(tokens, int) and tokens >= 0 and used["tokens"] >= tokens:
        return f"daily token limit reached ({used['tokens']}/{tokens})"
    return None


def rotation(state, key):
    row = (state.get("rotation") or {}).get(key) or {}
    return {"index": int(row.get("index") or 0), "used": int(row.get("used") or 0)}


def set_rotation(state, key, index, used):
    state.setdefault("rotation", {})[key] = {"index": int(index), "used": int(used)}
    return state


def prune(state):
    """Drop cooldowns that expired long ago so the state file stays readable."""
    now = now_utc()
    for section in ("providers", "models"):
        for key, row in list((state.get(section) or {}).items()):
            until = parse_ts((row or {}).get("until"))
            if until and until < now - timedelta(days=7):
                if section == "providers":
                    row["until"] = None
                    row["status"] = "unknown" if row.get("status") == "cooling" else row.get("status")
                else:
                    state[section].pop(key, None)
    return state


def has_timezone(text):
    return bool(re.search(r"(Z|[+-]\d{2}:?\d{2})$", str(text or "")))
