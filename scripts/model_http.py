#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""HTTP transport for the aggregate lane: listing probes and one completion.

Three wire formats are supported because that is what the vendored registry
declares: OpenAI Chat Completions, OpenAI Responses and Anthropic Messages.
Nothing is streamed here — a coordination call is a bounded piece of work whose
result the lead has to read in full anyway, and a non-streaming response is the
shape every OpenAI-compatible local server already implements (Ollama, LM Studio,
llama.cpp, vLLM).

Failure classification follows the reference project's vocabulary
(``dsh-our-free-model/src/route/classify.js``), reduced to what a synchronous
call can observe:

    auth / billing        credential or money — no retry, long cooldown
    permission            key valid, action refused — no retry, long cooldown
    rate_limit            ertry after the provider's own reset, else 15 min
    overloaded            provider capacity — retry, 2 min
    upstream / timeout    transient — retry once, then the next chain link
    model_missing         wrong slug for this provider — 1 h for that model
    invalid_request        our bug, not the provider's — never silently fail over
    transport             DNS/proxy/socket — retry, 1 min

The request-scoped verdict is the important one: a malformed body must surface
as a defect in this skill, not be masked by walking to another provider.
"""
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from model_registry import chat_url, headers as registry_headers, models_url

USER_AGENT = "coord-multimodel/2.1 (+https://github.com/zlzyangreal/coordination_of_multiple_large_models)"
READ_LIMIT = 8 * 1024 * 1024


class CallFailure(Exception):
    """One classified call failure. `should_fallback` mirrors the reference rule."""

    def __init__(self, kind, scope, message, status=None, cooldown=0,
                 retryable=False, retry_after=None):
        super().__init__(message)
        self.kind = kind
        self.scope = scope
        self.message = message
        self.status = status
        self.cooldown = int(cooldown)
        self.retryable = bool(retryable)
        self.retry_after = retry_after

    @property
    def should_fallback(self):
        """A request-scoped defect must not be hidden by another provider."""
        if self.scope == "request":
            return False
        return bool(self.retryable or self.cooldown > 0)

    def as_dict(self):
        return {
            "kind": self.kind,
            "scope": self.scope,
            "status": self.status,
            "cooldownSeconds": self.cooldown,
            "retryable": self.retryable,
            "shouldFallback": self.should_fallback,
            "message": self.message,
        }


COOLDOWNS = {
    "auth": 6 * 3600,
    "billing": 12 * 3600,
    "permission": 6 * 3600,
    "rate_limit": 15 * 60,
    "overloaded": 2 * 60,
    "upstream": 2 * 60,
    "timeout": 60,
    "model_missing": 3600,
    "invalid_request": 0,
    "transport": 60,
    "empty_response": 30,
}

RETRYABLE = {"rate_limit", "overloaded", "upstream", "timeout", "transport",
             "empty_response"}

TEXT_RULES = (
    # marker fragment, kind
    ("insufficient_quota", "billing"),
    ("exceeded your current quota", "billing"),
    ("billing", "billing"),
    ("quota", "rate_limit"),
    ("rate limit", "rate_limit"),
    ("rate_limit", "rate_limit"),
    ("too many requests", "rate_limit"),
    ("resource exhausted", "overloaded"),
    ("capacity", "overloaded"),
    ("overloaded", "overloaded"),
    ("model not found", "model_missing"),
    ("model_not_found", "model_missing"),
    ("no such model", "model_missing"),
    ("does not exist", "model_missing"),
    ("not available in your", "permission"),
    ("region", "permission"),
    ("invalid api key", "auth"),
    ("incorrect api key", "auth"),
    ("unauthorized", "auth"),
    ("permission denied", "permission"),
    ("context length", "context"),
    ("maximum context", "context"),
    ("too many tokens", "context"),
)


def _now():
    return datetime.now(timezone.utc)


def _retry_after_seconds(value):
    text = str(value or "").strip()
    if text == "":
        return None
    try:
        return max(0, int(float(text)))
    except ValueError:
        pass
    try:
        moment = datetime.strptime(text, "%a, %d %b %Y %H:%M:%S %Z")
        return max(0, int((moment.replace(tzinfo=timezone.utc) - _now()).total_seconds()))
    except (ValueError, TypeError):
        return None


MISSING_MARKERS = ("not found", "does not exist", "not exist", "missing",
                   "unknown", "unsupported", "not available", "unavailable")
MODEL_MISSING_CODES = {"model_not_found", "model_not_found_error", "unknown_model",
                       "model_does_not_exist", "invalid_model", "unsupported_model"}
ENDPOINT_NOUNS = re.compile(r"\b(endpoints?|routes?|paths?|urls?|services?|gateways?|"
                            r"hosts?|prox(?:y|ies)|apis?)\b", re.I)
MODEL_NOUNS = re.compile(r"\bmodels?\b", re.I)
MISSING_RE = re.compile(r"\b(" + "|".join(re.escape(m) for m in MISSING_MARKERS) + r")\b",
                        re.I)


def _structured_model_code(text):
    """An exact provider error code that names a missing model, or None.

    A prefix match is wrong here: `model_service_endpoint_not_available` starts
    with "model" but is an endpoint refusal.
    """
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("code", "type", "reason", "error_code") and isinstance(value, str):
                    if value.strip().lower() in MODEL_MISSING_CODES:
                        return value.strip()
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return None


def _model_missing(status, text):
    """Does a 404 body actually say that the *model* is missing?

    Structured codes are exact. For prose, the subject of the missing predicate
    decides: "model x does not exist on this service" is about the model, while
    "model service endpoint not available" is about the endpoint. Bare "Not
    Found" names no model and stays endpoint-scoped.
    """
    if _structured_model_code(text):
        return True
    for sentence in re.split(r"[.;\n]", text):
        hit = MISSING_RE.search(sentence)
        if not hit:
            continue
        position = hit.start()
        models = [m.start() for m in MODEL_NOUNS.finditer(sentence)]
        if not models:
            continue
        endpoints = [m.start() for m in ENDPOINT_NOUNS.finditer(sentence)]
        before_models = [p for p in models if p < position]
        before_endpoints = [p for p in endpoints if p < position]
        if before_models:
            nearest = max(before_models)
            if not before_endpoints or nearest > max(before_endpoints):
                return True
        after_models = [p for p in models if p > position]
        if after_models and after_models[0] - position <= 40:
            if not [p for p in endpoints if position < p < after_models[0]]:
                return True
    return False


def classify(status, body, retry_after=None):
    """Map an upstream refusal to (kind, scope, retryable, cooldown, retry_after).

    Status wins over text: a 401 that happens to say "rate limit" in a proxy's
    HTML page is still an auth failure. Text markers are consulted for 4xx/5xx
    bodies that carry a machine-readable code in prose (Gemini, Cloudflare
    gateways, and several resellers do).
    """
    text = str(body or "").lower()
    marker_kind = None
    for marker, kind in TEXT_RULES:
        if marker in text:
            marker_kind = kind
            break
    if status in (401, 403):
        kind = "auth" if status == 401 else "permission"
        return kind, "account", False, COOLDOWNS[kind], None
    if status == 402:
        return "billing", "account", False, COOLDOWNS["billing"], None
    if status == 404:
        if _model_missing(404, text):
            return "model_missing", "model", False, COOLDOWNS["model_missing"], None
        return "endpoint_missing", "request", False, 0, None
    if status == 408:
        return "timeout", "transient", True, COOLDOWNS["timeout"], None
    if status == 429:
        wait = retry_after if retry_after is not None else COOLDOWNS["rate_limit"]
        wait = min(max(wait, 5), 2 * 3600)
        return "rate_limit", "account", True, wait, retry_after
    if status == 400:
        if marker_kind == "context":
            return "invalid_request", "request", False, 0, None
        if marker_kind in ("rate_limit", "billing", "overloaded"):
            kind = marker_kind
            return kind, "account", True, COOLDOWNS[kind], None
        return "invalid_request", "request", False, 0, None
    if status in (413, 422):
        return "invalid_request", "request", False, 0, None
    if status >= 500:
        kind = "overloaded" if status in (503, 529) else "upstream"
        return kind, "transient", True, COOLDOWNS[kind], None
    if marker_kind in ("rate_limit", "billing", "overloaded", "auth", "permission",
                       "model_missing", "context"):
        if marker_kind == "context":
            return "invalid_request", "request", False, 0, None
        return marker_kind, "account" if marker_kind != "overloaded" else "transient", \
            marker_kind in RETRYABLE, COOLDOWNS[marker_kind], None
    return "upstream", "transient", True, COOLDOWNS["upstream"], None


def error_message(status, body):
    """The most useful short string a provider's error body carries."""
    text = str(body or "").strip()
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            for key in ("message", "detail", "code", "type"):
                value = error.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()[:300]
        for key in ("message", "detail", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:300]
    return (text[:300] or f"HTTP {status}").replace("\n", " ")


def _open(url, method="GET", extra_headers=None, body=None, timeout=60):
    """One HTTP exchange. Returns (status, text, headers); raises CallFailure."""
    data = None
    hooks = {"User-Agent": USER_AGENT, "accept": "application/json"}
    hooks.update(extra_headers or {})
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method, headers=hooks)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(READ_LIMIT)
            charset = response.headers.get_content_charset() or "utf-8"
            return response.status, raw.decode(charset, errors="replace"), dict(response.headers)
    except urllib.error.HTTPError as exc:
        raw = b""
        try:
            raw = exc.read(READ_LIMIT)
        except OSError:
            pass
        charset = exc.headers.get_content_charset() if exc.headers else None
        return exc.code, raw.decode(charset or "utf-8", errors="replace"), \
            dict(exc.headers or {})
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, socket.timeout):
            raise CallFailure("timeout", "transient",
                              f"no answer within {timeout}s", cooldown=COOLDOWNS["timeout"],
                              retryable=True) from exc
        raise CallFailure("transport", "transient",
                          f"could not reach {urllib.parse.urlsplit(url).netloc}: {reason}",
                          cooldown=COOLDOWNS["transport"], retryable=True) from exc
    except socket.timeout as exc:
        raise CallFailure("timeout", "transient", f"no answer within {timeout}s",
                          cooldown=COOLDOWNS["timeout"], retryable=True) from exc
    except OSError as exc:
        raise CallFailure("transport", "transient", f"connection failed: {exc}",
                          cooldown=COOLDOWNS["transport"], retryable=True) from exc


def list_models(record, api_key=None, base_override=None, timeout=20):
    """GET the provider's model listing (a non-generating key/availability check).

    Returns ``{ok, status, ids, message}``; the caller decides whether an empty
    listing is authoritative. ``ids=None`` means "the listing was not usable".
    """
    url = models_url(record, base_override)
    if url is None:
        return {"ok": False, "status": 0, "ids": None,
                "message": f"{record.get('name') or record.get('id')} declares no "
                           f"model listing; a key can only be verified by spending "
                           f"a generation (models.py test --generate)"}
    status, text, _ = _open(url, extra_headers=registry_headers(record, api_key),
                            timeout=timeout)
    if status >= 400:
        kind, _, _, cooldown, _ = classify(status, text)
        return {"ok": False, "status": status, "ids": None,
                "message": error_message(status, text), "kind": kind,
                "cooldown": cooldown}
    ids = model_ids_from(text)
    if ids is None:
        return {"ok": False, "status": status, "ids": None,
                "message": f"the listing answered HTTP {status} but its shape was "
                           f"not recognised"}
    return {"ok": True, "status": status, "ids": ids,
            "message": f"the key is accepted; {len(ids)} model(s) listed"}


def model_ids_from(text):
    """Tolerant listing reader: OpenAI, Cohere, Ollama, DeepInfra dialects."""
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    rows = payload if isinstance(payload, list) else \
        payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), list) else \
        payload.get("models") if isinstance(payload, dict) and isinstance(payload.get("models"), list) else None
    if rows is None:
        return None
    seen = set()
    ids = []
    for row in rows:
        model = ""
        if isinstance(row, str):
            model = row.strip()
        elif isinstance(row, dict):
            for field in ("id", "name", "model", "model_name"):
                value = row.get(field)
                if isinstance(value, str) and value.strip():
                    model = value.strip()
                    break
        if model and model not in seen:
            seen.add(model)
            ids.append(model)
    return ids


def build_body(record, model_id, prompt, system=None, max_output=4096,
               temperature=None):
    """Compose the request body for the provider's declared wire."""
    fmt = record.get("format")
    if fmt == "openai-responses":
        body = {"model": model_id, "input": prompt, "max_output_tokens": int(max_output),
                "stream": False}
        if system:
            body["instructions"] = system
        return body
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    if fmt == "anthropic-messages":
        body = {"model": model_id, "messages": messages, "max_tokens": int(max_output),
                "stream": False}
        if system:
            body["system"] = system
            body["messages"] = messages[1:]
        return body
    body = {"model": model_id, "messages": messages, "max_tokens": int(max_output),
            "stream": False}
    if temperature is not None:
        body["temperature"] = float(temperature)
    return body


def extract_text(record, payload):
    """Pull the assistant text out of one completion payload."""
    fmt = record.get("format")
    if fmt == "anthropic-messages":
        blocks = payload.get("content")
        if isinstance(blocks, list):
            parts = [block.get("text") for block in blocks
                     if isinstance(block, dict) and block.get("type") in (None, "text")
                     and isinstance(block.get("text"), str)]
            if parts:
                return "\n".join(parts).strip()
        return ""
    if fmt == "openai-responses":
        direct = payload.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        parts = []
        for item in payload.get("output") or []:
            if not isinstance(item, dict):
                continue
            for block in item.get("content") or []:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
        return "\n".join(parts).strip()
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] or {}
        message = first.get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [part.get("text") for part in content
                     if isinstance(part, dict) and isinstance(part.get("text"), str)]
            if parts:
                return "\n".join(parts).strip()
        legacy = first.get("text")
        if isinstance(legacy, str):
            return legacy.strip()
    return ""


def extract_usage(payload):
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return {"input": 0, "output": 0, "total": 0}

    def pick(*names):
        for name in names:
            value = usage.get(name)
            if isinstance(value, (int, float)):
                return int(value)
        return 0

    prompt = pick("prompt_tokens", "input_tokens", "inputTokens")
    completion = pick("completion_tokens", "output_tokens", "outputTokens")
    total = pick("total_tokens", "totalTokens") or (prompt + completion)
    return {"input": prompt, "output": completion, "total": total}


def chat(record, model_id, prompt, api_key=None, base_override=None, system=None,
         max_output=4096, temperature=None, timeout=180):
    """POST one completion and return `{text, usage, status, latencySeconds}`.

    Raises CallFailure on every non-2xx answer, an unreadable body, an empty
    answer, or a transport problem. The caller owns retries and fallbacks.
    """
    url = chat_url(record, base_override)
    if url is None:
        raise CallFailure("invalid_request", "request",
                          f"no dialable endpoint for provider "
                          f"{record.get('id')!r}; set baseUrl in its config")
    body = build_body(record, model_id, prompt, system, max_output, temperature)
    started = time.time()
    status, text, response_headers = _open(
        url, method="POST",
        extra_headers=registry_headers(record, api_key),
        body=body, timeout=timeout)
    latency = round(time.time() - started, 2)
    if status >= 300:
        retry_after = _retry_after_seconds(
            (response_headers or {}).get("Retry-After")
            or (response_headers or {}).get("retry-after"))
        kind, scope, retryable, cooldown, _ = classify(status, text, retry_after)
        raise CallFailure(kind, scope, error_message(status, text), status=status,
                          cooldown=cooldown, retryable=retryable,
                          retry_after=retry_after)
    try:
        payload = json.loads(text)
    except ValueError:
        raise CallFailure("upstream", "transient",
                          f"HTTP {status} but the body was not JSON", status=status,
                          cooldown=COOLDOWNS["upstream"], retryable=True)
    answer = extract_text(record, payload)
    if answer == "":
        raise CallFailure("empty_response", "transient",
                          f"HTTP {status} but the answer carried no text", status=status,
                          cooldown=COOLDOWNS["empty_response"], retryable=True)
    fmt = record.get("format")
    if fmt == "anthropic-messages":
        finish = payload.get("stop_reason")
    elif fmt == "openai-chat":
        finish = (payload.get("choices") or [{}])[0].get("finish_reason")
    else:
        finish = payload.get("status")
    return {
        "text": answer,
        "usage": extract_usage(payload),
        "status": status,
        "latencySeconds": latency,
        "finishReason": finish,
    }
