#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Vendored provider registry and the `provider/model` id grammar.

The data lives in ``assets/providers.registry.json``: 76 providers with their
category (``free`` / ``free-tier`` / ``apikey`` / ``local`` / ``oauth``), auth
kind, wire format, base URL, model listing URL and published model rows. It is
vendored from the MIT-licensed ``dsh-our-free-model`` project (see
``assets/providers.registry.NOTICE.md``); nothing in this module reaches the
network, so a typo can never turn into a live request.

An entry id is ``provider/model`` — for example
``deepseek/deepseek-v4-pro`` or ``nvidia_nim/nvidia/nemotron-3-super-120b-a12b``.
Model ids may contain slashes, so the split happens on the *first* slash only.
The two protected lanes use their own prefixes and never appear here:

    codex:<slug>   GPT through the codex CLI (scripts/run_gpt.py)
    dsh:<slug>     DeepSeek through the DSH host session (the lead)

This module is deliberately free of policy: whether a provider may be called,
and with which credential, is ``model_config.py``; whether it is reachable is
``model_http.py``; which one to pick is ``model_route.py``.
"""
import json
import os
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY_PATH = os.path.abspath(os.path.join(HERE, "..", "assets", "providers.registry.json"))

#: Wire formats the registry names, and the path a completion is posted to.
FORMATS = ("openai-chat", "openai-responses", "anthropic-messages")
CHAT_SUFFIX = {
    "openai-chat": "/chat/completions",
    "openai-responses": "/responses",
    "anthropic-messages": "/messages",
}
AUTH_KINDS = ("apiKey", "none", "oauth", "adc")
CATEGORIES = ("apikey", "free", "free-tier", "local", "oauth", "custom")

_LOCK = threading.Lock()
_CACHE = {}

#: Provider -> lab, only where the provider is a single lab's own API. Aggregators
#: (OpenRouter-shaped gateways, NVIDIA NIM, Groq, Together, Fireworks, local servers)
#: are deliberately absent: they host many labs, so the model id must decide, and
#: an unrecognised row stays `known=False`.
FAMILIES = {
    "codex": "openai",
    "openai": "openai",
    "azure_openai": "openai",
    "dsh": "deepseek",
    "deepseek": "deepseek",
    "anthropic": "anthropic",
    "google": "google",
    "vertex": "google",
    "gemini": "google",
    "meta": "meta",
    "qwen": "qwen",
    "alibaba_cloud": "qwen",
    "qwencloud": "qwen",
    "mistral": "mistral",
    "cohere": "cohere",
    "zhipu": "zhipu",
    "moonshot": "moonshot",
    "minimax": "minimax",
    "xai": "xai",
}

#: Model-id prefixes that name the lab that trained the weights. Boundaries matter:
#: `gpt-j-6b` is EleutherAI, `commander-7b` is not Cohere, `novalite-1` is not
#: Amazon. A prefix that is too loose certifies independence it cannot prove.
MODEL_FAMILY_PREFIXES = (
    ("openai/", "openai"), ("azure/", "openai"),
    ("gpt-3", "openai"), ("gpt-4", "openai"), ("gpt-5", "openai"),
    ("chatgpt", "openai"), ("o1-", "openai"), ("o3-", "openai"), ("o4-", "openai"),
    ("gpt-j", "eleutherai"), ("gpt-neox", "eleutherai"), ("pythia", "eleutherai"),
    ("anthropic/", "anthropic"), ("claude", "anthropic"),
    ("deepseek", "deepseek"),
    ("google/", "google"), ("gemini", "google"), ("gemma", "google"),
    ("meta-llama/", "meta"), ("meta/", "meta"), ("llama", "meta"),
    ("qwen", "qwen"), ("qwq", "qwen"),
    ("mistral", "mistral"), ("mixtral", "mistral"), ("codestral", "mistral"),
    ("moonshot", "moonshot"), ("kimi", "moonshot"),
    ("z-ai/", "zhipu"), ("zhipu", "zhipu"), ("glm", "zhipu"),
    ("minimax", "minimax"), ("x-ai/", "xai"), ("grok", "xai"),
    ("cohere", "cohere"), ("command-r", "cohere"), ("command-a", "cohere"),
    ("c4ai", "cohere"),
    ("ai21", "ai21"), ("jamba", "ai21"),
    ("amazon/", "amazon"), ("nova-", "amazon"),
    ("microsoft/", "microsoft"), ("phi-", "microsoft"),
    ("nousresearch/", "nous"), ("hermes", "nous"),
)

#: Substrings used only when a repository prefix is absent (`deepseek-v4-pro`).
MODEL_FAMILY_NEEDLES = (
    ("gpt-", "openai"), ("claude", "anthropic"), ("deepseek", "deepseek"),
    ("gemini", "google"), ("gemma", "google"), ("qwen", "qwen"),
    ("mistral", "mistral"), ("llama", "meta"), ("kimi", "moonshot"),
    ("glm", "zhipu"), ("minimax", "minimax"), ("grok", "xai"),
)


def registry():
    """The parsed registry, read once per process (a list of provider records)."""
    with _LOCK:
        if "providers" not in _CACHE:
            with open(REGISTRY_PATH, encoding="utf-8") as fh:
                _CACHE["providers"] = json.load(fh)
            _CACHE["by_id"] = {row["id"]: row for row in _CACHE["providers"]}
        return _CACHE["providers"]


def provider(provider_id):
    """One vendored provider record, or None."""
    registry()
    return _CACHE["by_id"].get(str(provider_id or "").strip())


def providers(category=None, format=None, auth_kind=None, search=""):
    """Filter the vendored registry; every argument is optional."""
    needle = str(search or "").strip().lower()
    out = []
    for row in registry():
        if category is not None and row.get("category") != category:
            continue
        if format is not None and row.get("format") != format:
            continue
        if auth_kind is not None and (row.get("auth") or {}).get("kind") != auth_kind:
            continue
        if needle:
            haystack = " ".join([
                str(row.get("id")), str(row.get("name")), str(row.get("website")),
                str(row.get("notes")), str((row.get("auth") or {}).get("env")),
                " ".join(model["id"] for model in row.get("models") or []),
            ]).lower()
            if needle not in haystack:
                continue
        out.append(row)
    return out


def compose(provider_id, model_id):
    """`provider` + `model` -> canonical id, or None when either part is empty."""
    pid = str(provider_id or "").strip()
    model = str(model_id or "").strip()
    if pid == "" or model == "":
        return None
    return f"{pid}/{model}"


def parse(model_id):
    """Split an aggregate id on its first slash.

    Returns ``{"provider": pid, "model": model}`` or None. ``codex:`` and
    ``dsh:`` ids are rejected here on purpose: they are lanes, not providers.
    """
    text = str(model_id or "").strip()
    if ":" in text.split("/", 1)[0]:
        return None
    slash = text.find("/")
    if slash <= 0:
        return None
    pid, model = text[:slash], text[slash + 1:].strip()
    if pid == "" or model == "":
        return None
    if provider(pid) is None:
        return None
    return {"provider": pid, "model": model}


def is_lane_id(model_id):
    """True for `codex:...` / `dsh:...` / `chain:...`, False for `provider/model`."""
    head, sep, _ = str(model_id or "").partition(":")
    return bool(sep) and head in ("codex", "dsh", "chain")


def _trim(url):
    return str(url or "").strip().rstrip("/")


def base_url(record, override=None):
    """The base URL to dial: the user's override when given, else the registry's."""
    return _trim(override) or _trim((record or {}).get("baseUrl"))


def models_url(record, override=None):
    """The provider's model-listing URL, moved under a base override when it applies."""
    declared = _trim((record or {}).get("modelsUrl"))
    if declared == "":
        return None
    base = _trim(override)
    registry_base = _trim((record or {}).get("baseUrl"))
    if base and registry_base and declared.startswith(registry_base):
        return base + declared[len(registry_base):]
    return declared


def chat_url(record, override=None):
    """The URL one completion is posted to, or None when it cannot be composed."""
    record = record or {}
    suffix = CHAT_SUFFIX.get(record.get("format"))
    base = base_url(record, override)
    if not base or suffix is None:
        return None
    if "{" in base and "}" in base:
        # A registry placeholder (`{account_id}`) is not a dialable host.
        return None
    if record.get("format") == "openai-chat" and base.rstrip("/").endswith("/chat/completions"):
        return base
    return base + suffix


def headers(record, api_key=None, extra=None):
    """Minimal headers for one provider on its declared wire.

    Source-declared transport headers come first so protocol and credential
    headers always win a collision. A key is attached for ``apiKey`` and
    ``token`` auth only: keyless providers must not send an authorization
    header, and oauth/adc credentials are minted by their own flow.
    """
    record = record or {}
    out = {}
    for name, value in (record.get("headers") or {}).items():
        out[str(name).lower()] = str(value)
    if record.get("format") == "anthropic-messages":
        out["anthropic-version"] = "2023-06-01"
    out["content-type"] = "application/json"
    key = str(api_key or "").strip()
    kind = (record.get("auth") or {}).get("kind")
    if key and kind in ("apiKey", "token"):
        if record.get("format") == "anthropic-messages":
            out["x-api-key"] = key
        else:
            out["authorization"] = f"Bearer {key}"
    for name, value in (extra or {}).items():
        out[str(name).lower()] = str(value)
    return out


def undrivable(record):
    """Why this provider cannot be driven from the skill, or None.

    ``oauth`` and ``adc`` credentials are minted by an interactive sign-in or a
    cloud identity flow; a request built here would carry no usable credential.
    The registry lists them so a picker can show the whole market honestly, and
    the skill refuses them with a reason instead of shipping a guaranteed 401.
    """
    kind = ((record or {}).get("auth") or {}).get("kind")
    if kind == "oauth":
        return ("this provider authenticates through an interactive OAuth sign-in "
                "that this skill cannot drive; log in with the vendor's own client "
                "and expose the resulting token as a `token` credential")
    if kind == "adc":
        return ("this provider uses Google application-default credentials; mint "
                "them with `gcloud auth application-default login` and import the "
                "token as a `token` credential")
    return None


def family_info(provider_id, record=None, model_id=None):
    """Capability provenance for one model, in three parts.

    A provider id is not a family: an aggregator sells GPT, Claude and DeepSeek
    rows under one id, and treating it as a fourth family would let a GPT model
    pose as an independent reviewer of a codex-produced artifact. So the model id
    decides first (``openai/gpt-*``, ``deepseek-*``, ``claude-*`` ...), the
    provider id second, and an unrecognised aggregator row is reported as
    **unknown** rather than guessed. ``known=False`` is what the router refuses to
    accept as an independent review unless the project config names the family.
    """
    pid = str(provider_id or "").strip().lower()
    model = str(model_id or "").strip().lower()
    if model:
        for prefix, fam in MODEL_FAMILY_PREFIXES:
            if model == prefix or model.startswith(prefix):
                return {"family": fam, "known": True, "source": "model"}
        for needle, fam in MODEL_FAMILY_NEEDLES:
            if needle in model:
                return {"family": fam, "known": True, "source": "model"}
    if pid in FAMILIES:
        return {"family": FAMILIES[pid], "known": True, "source": "provider"}
    # No display-name inference: "QwenCloud Coding Plan" or "OpenAI-compatible
    # gateway" may front many labs, and a name is not evidence of provenance.
    # A single-lab provider gets an explicit FAMILIES row above; anything else is
    # unknown until the model id or the project config says otherwise.
    return {"family": pid or "unknown", "known": False, "source": "unknown"}


def family(provider_id, record=None, model_id=None):
    """Capability family only (see :func:`family_info` for provenance)."""
    return family_info(provider_id, record, model_id)["family"]


def model_row(record, model_id):
    """The registry's published capability row for one model, or {}."""
    for row in (record or {}).get("models") or []:
        if row.get("id") == model_id:
            return row
    return {}


def capabilities(record, model_id):
    """Capability flags for one model, with honest defaults.

    The registry publishes ``vision`` / ``reasoning`` / ``contextWindow`` where
    the source declared them; most rows are null. Unknown vision is False (never
    send an image to a model that never claimed to read one) and unknown context
    takes the OpenAI-compatible default of 128K, which every row here accepts.
    """
    row = model_row(record, model_id)
    context = row.get("contextWindow")
    if not isinstance(context, int) or context <= 0:
        context = 131072
    return {
        "vision": row.get("vision") is True,
        "reasoning": row.get("reasoning") is not False,
        "contextWindow": context,
        "maxOutput": min(32768, context),
        "visionKnown": row.get("vision") is not None,
        "reasoningKnown": row.get("reasoning") is not None,
    }
