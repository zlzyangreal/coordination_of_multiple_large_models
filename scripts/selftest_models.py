#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Offline self-test for the aggregate model lane.

Everything runs against two local mock providers, so the suite proves the
*plumbing* without spending a real credential or reaching the internet:

    mock_a  OpenAI Chat Completions, keyless      models: ok / 429 / 400 / flaky
    mock_b  Anthropic Messages, key required      echoes the key in a 401 body
    mock_c  OpenAI Responses, key required        daily budget test

Run: `python scripts/selftest_models.py`. Exit 0 only when every case passes.
The protected lanes are asserted structurally, never executed: `codex:*` must
resolve to a `run_gpt.py` command (dry-run) and `dsh:*` must be refused.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "models.py")
SKILL = os.path.dirname(HERE)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print(f"PASS  {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"FAIL  {name}  {detail}")


class MockState:
    flaky_hits = 0
    chat_calls = []
    posts = 0
    key_echo_requests = 0
    review_payload = None


class ChatHandler(BaseHTTPRequestHandler):
    """mock_a: keyless OpenAI chat + a tolerant listing."""

    def log_message(self, *_args):
        pass

    def _json(self, status, payload, extra_headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/v1/models":
            self._json(200, {"data": [{"id": "mock-ok"}, {"id": "mock-429"},
                                      {"id": "mock-400"}, {"id": "mock-flaky"},
                                      {"id": "mock-500"}]})
        elif self.path.rstrip("/") == "/v1/leak/models":
            key = self.headers.get("authorization") or ""
            self._json(401, {"error": {"message": f"invalid api key: {key}"}})
        elif self.path.rstrip("/") == "/v1/echo/models":
            key = self.headers.get("authorization") or ""
            self._json(200, {"data": [{"id": key}]})
        else:
            self._json(404, {"error": {"message": "no such path"}})

    def do_POST(self):
        MockState.posts += 1
        if not self.path.startswith("/v1/chat/completions"):
            self._json(404, {"error": {"message": "no such path"}})
            return
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        model = body.get("model")
        MockState.chat_calls.append({"model": model,
                                     "auth": self.headers.get("authorization"),
                                     "stream": body.get("stream")})
        if model == "mock-429":
            self._json(429, {"error": {"message": "rate limit exceeded; retry later"}},
                       {"Retry-After": "2"})
        elif model == "mock-400":
            self._json(400, {"error": {"message": "invalid request: messages[0] malformed"}})
        elif model == "mock-500":
            self._json(500, {"error": {"message": "internal server error"}})
        elif model == "mock-flaky":
            MockState.flaky_hits += 1
            if MockState.flaky_hits <= 2:
                self._json(500, {"error": {"message": "internal server error"}})
            else:
                self._json(200, {"choices": [{"message": {"content": "flaky recovered"},
                                              "finish_reason": "stop"}],
                                 "usage": {"prompt_tokens": 4, "completion_tokens": 2,
                                           "total_tokens": 6}})
        elif model == "mock-ok":
            self._json(200, {"choices": [{"message": {"content": "hello from mock A"},
                                          "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 5, "completion_tokens": 2,
                                       "total_tokens": 7}})
        elif model == "mock-review":
            self._json(200, {"choices": [{"message": {
                "content": MockState.review_payload or "{}"},
                "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2,
                          "total_tokens": 7}})
        elif model == "echo-key":
            key = self.headers.get("authorization") or ""
            self._json(200, {"choices": [{"message": {"content": f"your key is {key}"},
                                          "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 5, "completion_tokens": 2,
                                       "total_tokens": 7}})
        else:
            self._json(404, {"error": {"message": f"model {model} not found"}})


class MessagesHandler(BaseHTTPRequestHandler):
    """mock_b: Anthropic Messages, x-api-key required, echoes a bad key back."""

    def log_message(self, *_args):
        pass

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/v1/models":
            self._json(200, {"models": [{"name": "claude-mock"}]})
        else:
            self._json(404, {"error": {"message": "no such path"}})

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        key = self.headers.get("x-api-key") or ""
        MockState.key_echo_requests += 1
        if key != "test-key-1234":
            self._json(401, {"error": {"message": f"invalid api key: {key}"}})
            return
        self._json(200, {"content": [{"type": "text", "text": "anthropic ok"}],
                         "usage": {"input_tokens": 3, "output_tokens": 1},
                         "stop_reason": "end_turn"})


class ResponsesHandler(BaseHTTPRequestHandler):
    """mock_c: OpenAI Responses, Bearer required."""

    def log_message(self, *_args):
        pass

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not str(self.headers.get("authorization") or "").startswith("Bearer resp-key"):
            self._json(401, {"error": {"message": "bad bearer"}})
            return
        self._json(200, {"output": [{"type": "message",
                                     "content": [{"type": "output_text",
                                                  "text": "responses ok"}]}],
                         "usage": {"input_tokens": 4, "output_tokens": 2},
                         "status": "completed"})


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def run(project, args, env):
    proc = subprocess.run([sys.executable, MODELS, "-C", project] + args,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _patch_config(path, fn):
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    fn(cfg)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


def main():
    work = tempfile.mkdtemp(prefix="coord-models-selftest-")
    home = os.path.join(work, "user-home")
    project = os.path.join(work, "project")
    other = os.path.join(work, "other-project")
    os.makedirs(project)
    os.makedirs(other)
    env = dict(os.environ)
    env.update({
        "COORD_MODELS_HOME": home,
        "TEST_KEY": "test-key-1234",
        "RESP_KEY": "resp-key-5678",
        "LEAK_KEY": "sk-leak-SECRET-9999",
        "PYTHONIOENCODING": "utf-8",
    })

    server_a, port_a = serve(ChatHandler)
    server_b, port_b = serve(MessagesHandler)
    server_c, port_c = serve(ResponsesHandler)
    print(f"mock providers: A={port_a} B={port_b} C={port_c}")
    print(f"project:        {project}")
    try:
        cases(env, project, other, port_a, port_b, port_c)
        extended_cases(env, project, port_a, port_b, port_c)
        team_cases(env, work, port_a, port_b)
        plan_cases(env, work, port_a, port_b)
        gate_cases(env, work, port_a, port_b)
    finally:
        for server in (server_a, server_b, server_c):
            server.shutdown()
            server.server_close()

    print("")
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  FAILED {failure}")
    return 0 if not FAILED else 1


def cases(env, project, other, port_a, port_b, port_c):
    code, out, err = run(project, ["init"], env)
    check("init creates the project config", code == 0
          and os.path.isfile(os.path.join(project, "_work", "models", "config.json")),
          out + err)

    base_a = f"http://127.0.0.1:{port_a}/v1"
    base_b = f"http://127.0.0.1:{port_b}/v1"
    base_c = f"http://127.0.0.1:{port_c}/v1"
    for args in (
        ["add-custom", "mock_a", "--base-url", base_a, "--models",
         "mock-ok,mock-429,mock-400,mock-flaky,mock-500", "--auth", "none"],
        ["add-custom", "mock_b", "--base-url", base_b, "--format", "anthropic-messages",
         "--auth", "apiKey", "--models", "claude-mock"],
        ["add-custom", "mock_c", "--base-url", base_c, "--format", "openai-responses",
         "--auth", "apiKey", "--models", "resp-mock"],
        ["add-custom", "mock_leak", "--base-url", base_b, "--format", "anthropic-messages",
         "--auth", "apiKey", "--models", "claude-mock"],
    ):
        code, out, err = run(project, args, env)
        check(f"configure {args[1]}", code == 0, out + err)
    run(project, ["key", "set", "mock_b", "--from-env", "TEST_KEY"], env)
    run(project, ["key", "set", "mock_c", "--from-env", "RESP_KEY"], env)
    run(project, ["key", "set", "mock_leak", "--from-env", "LEAK_KEY"], env)
    run(project, ["chains", "set", "r429", "--models", "mock_a/mock-429,mock_a/mock-ok"], env)
    run(project, ["chains", "set", "bad400", "--models", "mock_a/mock-400,mock_a/mock-ok"], env)
    config_path = os.path.join(project, "_work", "models", "config.json")
    _patch_config(config_path, lambda cfg: cfg["providers"]["mock_a"].__setitem__(
        "overrides", {model: {"family": "mocklab"} for model in
                      ("mock-ok", "mock-429", "mock-400", "mock-flaky", "mock-500")}))

    code, out, err = run(project, ["list", "--json"], env)
    ids = [entry["id"] for entry in json.loads(out)] if code == 0 else []
    check("catalog carries the protected lanes",
          "codex:auto" in ids and "dsh:auto" in ids, out + err)
    check("catalog carries configured providers", "mock_a/mock-ok" in ids, out + err)

    code, out, err = run(project, ["discover", "mock_a"], env)
    state_file = os.path.join(project, "_work", "models", "state.json")
    state = json.load(open(state_file, encoding="utf-8")) if os.path.isfile(state_file) else {}
    check("discover records a live listing",
          code == 0 and state.get("discovered", {}).get("mock_a", {}).get("ids")
          == ["mock-ok", "mock-429", "mock-400", "mock-flaky", "mock-500"], out + err)

    code, out, err = run(project, ["key", "status", "mock_b", "--json"], env)
    status = json.loads(out) if code == 0 else {}
    check("key status is masked", status.get("hasKey") is True
          and status.get("fingerprint") == "…1234"
          and "test-key-1234" not in out, out + err)

    code, out, err = run(project, ["route", "--role", "review", "--no-quota", "--json"], env)
    plan = json.loads(out) if code == 0 else {}
    check("review defaults to the codex lane",
          code == 0 and plan.get("primary", {}).get("id") == "codex:auto"
          and plan["primary"]["family"] == "openai", out + err)

    code, out, err = run(project, ["route", "--role", "review", "--producer", "codex:auto",
                                   "--no-quota", "--json"], env)
    plan = json.loads(out) if code == 0 else {}
    check("review excludes the producer's family",
          code == 0 and plan.get("primary", {}).get("family") != "openai", out + err)

    code, out, err = run(project, ["route", "--role", "vision", "--no-quota", "--json"], env)
    plan = json.loads(out) if code == 0 else {}
    check("vision routes to a model that declares vision",
          code == 0 and plan["primary"]["id"] == "codex:auto", out + err)

    code, out, err = run(project, ["use", "review", "mock_a/mock-ok"], env)
    code2, out2, err2 = run(project, ["route", "--role", "review", "--no-quota", "--json"], env)
    plan = json.loads(out2) if code2 == 0 else {}
    check("an explicit role switch wins over the protected default",
          code == 0 and code2 == 0
          and plan.get("primary", {}).get("id") == "mock_a/mock-ok", out + err + out2 + err2)
    run(project, ["use", "review", "auto"], env)

    code, out, err = run(project, ["call", "--role", "probe", "--model", "mock_a/mock-ok",
                                   "--prompt", "hi", "--json"], env)
    result = json.loads(out) if code == 0 else {}
    check("a keyless completion succeeds",
          code == 0 and "hello from mock A" in result.get("text", ""), out + err)
    calls_path = os.path.join(project, "_work", "models", "model_calls.jsonl")
    check("every attempt is logged", os.path.isfile(calls_path)
          and "mock_a/mock-ok" in open(calls_path, encoding="utf-8").read())

    code, out, err = run(project, ["call", "--role", "probe", "--chain", "r429", "--prompt", "hi", "--json"], env)
    result = json.loads(out) if code == 0 else {}
    attempts = result.get("attempts") or []
    check("a 429 falls over to the next chain link",
          code == 0 and "hello from mock A" in result.get("text", "")
          and attempts and attempts[0].get("kind") == "rate_limit", out + err)

    code, out, err = run(project, ["call", "--role", "probe", "--chain", "bad400", "--prompt", "hi", "--json"], env)
    check("a 400 is a request defect and does not fall over", code == 4, out + err)

    # Round-5: a named chain is executed by a script, so a dsh link is dropped at
    # plan time rather than silently skipped at call time.
    run(project, ["chains", "set", "withdsh", "--models",
                  "dsh:auto,mock_a/mock-ok"], env)
    code, out, err = run(project, ["chains", "show", "withdsh", "--no-quota", "--json"], env)
    plan = json.loads(out) if code == 0 else {}
    check("a chain link that is the lead session is dropped with a reason",
          code == 0
          and [link["id"] for link in plan.get("links", [])] == ["mock_a/mock-ok"]
          and any(row["id"] == "dsh:auto" and "lead session" in row["reason"]
                  for row in plan.get("dropped", [])), out + err)

    config_path = os.path.join(project, "_work", "models", "config.json")
    config = json.load(open(config_path, encoding="utf-8"))
    config["defaults"]["retryTransient"] = 2
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)
    code, out, err = run(project, ["call", "--role", "probe", "--model", "mock_a/mock-flaky",
                                   "--prompt", "hi", "--json"], env)
    result = json.loads(out) if code == 0 else {}
    attempts = result.get("attempts") or []
    check("a transient 500 is retried before the link is dropped",
          code == 0 and len(attempts) == 3
          and "flaky recovered" in result.get("text", ""), out + err)

    code, out, err = run(project, ["call", "--role", "probe", "--model", "mock_b/claude-mock",
                                   "--prompt", "hi", "--json"], env)
    result = json.loads(out) if code == 0 else {}
    check("the Anthropic Messages wire works",
          code == 0 and result.get("text") == "anthropic ok", out + err)

    code, out, err = run(project, ["call", "--role", "probe", "--model", "mock_c/resp-mock",
                                   "--prompt", "hi", "--json"], env)
    result = json.loads(out) if code == 0 else {}
    check("the OpenAI Responses wire works",
          code == 0 and result.get("text") == "responses ok", out + err)

    code, out, err = run(project, ["call", "--role", "probe", "--model", "mock_leak/claude-mock",
                                   "--prompt", "hi"], env)
    state_text = open(os.path.join(project, "_work", "models", "state.json"),
                      encoding="utf-8").read()
    check("an echoed credential is redacted everywhere",
          code == 5 and "sk-leak-SECRET-9999" not in (out + err + state_text),
          out + err)

    code, out, err = run(project, ["call", "--role", "probe", "--model", "dsh:auto", "--prompt", "hi"], env)
    check("the dsh lane refuses a script call",
          code == 2 and "lead session" in (out + err), out + err)

    code, out, err = run(project, ["call", "--model", "codex:auto", "--prompt", "hi",
                                   "--dry-run"], env)
    check("the codex lane delegates to run_gpt.py",
          code == 0 and "run_gpt.py" in out and "--prompt-file" in out, out + err)

    config = json.load(open(config_path, encoding="utf-8"))
    config["providers"]["mock_c"]["limits"] = {"callsPerDay": 1}
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)
    code, out, err = run(project, ["call", "--role", "probe", "--model", "mock_c/resp-mock",
                                   "--prompt", "hi"], env)
    check("a declared daily budget stops the next call", code == 3, out + err)

    code, out, err = run(other, ["list", "--json"], env)
    check("another project does not inherit providers",
          code == 0 and "mock_a/mock-ok" not in out, out + err)

    code, out, err = run(project, ["providers", "--search", "deepseek", "--json"], env)
    check("the vendored registry is searchable",
          code == 0 and any(row["id"] == "deepseek" for row in json.loads(out)), out + err)

    fresh = os.path.join(other, "_work", "models")
    os.makedirs(fresh, exist_ok=True)
    with open(os.path.join(fresh, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "lanes": {"codex": {"enabled": False}}}, fh)
    code, out, err = run(other, ["doctor"], env)
    check("doctor passes on a fresh project", code == 0 and "verdict   ok" in out, out + err)

    # Unit-level checks that do not need a subprocess.
    sys.path.insert(0, HERE)
    import model_config as cfg_mod  # noqa: PLC0415
    import model_http  # noqa: PLC0415
    import model_registry as registry  # noqa: PLC0415
    check("registry splits on the first slash only",
          registry.parse("nvidia_nim/nvidia/nemotron-3-super-120b-a12b")
          == {"provider": "nvidia_nim", "model": "nvidia/nemotron-3-super-120b-a12b"})
    check("lane ids are not provider ids",
          registry.parse("codex:gpt-5.6-sol") is None
          and registry.is_lane_id("codex:gpt-5.6-sol"))
    check("a full chatUrl overrides the composed completion path",
          registry.chat_url({"format": "openai-chat",
                             "baseUrl": "https://text.pollinations.ai",
                             "chatUrl": "https://text.pollinations.ai/openai"})
          == "https://text.pollinations.ai/openai")
    check("a chatPath is appended to the base URL",
          registry.chat_url({"format": "openai-chat", "baseUrl": "http://x:1/v1",
                             "chatPath": "/openai"}) == "http://x:1/v1/openai")
    kind, scope, retryable, cooldown, _ = model_http.classify(400, "invalid request")
    check("an unclassified 400 is request-scoped", (kind, scope, retryable, cooldown)
          == ("invalid_request", "request", False, 0))
    kind, _, retryable, cooldown, _ = model_http.classify(429, "rate limit", 7)
    check("a 429 honours retry-after", kind == "rate_limit" and retryable and cooldown == 7)
    check("redaction removes a secret",
          cfg_mod.redact("key sk-abc-12345 rejected", ["sk-abc-12345"])
          == "key [redacted] rejected")
    check("fingerprint never reveals a short secret",
          cfg_mod.fingerprint("abcd") == "…****" and cfg_mod.fingerprint("sk-live-abcd")
          != "sk-live-abcd")


def extended_cases(env, project, port_a, port_b, port_c):
    """Regression cases for the round-1 review findings (R1-*)."""
    base_a = f"http://127.0.0.1:{port_a}/v1"
    base_b = f"http://127.0.0.1:{port_b}/v1"
    base_c = f"http://127.0.0.1:{port_c}/v1"
    config_path = os.path.join(project, "_work", "models", "config.json")
    marker = os.path.join(project, "_work", "credential-ran.txt")

    for args in (
        ["add-custom", "mock_404", "--base-url", f"http://127.0.0.1:{port_a}/wrong",
         "--models", "x", "--auth", "none"],
        ["add-custom", "mock_cmd", "--base-url", base_a, "--models", "mock-ok",
         "--auth", "none"],
        ["add-custom", "mock_cmd_fail", "--base-url", base_a, "--models", "mock-ok",
         "--auth", "apiKey"],
        ["add-custom", "mock_list_leak", "--base-url", base_a, "--models", "mock-ok",
         "--auth", "apiKey", "--models-url", f"{base_a}/leak/models"],
        ["add-custom", "agg_openai", "--base-url", base_a, "--models", "gpt-4o",
         "--auth", "none"],
        ["add-custom", "agg_openai_locked", "--base-url", base_a, "--models", "gpt-4o",
         "--auth", "none"],
        ["add-custom", "agg_unknown", "--base-url", base_a, "--models", "mystery-1",
         "--auth", "none"],
        ["add-custom", "agg_alias", "--base-url", base_a, "--models", "gpt-4o",
         "--auth", "none"],
        ["add-custom", "mock_nomodel", "--base-url", base_b, "--format",
         "anthropic-messages", "--auth", "apiKey", "--models", "claude-mock"],
        ["add-custom", "mock_list_echo", "--base-url", base_a, "--auth", "apiKey",
         "--models", "mock-ok", "--models-url", f"{base_a}/echo/models"],
        ["add-custom", "mock_echo", "--base-url", base_a, "--auth", "apiKey",
         "--models", "echo-key"],
        ["add-custom", "ds_nomodel", "--base-url", base_a, "--format",
         "openai-responses", "--auth", "none", "--models", "deepseek-chat"],
        ["add-custom", "gpt_nomodel", "--base-url", base_a, "--format",
         "openai-responses", "--auth", "none", "--models", "gpt-4o"],
    ):
        code, out, err = run(project, args, env)
        check(f"configure {args[1]}", code == 0, out + err)
    _patch_config(config_path, lambda cfg: cfg["providers"]["agg_openai"].__setitem__(
        "allowProtectedFamily", True))
    _patch_config(config_path, lambda cfg: cfg["providers"]["agg_alias"].__setitem__(
        "overrides", {"gpt-4o": {"family": "harmless"}}))
    run(project, ["key", "set", "mock_nomodel", "--from-env", "TEST_KEY"], env)
    run(project, ["key", "set", "mock_list_echo", "--from-env", "LEAK_KEY"], env)
    run(project, ["key", "set", "mock_echo", "--from-env", "LEAK_KEY"], env)
    # Verify the two aggregator rows first, so the routing assertions below are
    # about independence rather than about an untested provider.
    run(project, ["test", "agg_openai"], env)
    run(project, ["test", "agg_unknown"], env)

    _patch_config(config_path, lambda cfg: cfg["providers"]["mock_cmd"].__setitem__(
        "credential", {"source": "command", "argv": [
            sys.executable, "-c",
            f"open(r'{marker}','w').write('x');print('cmd-key-1234')"]}))
    _patch_config(config_path, lambda cfg: cfg["providers"]["mock_cmd_fail"].__setitem__(
        "credential", {"source": "command", "argv": [
            sys.executable, "-c",
            "import sys;sys.stderr.write('sk-command-LEAK-7777');sys.exit(1)"]}))
    run(project, ["key", "set", "mock_list_leak", "--from-env", "LEAK_KEY"], env)

    # R1-PROTECTED-BYPASS: a GPT-family row over HTTP is opt-in, never silent.
    code, out, err = run(project, ["list", "--json"], env)
    entries = {entry["id"]: entry for entry in json.loads(out)} if code == 0 else {}
    check("a GPT-family HTTP row is refused without allowProtectedFamily",
          entries.get("agg_openai_locked/gpt-4o", {}).get("usable") is False
          and "protected family" in str(entries["agg_openai_locked/gpt-4o"].get("reason")),
          out + err)

    # R1-INDEPENDENCE: model id decides the family; unknown families cannot review.
    code, out, err = run(project, ["route", "--role", "review", "--producer", "codex:auto",
                                   "--no-quota", "--json"], env)
    plan = json.loads(out) if code == 0 else {}
    excluded = {row["id"]: row["reason"] for row in plan.get("excluded", [])}
    check("an aggregator GPT row is excluded as a same-family reviewer",
          code == 0 and "agg_openai/gpt-4o" in excluded
          and "same family" in excluded["agg_openai/gpt-4o"], out + err)
    check("an unknown-family row is excluded from review",
          "agg_unknown/mystery-1" in excluded
          and "family unknown" in excluded["agg_unknown/mystery-1"], out + err)

    # Round-2/4: "unverified" and "protected family" cannot be bypassed by a direct
    # id; every POST (including /v1/responses) is counted, not just chat calls.
    before = MockState.posts
    code, out, err = run(project, ["call", "--model", "agg_openai_locked/gpt-4o",
                                   "--prompt", "hi"], env)
    check("a direct call to a protected-family row is refused",
          code == 2 and MockState.posts == before, out + err)
    code, out, err = run(project, ["test", "agg_openai_locked/gpt-4o", "--generate"], env)
    check("test --generate refuses a protected-family row",
          code == 2 and MockState.posts == before, out + err)
    code, out, err = run(project, ["test", "ds_nomodel", "--generate"], env)
    check("a provider-target generation cannot bypass the DeepSeek protection",
          code == 2 and MockState.posts == before, out + err)
    code, out, err = run(project, ["test", "gpt_nomodel", "--generate"], env)
    check("a provider-target generation cannot bypass the GPT protection",
          code == 2 and MockState.posts == before, out + err)
    code, out, err = run(project, ["list", "--json"], env)
    entries = {entry["id"]: entry for entry in json.loads(out)} if code == 0 else {}
    check("a family override cannot make a GPT row callable",
          entries.get("agg_alias/gpt-4o", {}).get("usable") is False
          and entries["agg_alias/gpt-4o"].get("protectedFamily") is True, out + err)

    # Round-2: call --role must not crash (the route-only flag was missing).
    state_path = os.path.join(project, "_work", "models", "state.json")
    state = json.load(open(state_path, encoding="utf-8"))
    from datetime import datetime, timezone
    state.setdefault("quota", {})["codex"] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data": {"tier": "S", "remaining": 90.0, "weekly_remaining": 90.0,
                 "hours_left": 3.0, "source": "test"}}
    json.dump(state, open(state_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    code, out, err = run(project, ["call", "--role", "produce", "--prompt", "hi",
                                   "--dry-run"], env)
    check("call --role produce reaches the planner without a traceback",
          code == 0 and "dry-run" in out.lower(), out + err)
    code, out, err = run(project, ["call", "--role", "lead", "--prompt", "hi",
                                   "--dry-run"], env)
    check("call --role lead refuses instead of substituting another model",
          code == 2 and "lead session" in (out + err), out + err)
    code, out, err = run(project, ["use", "lead", "codex:auto"], env)
    check("the lead role cannot be switched to codex", code == 2, out + err)
    config_path = os.path.join(project, "_work", "models", "config.json")
    _patch_config(config_path, lambda cfg: cfg.setdefault("roles", {}).setdefault(
        "lead", {}) .__setitem__("model", "codex:auto"))
    code, out, err = run(project, ["route", "--role", "lead", "--no-quota", "--json"], env)
    plan = json.loads(out) if code == 0 else {}
    check("the lead route is dsh even if the config says codex",
          code == 0 and plan.get("primary", {}).get("id") == "dsh:auto", out + err)
    _patch_config(config_path, lambda cfg: cfg.setdefault("lanes", {}).setdefault(
        "dsh", {}).__setitem__("enabled", False))
    code, out, err = run(project, ["route", "--role", "lead", "--no-quota"], env)
    check("a disabled dsh lane fails instead of substituting a model", code == 3, out + err)
    _patch_config(config_path, lambda cfg: cfg.setdefault("lanes", {}).setdefault(
        "dsh", {}).__setitem__("enabled", True))
    state_path = os.path.join(project, "_work", "models", "state.json")
    state = json.load(open(state_path, encoding="utf-8"))
    from datetime import datetime, timezone
    state.setdefault("quota", {})["codex"] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data": {"tier": "R", "remaining": 5.0, "weekly_remaining": 50.0,
                 "hours_left": 3.0, "source": "test"}}
    json.dump(state, open(state_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    code, out, err = run(project, ["call", "--role", "produce", "--prompt", "hi",
                                   "--no-quota", "--dry-run"], env)
    check("call --role produce refuses when the tier gives production to the lead",
          code == 2 and "quota tier" in (out + err), out + err)
    state = json.load(open(state_path, encoding="utf-8"))
    state.pop("quota", None)
    json.dump(state, open(state_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    code, out, err = run(project, ["route", "--role", "lead", "--no-quota", "--json"], env)
    lead_plan = json.loads(out) if code == 0 else {}
    check("the lead role has no script-side fallback",
          code == 0 and lead_plan.get("fallback") == [], out + err)
    code, out, err = run(project, ["route", "--role", "review", "--no-quota", "--json"], env)
    review_plan = json.loads(out) if code == 0 else {}
    check("the review fallback never lists the lead session",
          code == 0 and all(entry["lane"] != "dsh"
                            for entry in review_plan.get("fallback", [])), out + err)

    # Round-2: reasoning need is enforced, including for an exact pin.
    _patch_config(config_path, lambda cfg: cfg["providers"]["mock_a"]["overrides"]
                  ["mock-ok"].__setitem__("reasoning", False))
    code, out, err = run(project, ["route", "--role", "review", "--pin", "mock_a/mock-ok",
                                   "--needs", "reasoning", "--no-quota"], env)
    check("a pinned model without the requested reasoning capability is refused",
          code == 3, out + err)
    _patch_config(config_path, lambda cfg: cfg["providers"]["mock_a"]["overrides"]
                  ["mock-ok"].__setitem__("reasoning", True))

    # Round-2: provider-controlled successful content is tainted as well.
    code, out, err = run(project, ["discover", "mock_list_echo"], env)
    state_text = open(os.path.join(project, "_work", "models", "state.json"),
                      encoding="utf-8").read()
    check("a listing that echoes the key as a model id is redacted",
          code == 0 and "[redacted]" in out
          and "sk-leak-SECRET-9999" not in (out + err + state_text), out + err)
    code, out, err = run(project, ["call", "--role", "probe", "--model", "mock_echo/echo-key",
                                   "--prompt", "hi"], env)
    check("a successful answer that echoes the key is redacted",
          code == 0 and "sk-leak-SECRET-9999" not in (out + err), out + err)

    # Round-2: a successful --generate is a success, not the listing's failure.
    code, out, err = run(project, ["test", "mock_nomodel", "--generate"], env)
    check("a generation probe that answers reports success", code == 0, out + err)

    # Round-2: health scope, nested transactions and ambiguous 404s.
    sys.path.insert(0, HERE)
    import model_config as cfg_mod  # noqa: PLC0415
    import model_http  # noqa: PLC0415
    import model_registry as registry  # noqa: PLC0415
    state = cfg_mod.load_state(project)
    cfg_mod.mark_failure(state, "race", "auth", 21600, "bad key")
    cfg_mod.mark_ok(state, "race", "sibling")
    check("a sibling success cannot erase a provider auth cooldown",
          cfg_mod.cooling(state, "race") > 0 and
          cfg_mod.provider_health(state, "race")["status"] != "ok")
    try:
        with cfg_mod.project_lock(project):
            cfg_mod.mutate(project, lambda current: None)
        nested = False
    except cfg_mod.ConfigError:
        nested = True
    check("a nested state transaction is rejected instead of deadlocking", nested)

    ambiguous = ["Not Found", "route not found", "model service endpoint missing",
                 "model service endpoint not available"]
    misread = [(body, model_http.classify(404, body)[:2]) for body in ambiguous]
    check("ambiguous 404 bodies stay endpoint-scoped",
          all(pair[1] == ("endpoint_missing", "request") for pair in misread), misread)
    check("a 404 naming a model is model_missing",
          model_http.classify(404, "model 'x' does not exist")[:2]
          == ("model_missing", "model"))
    check("a reversed 404 naming a model is model_missing",
          model_http.classify(404, "not found: model x")[:2]
          == ("model_missing", "model"))
    check("a structured model_not_found code is model_missing",
          model_http.classify(404, '{"error":{"code":"model_not_found"}}')[:2]
          == ("model_missing", "model"))
    check("a structured endpoint code stays endpoint-scoped",
          model_http.classify(
              404, '{"error":{"code":"model_service_endpoint_not_available"}}')[:2]
          == ("endpoint_missing", "request"))
    check("a model named before endpoint prose is still model_missing",
          model_http.classify(404, "model x not found at this endpoint")[:2]
          == ("model_missing", "model"))
    check("a model named before service prose is still model_missing",
          model_http.classify(404, "model x does not exist on this service")[:2]
          == ("model_missing", "model"))

    # Round-2: family inference must not certify a lab it cannot prove.
    cases = {
        "gpt-j-6b": ("eleutherai", True),
        "o1labs-7b": (None, False),
        "commander-7b": (None, False),
        "novalite-1": (None, False),
        "openai/gpt-4o": ("openai", True),
        "deepseek-v4-pro": ("deepseek", True),
    }
    wrong = []
    for model_id, (family, known) in cases.items():
        info = registry.family_info("some_gateway", {"name": "Some Gateway"}, model_id)
        if family is None:
            if info["known"]:
                wrong.append((model_id, info))
        elif (info["family"], info["known"]) != (family, known):
            wrong.append((model_id, info))
    check("family inference is boundary-aware", not wrong, wrong)
    together = registry.family_info("together", {"name": "Together AI"}, "private-deploy")
    check("a multi-family aggregator without a recognisable model stays unknown",
          together["known"] is False)
    named = registry.family_info("qwencloud_coding",
                                 {"name": "QwenCloud Coding Plan"}, "private-deploy")
    check("a provider display name cannot certify a family",
          named["known"] is False)
    gateway = registry.family_info("custom_gateway",
                                   {"name": "OpenAI-compatible gateway"}, "private-deploy")
    check("an OpenAI-compatible gateway name cannot certify a family",
          gateway["known"] is False)

    # R1-PIN: an exact pin never silently becomes another model.
    code, out, err = run(project, ["route", "--role", "review", "--pin", "nope/nope",
                                   "--no-quota"], env)
    check("an unknown pin fails instead of substituting another model",
          code == 3 and "not in this project's catalog" in (out + err), out + err)
    code, out, err = run(project, ["route", "--role", "review", "--pin",
                                   "agg_openai_locked/gpt-4o", "--no-quota"], env)
    check("a pinned protected-family row is refused", code == 3, out + err)

    # R1-MODEL-COOLDOWN: a 429 cools that model only, and route sees it.
    run(project, ["call", "--role", "probe", "--chain", "r429", "--prompt", "hi"], env)
    code, out, err = run(project, ["list", "--json"], env)
    entries = {entry["id"]: entry for entry in json.loads(out)} if code == 0 else {}
    check("a model-level cooldown marks the model unusable",
          entries.get("mock_a/mock-429", {}).get("usable") is False
          and "cooling" in str(entries["mock_a/mock-429"].get("reason")), out + err)
    check("the sibling model stays usable after a model-scoped 429",
          entries.get("mock_a/mock-ok", {}).get("usable") is True, out + err)

    # R1-HTTP-404: a wrong endpoint is a request defect, not a model miss.
    code, out, err = run(project, ["call", "--role", "probe", "--model", "mock_404/x", "--prompt", "hi",
                                   "--json"], env)
    result = json.loads(out) if out.strip().startswith("{") else {}
    check("an endpoint 404 is request-scoped and does not fall back",
          code == 4 and (result.get("status") == "request-defect"
                         or "endpoint" in (out + err)), out + err)

    # R1-COMMAND-EXEC: read-only views never run a credential command.
    if os.path.isfile(marker):
        os.remove(marker)
    run(project, ["list"], env)
    check("list does not execute a credential command", not os.path.isfile(marker))
    code, out, err = run(project, ["call", "--role", "probe", "--model", "mock_cmd/mock-ok",
                                   "--prompt", "hi"], env)
    check("a call mints the command credential and succeeds",
          code == 0 and os.path.isfile(marker), out + err)

    # R1-CREDENTIAL-LEAK: a failing login helper must not spill its stderr.
    code, out, err = run(project, ["test", "mock_cmd_fail"], env)
    check("a failing credential command withholds its stderr",
          code == 2 and "sk-command-LEAK-7777" not in (out + err)
          and "withheld" in (out + err), out + err)
    code, out, err = run(project, ["key", "status", "mock_cmd_fail", "--json"], env)
    status = json.loads(out) if code == 0 else {}
    check("a declared command credential reports as declared, not minted",
          status.get("hasKey") is True and status.get("minted") is False
          and status.get("declared") is True, out + err)

    code, out, err = run(project, ["discover", "mock_list_leak"], env)
    state_text = open(os.path.join(project, "_work", "models", "state.json"),
                      encoding="utf-8").read()
    check("a listing that echoes a key is redacted in output and state",
          code == 1 and "sk-leak-SECRET-9999" not in (out + err + state_text), out + err)

    # R1-RETRY-ZERO: zero retries means exactly one attempt.
    _patch_config(config_path, lambda cfg: cfg["defaults"].__setitem__("retryTransient", 0))
    code, out, err = run(project, ["call", "--role", "probe", "--model", "mock_a/mock-500", "--prompt", "hi",
                                   "--json"], env)
    result = json.loads(out) if out.strip().startswith("{") else {}
    check("retryTransient=0 performs one attempt",
          code == 5 and len(result.get("attempts") or []) == 1, out + err)
    _patch_config(config_path, lambda cfg: cfg["defaults"].__setitem__("retryTransient", 2))

    # R1-CODEX-PATHS: the runner is pinned to this project's records and ledger.
    code, out, err = run(project, ["call", "--model", "codex:auto", "--prompt", "hi",
                                   "--dry-run"], env)
    check("the codex runner writes into the selected project",
          code == 0 and "--calls-dir" in out and "--ledger" in out
          and os.path.join(project, "_work", "quota_ledger.md") in out, out + err)

    # Round-2: exercise the delegation itself with a fake runner.
    fake_dir = os.path.join(project, "_work", "fake")
    os.makedirs(fake_dir, exist_ok=True)
    fake = os.path.join(fake_dir, "fake_runner.py")
    record_path = os.path.join(fake_dir, "record.json")
    with open(fake, "w", encoding="utf-8") as fh:
        fh.write("import json,os,sys\n"
                 "open(os.environ['FAKE_RECORD'],'w',encoding='utf-8').write("
                 "json.dumps({'cwd':os.getcwd(),'argv':sys.argv[1:]}))\n"
                 "print(json.dumps({'final_message':'fake ok','usage':"
                 "{'input_tokens':1,'output_tokens':1}}))\n")
    env_fake = dict(env, COORD_RUN_GPT=fake, FAKE_RECORD=record_path)
    code, out, err = run(project, ["call", "--role", "probe", "--model", "codex:auto", "--prompt", "hi",
                                   "--json"], env_fake)
    result = json.loads(out) if out.strip().startswith("{") else {}
    observed = json.load(open(record_path, encoding="utf-8")) \
        if os.path.isfile(record_path) else {}
    check("the codex delegation runs inside the selected project",
          code == 0 and result.get("text") == "fake ok"
          and observed.get("cwd") == project, out + err + str(observed))
    empty = os.path.join(fake_dir, "empty_runner.py")
    with open(empty, "w", encoding="utf-8") as fh:
        fh.write("import sys\nsys.exit(0)\n")
    code, out, err = run(project, ["call", "--role", "probe", "--model", "codex:auto", "--prompt", "hi"],
                         dict(env, COORD_RUN_GPT=empty))
    check("an empty codex payload is a failed attempt, not a success", code == 5,
          out + err)
    log_path = os.path.join(project, "_work", "models", "model_changes.log")
    check("model changes are audited",
          os.path.isfile(log_path) and "add-custom" in open(log_path,
                                                            encoding="utf-8").read())

    # Round-5: rotating a credential clears the old auth cooldown, or the fixed
    # key would stay unusable for hours.
    run(project, ["add-custom", "rot", "--base-url", base_b, "--format",
                  "anthropic-messages", "--auth", "apiKey", "--models", "claude-mock"], env)
    run(project, ["key", "set", "rot", "--from-env", "LEAK_KEY"], env)
    code_bad, _, _ = run(project, ["call", "--role", "probe", "--model", "rot/claude-mock",
                                   "--prompt", "hi"], env)
    run(project, ["key", "set", "rot", "--from-env", "TEST_KEY"], env)
    code_good, out_good, err_good = run(project, ["call", "--role", "probe", "--model", "rot/claude-mock",
                                                  "--prompt", "hi", "--json"], env)
    check("rotating a credential clears the auth cooldown",
          code_bad != 0 and code_good == 0
          and json.loads(out_good).get("text") == "anthropic ok", out_good + err_good)
    # outcome and clears the stored token on logout.
    account_cli = os.path.join(fake_dir, "fake_account.py")
    account_log = os.path.join(fake_dir, "account.log")
    with open(account_cli, "w", encoding="utf-8") as fh:
        fh.write("import os,sys\n"
                 "open(os.environ['ACCT_LOG'],'a',encoding='utf-8').write(sys.argv[1]+'\\n')\n"
                 "if sys.argv[1]=='whoami':\n"
                 "    print('account: demo-user'); sys.exit(0)\n"
                 "if sys.argv[1] in ('login','logout'):\n"
                 "    print(sys.argv[1]+' ok'); sys.exit(0)\n"
                 "sys.exit(2)\n")
    run(project, ["add-custom", "acct_mock", "--base-url", base_a,
                  "--models", "mock-ok", "--auth", "none"], env)
    _patch_config(config_path, lambda cfg: cfg["providers"]["acct_mock"].__setitem__(
        "account", {
            "loginArgv": [sys.executable, account_cli, "login"],
            "logoutArgv": [sys.executable, account_cli, "logout"],
            "statusArgv": [sys.executable, account_cli, "whoami"],
        }))
    env_acct = dict(env, ACCT_LOG=account_log)
    code, out, err = run(project, ["account", "login", "acct_mock"], env_acct)
    check("account login runs the vendor CLI and records the session",
          code == 0 and os.path.isfile(account_log), out + err)
    code, out, err = run(project, ["account", "status", "acct_mock"], env_acct)
    check("account status reports the logged-in account", code == 0 and "demo-user" in out,
          out + err)
    code, out, err = run(project, ["account", "logout", "acct_mock"], env_acct)
    logged = open(account_log, encoding="utf-8").read() if os.path.isfile(account_log) else ""
    check("account logout runs the vendor CLI and records the exit",
          code == 0 and "logout" in logged, out + err)
    code, out, err = run(project, ["account", "login", "acct_mock"], env_acct)
    check("account login works again after logout (re-login)", code == 0, out + err)
    code, out, err = run(project, ["account", "list", "--json"], env_acct)
    rows = json.loads(out) if code == 0 else []
    check("account list shows the configured account",
          code == 0 and any(row[0] == "acct_mock" for row in rows), out + err)

    # R1-CONCURRENCY: five processes, five counted calls, no lost update.
    script = ("import sys;sys.path.insert(0,'scripts');import model_config as m;"
              "m.mutate(sys.argv[1], lambda s: m.bump_usage(s,'conc',calls=1))")
    procs = [subprocess.Popen([sys.executable, "-c", script, project], cwd=SKILL,
                              env=env) for _ in range(5)]
    codes = [proc.wait() for proc in procs]
    state = json.load(open(os.path.join(project, "_work", "models", "state.json"),
                           encoding="utf-8"))
    total = sum(day.get("conc", {}).get("calls", 0)
                for day in (state.get("usage") or {}).values())
    check("concurrent state updates are serialised", codes == [0] * 5 and total == 5,
          f"codes={codes} calls={total}")

    # Round-3: concurrent round-robin planners must not both pick the same head.
    run(project, ["chains", "set", "rr", "--models",
                  "mock_a/mock-ok,mock_b/claude-mock", "--strategy", "round-robin"], env)
    rotation_script = (
        "import sys;sys.path.insert(0,'scripts');"
        "import model_config as c, model_route as r;"
        "cfg,_=c.load_config(sys.argv[1]);st=c.load_state(sys.argv[1]);"
        "plan=r.plan_chain('rr',cfg,st,sys.argv[1],read_quota=False);"
        "print(plan['links'][0]['id'])")
    procs = [subprocess.Popen([sys.executable, "-c", rotation_script, project],
                              cwd=SKILL, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, encoding="utf-8")
             for _ in range(6)]
    heads = []
    for proc in procs:
        stdout, stderr = proc.communicate(timeout=120)
        heads.append(stdout.strip())
        if proc.returncode != 0:
            heads.append(f"exit:{proc.returncode}:{(stderr or '').strip()[-120:]}")
    check("concurrent round-robin planners rotate through both links",
          len(heads) == 6 and set(heads) == {"mock_a/mock-ok", "mock_b/claude-mock"}
          and heads.count("mock_a/mock-ok") == 3
          and heads.count("mock_b/claude-mock") == 3, heads)

    # Round-4: the invariant behind that test — the cursor is read only inside the
    # transaction. A stale read outside mutate() raises here and fails the case.
    import model_route as route_mod  # noqa: PLC0415
    cfg_for_rotation, _ = cfg_mod.load_config(project)
    original_mutate = cfg_mod.mutate
    original_rotation = cfg_mod.rotation
    inside = {"value": False}

    def fake_mutate(project_arg, fn):
        def wrapped(state):
            inside["value"] = True
            try:
                fn(state)
            finally:
                inside["value"] = False
        return original_mutate(project_arg, wrapped)

    def guarded_rotation(state, key):
        if not inside["value"]:
            raise AssertionError("rotation read outside the transaction")
        return original_rotation(state, key)

    cfg_mod.mutate = fake_mutate
    cfg_mod.rotation = guarded_rotation
    guarded_ok = False
    try:
        plan = route_mod.plan_chain("rr", cfg_for_rotation,
                                    cfg_mod.load_state(project), project,
                                    read_quota=False)
        guarded_ok = bool(plan["links"])
    except AssertionError:
        guarded_ok = False
    finally:
        cfg_mod.mutate = original_mutate
        cfg_mod.rotation = original_rotation
    check("round-robin reads the cursor only inside the transaction", guarded_ok)

    code, out, err = run(project, ["doctor"], env)
    check("doctor passes on the configured project", code == 0, out + err)

    # Unit-level checks for the family grammar and 404 split.
    sys.path.insert(0, HERE)
    import model_http  # noqa: PLC0415
    import model_registry as registry  # noqa: PLC0415
    info = registry.family_info("tokenrouter", {"name": "TokenRouter"}, "openai/gpt-4o")
    check("a model id decides its own family",
          info["family"] == "openai" and info["known"] is True)
    info = registry.family_info("tokenrouter", {"name": "TokenRouter"}, "mystery-1")
    check("an unknown aggregator family is reported unknown", info["known"] is False)
    kind, scope, _, _, _ = model_http.classify(404, "no such path")
    check("a 404 with no model named is an endpoint defect",
          (kind, scope) == ("endpoint_missing", "request"))
    kind, scope, _, _, _ = model_http.classify(404, "model 'x' does not exist")
    check("a 404 that names a model is model_missing",
          (kind, scope) == ("model_missing", "model"))


def team_cases(env, work, port_a, port_b):
    """Round-6: scan candidates, ask the user, form the approved team."""
    project = os.path.join(work, "team-project")
    os.makedirs(project)
    base_a = f"http://127.0.0.1:{port_a}/v1"
    base_b = f"http://127.0.0.1:{port_b}/v1"
    config_path = os.path.join(project, "_work", "models", "config.json")
    team_path = os.path.join(project, "_work", "models", "team.json")
    state_path = os.path.join(project, "_work", "models", "state.json")

    def digest(path):
        return (hashlib.sha256(open(path, "rb").read()).hexdigest()
                if os.path.isfile(path) else None)
    run(project, ["init"], env)
    for args in (
        ["add-custom", "t_a", "--base-url", base_a, "--models",
         "mock-ok,mock-400", "--auth", "none"],
        ["add-custom", "t_b", "--base-url", base_b, "--format", "anthropic-messages",
         "--models", "claude-mock", "--auth", "apiKey"],
        ["add-custom", "t_stale", "--base-url", base_a, "--models", "mock-ok",
         "--auth", "none"],
        ["add-custom", "t_missing", "--base-url", base_a, "--models", "ghost",
         "--auth", "none"],
        ["add-custom", "t_prot", "--base-url", base_a, "--models", "gpt-4o",
         "--auth", "none"],
        ["add-custom", "t_chat", "--base-url", base_a, "--models", "mock-ok",
         "--auth", "none"],
    ):
        run(project, args, env)
    _patch_config(config_path, lambda cfg: cfg["providers"]["t_a"].__setitem__(
        "overrides", {"mock-ok": {"family": "teamlab"}, "mock-400": {"family": "teamlab"}}))
    _patch_config(config_path, lambda cfg: cfg["providers"]["t_b"].__setitem__(
        "overrides", {"claude-mock": {"family": "teambeta"}}))
    run(project, ["key", "set", "t_b", "--from-env", "TEST_KEY"], env)

    code, out, err = run(project, ["team", "scan", "--json", "--no-local"], env)
    scan = json.loads(out) if code == 0 else {}
    ids = [row["id"] for row in scan.get("candidates", [])]
    check("team scan lists configured candidates without joining them",
          code == 0 and "t_a/mock-ok" in ids and scan.get("team") is None, out + err)
    candidate = next((row for row in scan.get("candidates", [])
                      if row["id"] == "t_a/mock-ok"), {})
    check("team scan reports context and declared limits for the decision",
          bool(candidate.get("contextWindow")) and bool(candidate.get("maxOutput"))
          and "limits" in candidate, str(candidate))
    code, out, err = run(project, ["team", "scan", "--no-local"], env)
    check("the human scan table shows context and limits",
          code == 0 and "ctx" in out and "limits" in out, out + err)

    # A protected family offered by the environment cannot be configured into the team.
    env_team = dict(env, DEEPSEEK_API_KEY="sk-fake-deepseek")
    code, out, err = run(project, ["team", "scan", "--json", "--no-local"], env_team)
    scan_env = json.loads(out) if code == 0 else {}
    env_candidates = [row for row in scan_env.get("candidates", [])
                      if row["kind"] == "env" and row["id"].startswith("deepseek/")]
    if env_candidates:
        code, out, err = run(project, ["team", "add", env_candidates[0]["id"]], env_team)
        check("a protected env candidate is refused before configuration",
              code == 1 and "protected family" in (out + err)
              and "deepseek" not in (json.load(open(config_path, encoding="utf-8"))
                                     .get("providers") or {}), out + err)

    # A chatUrl-only change must invalidate the approval too.
    _patch_config(config_path, lambda cfg: cfg["providers"]["t_chat"].__setitem__(
        "chatUrl", base_b + "/chat/completions"))
    code, out, err = run(project, ["team", "add", "t_chat/mock-ok"], env)
    check("a chatUrl-only change invalidates the scan",
          code == 1 and "changed since the last scan" in (out + err), out + err)

    # A scan cache without a fingerprint is not an approval.
    scan_path = os.path.join(project, "_work", "models", "team_scan.json")
    scan_cache = json.load(open(scan_path, encoding="utf-8"))
    for row in scan_cache.get("candidates", []):
        if row.get("id") == "t_a/mock-ok":
            row.pop("fingerprint", None)
    json.dump(scan_cache, open(scan_path, "w", encoding="utf-8"), ensure_ascii=False)
    code, out, err = run(project, ["team", "add", "t_a/mock-ok"], env)
    check("a scan cache without a fingerprint is refused",
          code == 1 and "no usable fingerprint" in (out + err), out + err)
    run(project, ["team", "scan", "--no-local"], env)  # restore a valid cache

    # TEAM-STALE-SCAN-IDENTITY: approval is bound to the endpoint that was scanned.
    _patch_config(config_path, lambda cfg: cfg["providers"]["t_stale"].__setitem__(
        "baseUrl", base_b))
    code, out, err = run(project, ["team", "add", "t_stale/mock-ok"], env)
    check("a provider changed after the scan cannot join on the old approval",
          code == 1 and "changed since the last scan" in (out + err)
          and not os.path.isfile(os.path.join(project, "_work", "models", "team.json")),
          out + err)

    # TEAM-MODEL-NOT-VERIFIED / TEAM-PROTECTED-ADD-GAP.
    code, out, err = run(project, ["team", "add", "t_missing/ghost"], env)
    check("a model absent from the listing is refused",
          code == 1 and "not in the provider's listing" in (out + err), out + err)
    code, out, err = run(project, ["team", "add", "t_prot/gpt-4o"], env)
    check("a protected-family candidate cannot join the team",
          code == 1 and "protected family" in (out + err), out + err)

    code, out, err = run(project, ["team", "add", "t_a/mock-ok"], env)
    team_file = os.path.join(project, "_work", "models", "team.json")
    check("team add joins the approved candidate",
          code == 0 and os.path.isfile(team_file), out + err)
    code, out, err = run(project, ["list", "--json"], env)
    entries = {entry["id"]: entry for entry in json.loads(out)} if code == 0 else {}
    check("an unapproved provider is inactive once a team exists",
          entries.get("t_a/mock-ok", {}).get("usable") is True
          and entries.get("t_a/mock-400", {}).get("usable") is False
          and "not in this task's team" in str(entries["t_a/mock-400"].get("reason")),
          out + err)
    check("the approved candidate can serve as an independent reviewer",
          run(project, ["route", "--role", "review", "--producer", "dsh:auto",
                        "--no-quota", "--json"], env)[1].find('"t_a/mock-ok"') >= 0,
          out + err)

    # TEAM-GATE: every path, not just `list`, must honour the team.
    code, out, err = run(project, ["call", "--model", "t_a/mock-400", "--prompt", "hi"], env)
    check("call --model refuses an unapproved row", code == 2, out + err)
    code, out, err = run(project, ["test", "t_a/mock-400", "--generate"], env)
    check("test --generate refuses an unapproved row", code == 2, out + err)
    run(project, ["chains", "set", "teamchain", "--models",
                  "t_a/mock-400,t_a/mock-ok"], env)
    code, out, err = run(project, ["chains", "show", "teamchain", "--no-quota", "--json"], env)
    plan = json.loads(out) if code == 0 else {}
    check("a chain drops an unapproved link",
          code == 0 and [link["id"] for link in plan.get("links", [])] == ["t_a/mock-ok"]
          and any(row["id"] == "t_a/mock-400" for row in plan.get("dropped", [])),
          out + err)
    code, out, err = run(project, ["route", "--role", "review", "--pin", "t_a/mock-400",
                                   "--no-quota"], env)
    check("an exact pin to an unapproved row fails", code == 3, out + err)
    code, out, err = run(project, ["test", "t_b", "--generate"], env)
    check("a provider target with no approved model is not probed", code == 2, out + err)

    # A failed verification must leave both the team and the health state untouched.
    before_team, before_state = digest(team_path), digest(state_path)
    code, out, err = run(project, ["team", "add", "t_b/claude-mock"], env)
    check("a failed verification leaves the team and health unchanged",
          code == 1 and digest(team_path) == before_team
          and digest(state_path) == before_state, out + err)

    code, out, err = run(project, ["team", "decline", "t_a/mock-400"], env)
    check("team decline records the refusal", code == 0 and "declined" in out, out + err)
    code, out, err = run(project, ["team", "show", "--json"], env)
    team = json.loads(out) if code == 0 else {}
    check("team show lists added and declined",
          "t_a/mock-ok" in (team.get("added") or {})
          and "t_a/mock-400" in (team.get("declined") or {}), out + err)

    code, out, err = run(project, ["team", "add", "t_a/mock-400"], env)
    code2, out2, _ = run(project, ["team", "show", "--json"], env)
    team = json.loads(out2) if code2 == 0 else {}
    check("re-adding a declined candidate clears the refusal",
          code == 0 and "t_a/mock-400" in (team.get("added") or {})
          and "t_a/mock-400" not in (team.get("declined") or {}), out + err + out2)

    code, out, err = run(project, ["team", "add", "t_b/claude-mock"], env)
    check("a provider with no listing is refused without --allow-unverified", code == 1,
          out + err)
    code, out, err = run(project, ["team", "add", "t_b/claude-mock",
                                   "--allow-unverified"], env)
    check("an unverified provider can join explicitly", code == 0, out + err)

    code, out, err = run(project, ["team", "clear"], env)
    code2, out2, err2 = run(project, ["list", "--json"], env)
    entries = {entry["id"]: entry for entry in json.loads(out2)} if code2 == 0 else {}
    check("team clear restores the backward-compatible mode",
          code == 0 and entries.get("t_a/mock-400", {}).get("usable") is True,
          out + err + out2 + err2)

    code, out, err = run(project, ["team", "scan", "--detect", base_a, "--no-local",
                                   "--json"], env)
    scan = json.loads(out) if code == 0 else {}
    local = [row for row in scan.get("candidates", []) if row["kind"] == "local"
             and row["id"].endswith("/mock-ok")]
    check("team scan detects a local OpenAI-compatible service",
          code == 0 and local and local[0]["id"].startswith("local_"), out + err)
    if local:
        code, out, err = run(project, ["team", "add", local[0]["id"]], env)
        check("a detected local service can join after approval",
              code == 0 and os.path.isfile(team_file), out + err)

    # TEAM-EMPTY-FILE-BYPASS: an existing team document is an allowlist even empty.
    run(project, ["team", "clear"], env)
    with open(team_file, "w", encoding="utf-8") as fh:
        fh.write("{}")
    code, out, err = run(project, ["list", "--json"], env)
    entries = {entry["id"]: entry for entry in json.loads(out)} if code == 0 else {}
    check("an empty team file still closes the gate",
          entries.get("t_a/mock-ok", {}).get("usable") is False, out + err)
    code, out, err = run(project, ["call", "--model", "t_a/mock-ok", "--prompt", "hi"], env)
    check("an empty team file makes an unapproved call fail", code == 2, out + err)
    os.remove(team_file)


def plan_cases(env, work, port_a, port_b):
    """Planner is scored and separate from the dispatcher."""
    project = os.path.join(work, "plan-project")
    os.makedirs(project)
    base_a = f"http://127.0.0.1:{port_a}/v1"
    base_b = f"http://127.0.0.1:{port_b}/v1"
    config_path = os.path.join(project, "_work", "models", "config.json")
    requirements = os.path.join(project, "requirements.md")
    with open(requirements, "w", encoding="utf-8") as fh:
        fh.write("Add a --json flag to the team command and document it.\n")
    with open(os.path.join(project, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("# plan-project\n\nPLAN-CONTEXT-MARKER\n")
    run(project, ["init"], env)

    code, out, err = run(project, ["plan", "--requirements", requirements,
                                   "--planner", "dsh:auto", "--no-quota"], env)
    check("planning on the lead session is refused from a script",
          code == 2 and "lead session" in (out + err), out + err)
    code, out, err = run(project, ["call", "--role", "plan", "--prompt", "hi",
                                   "--dry-run"], env)
    check("call --role plan points at the plan command",
          code == 2 and "models.py plan" in (out + err), out + err)

    for args in (
        ["add-custom", "p_low", "--base-url", base_a, "--models", "mock-ok",
         "--auth", "none"],
        ["add-custom", "p_high", "--base-url", base_b, "--format", "anthropic-messages",
         "--models", "claude-mock", "--auth", "apiKey"],
        ["add-custom", "p_dead", "--base-url", base_a, "--models", "mock-ok",
         "--auth", "none"],
        ["add-custom", "p_400", "--base-url", base_a, "--models", "mock-400",
         "--auth", "none"],
    ):
        run(project, args, env)
    run(project, ["key", "set", "p_high", "--from-env", "TEST_KEY"], env)
    _patch_config(config_path, lambda cfg: cfg.__setitem__("lanes", {
        "codex": {"enabled": False}, "dsh": {"enabled": False}}))
    _patch_config(config_path, lambda cfg: cfg["providers"]["p_low"].__setitem__(
        "overrides", {"mock-ok": {"family": "planlab",
                                  "scores": {"reasoning": 1, "design": 1,
                                             "agentic": 1, "coding": 1}}}))
    _patch_config(config_path, lambda cfg: cfg["providers"]["p_high"].__setitem__(
        "overrides", {"claude-mock": {"family": "planbeta",
                                      "scores": {"reasoning": 5, "design": 5,
                                                 "agentic": 5, "coding": 5}}}))
    _patch_config(config_path, lambda cfg: cfg["providers"]["p_dead"].__setitem__(
        "overrides", {"mock-ok": {"family": "plandead",
                                  "scores": {"reasoning": 4, "design": 4,
                                             "agentic": 4, "coding": 4}}}))

    code, out, err = run(project, ["route", "--role", "plan", "--no-quota", "--json"], env)
    plan = json.loads(out) if code == 0 else {}
    primary = plan.get("primary") or {}
    check("the planner is the highest scored model",
          code == 0 and primary.get("id") == "p_high/claude-mock"
          and primary.get("planning", {}).get("weighted") == 5.0, out + err)
    check("planning scores report their source",
          primary.get("planning", {}).get("source") == "config", out + err)

    code, out, err = run(project, ["plan", "--requirements", requirements,
                                   "--dry-run", "--no-quota"], env)
    check("plan dry-run picks the scored planner without calling",
          code == 0 and "p_high/claude-mock" in out and "POST" in out, out + err)
    code, out, err = run(project, ["plan", "--requirements", requirements,
                                   "--planner", "p_low/mock-ok", "--dry-run",
                                   "--no-quota"], env)
    check("an explicit planner overrides the score",
          code == 0 and "p_low/mock-ok" in out, out + err)

    code, out, err = run(project, ["plan", "--requirements", requirements,
                                   "--planner", "p_high/claude-mock", "--max-tokens",
                                   "64", "--no-quota", "--json"], env)
    record = json.loads(out) if out.strip().startswith("{") else {}
    plan_file = os.path.join(project, "_work", "plan", "plan.md")
    check("the planning call writes the plan and a planner record",
          code == 0 and os.path.isfile(plan_file)
          and "anthropic ok" in open(plan_file, encoding="utf-8").read()
          and record.get("planner") == "p_high/claude-mock"
          and record.get("scores", {}).get("weighted") == 5.0, out + err)
    brief_text = open(record.get("request"), encoding="utf-8").read() \
        if record.get("request") else ""
    check("the planning brief carries a repository snapshot",
          "PLAN-CONTEXT-MARKER" in brief_text and "README.md" in brief_text,
          brief_text[:200])

    # Dry-run must not read quota or write state/plan output.
    state_path = os.path.join(project, "_work", "models", "state.json")
    before_state = hashlib.sha256(open(state_path, "rb").read()).hexdigest()
    before_plan = os.path.getmtime(plan_file)
    code, out, err = run(project, ["plan", "--requirements", requirements,
                                   "--dry-run"], env)
    after_state = hashlib.sha256(open(state_path, "rb").read()).hexdigest()
    check("plan dry-run leaves state and plan output untouched",
          code == 0 and after_state == before_state
          and os.path.getmtime(plan_file) == before_plan, out + err)

    # Fallback: the record must name the model that actually produced the plan.
    _patch_config(config_path, lambda cfg: cfg["providers"]["p_high"].__setitem__(
        "baseUrl", "http://127.0.0.1:9/v1"))
    code, out, err = run(project, ["plan", "--requirements", requirements,
                                   "--max-tokens", "64", "--no-quota", "--json"], env)
    record = json.loads(out) if out.strip().startswith("{") else {}
    check("a fallback planner is recorded as the actual producer",
          code == 0 and record.get("planner") == "p_dead/mock-ok"
          and record.get("selectedPlanner") == "p_high/claude-mock"
          and record.get("scores", {}).get("weighted") == 4.0, out + err)
    _patch_config(config_path, lambda cfg: cfg["providers"]["p_high"].__setitem__(
        "baseUrl", base_b))

    code, out, err = run(project, ["plan", "--requirements", requirements,
                                   "--planner", "p_400/mock-400", "--no-quota"], env)
    check("a request-scoped planner failure returns exit 4", code == 4, out + err)

    _patch_config(config_path, lambda cfg: cfg["providers"]["p_low"].__setitem__(
        "overrides", {"mock-ok": {"scores": {"reasoning": "abc", "design": 99,
                                             "agentic": -5, "coding": 2}}}))
    import model_route as route_mod  # noqa: PLC0415
    cfg_scores = json.load(open(config_path, encoding="utf-8"))
    scores = route_mod.planning_scores({"lane": "http", "family": "planlab",
                                        "provider": "p_low", "model": "mock-ok"},
                                       cfg_scores)
    check("invalid planning scores are clamped or fall back",
          scores["reasoning"] == 3 and scores["design"] == 5
          and scores["agentic"] == 0 and scores["coding"] == 2
          and scores["source"] == "config", scores)

    empty = os.path.join(work, "plan-empty")
    os.makedirs(empty)
    run(empty, ["init"], env)
    _patch_config(os.path.join(empty, "_work", "models", "config.json"),
                  lambda cfg: cfg.__setitem__("lanes", {
                      "codex": {"enabled": False}, "dsh": {"enabled": False}}))
    code, out, err = run(empty, ["plan", "--requirements", requirements,
                                 "--no-quota"], env)
    check("no usable planner exits 3", code == 3, out + err)

    # The team gate applies to the planner too.
    run(project, ["team", "scan", "--json", "--no-local"], env)
    code, out, err = run(project, ["team", "add", "p_low/mock-ok"], env)
    check("a lower-scored approved model can be the planner", code == 0, out + err)
    code, out, err = run(project, ["route", "--role", "plan", "--no-quota", "--json"], env)
    plan = json.loads(out) if code == 0 else {}
    check("an unapproved high-score model cannot plan",
          code == 0 and plan.get("primary", {}).get("id") == "p_low/mock-ok", out + err)
    code, out, err = run(project, ["plan", "--requirements", requirements,
                                   "--planner", "p_high/claude-mock", "--no-quota"], env)
    check("pinning an unapproved planner fails", code == 2, out + err)


def gate_cases(env, work, port_a, port_b):
    """v2.2 gates: plan review, budget, facts, evidence, production, identity, release."""
    import check_docs  # noqa: PLC0415
    import review_budget  # noqa: PLC0415

    def script(project, name, args, extra_env=None):
        done = subprocess.run([sys.executable, os.path.join(SKILL, "scripts", name)] + args,
                              cwd=project, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=extra_env or env)
        return done.returncode, done.stdout or "", done.stderr or ""

    def write(path, text):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def write_json(path, data):
        write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def sha(path):
        return hashlib.sha256(open(path, "rb").read()).hexdigest()

    def mtime(path):
        from datetime import datetime
        return datetime.fromtimestamp(os.path.getmtime(path)).astimezone() \
            .isoformat(timespec="seconds")

    def facts_for(project, text=None):
        body = text or (
            "# Facts pack — gate fixture\n\n"
            "## budgets\n\n"
            "| budget | value | changed | reason |\n|---|---|---|---|\n"
            "| review_calls | 2 per milestone | | default |\n"
            "| review_minutes | 30 min | | default |\n")
        write(os.path.join(project, "_work", "facts_pack.md"), body)
        return script(project, "facts_pack.py", ["freeze"])

    # ---------------------------------------------------------------- facts pack
    facts_project = os.path.join(work, "facts-project")
    os.makedirs(facts_project)
    write(os.path.join(facts_project, "_work", "facts_pack.md"),
          "# Facts\n\n## budgets\n\n| budget | value |\n|---|---|\n"
          "| review_calls | 2 per milestone |\n| review_minutes | 30 min |\n")
    code, out, err = script(facts_project, "facts_pack.py", ["check"])
    check("facts_pack: an unversioned pack is refused", code == 3, out + err)
    code, out, err = script(facts_project, "facts_pack.py", ["freeze"])
    check("facts_pack: freeze writes version and hash", code == 0
          and "facts_hash" in open(os.path.join(facts_project, "_work", "facts_pack.md"),
                                   encoding="utf-8").read(), out + err)
    code, out, err = script(facts_project, "facts_pack.py", ["check"])
    check("facts_pack: a frozen pack checks clean", code == 0, out + err)
    with open(os.path.join(facts_project, "_work", "facts_pack.md"), "a",
              encoding="utf-8") as fh:
        fh.write("\n- drifted line\n")
    code, out, err = script(facts_project, "facts_pack.py", ["check"])
    check("facts_pack: an edited body is stale", code == 3, out + err)
    code, out, err = script(facts_project, "facts_pack.py",
                            ["bump", "--reason", "scope added"])
    check("facts_pack: bump increments the version and freezes", code == 0
          and "v2" in out, out + err)
    code, out, err = script(facts_project, "facts_pack.py", ["set-budget",
                                                             "review_calls", "3",
                                                             "--reason", "raise"])
    check("facts_pack: set-budget edits the row and re-freezes", code == 0, out + err)
    show = script(facts_project, "facts_pack.py", ["show", "--json"])[1]
    info = json.loads(show) if show.strip().startswith("{") else {}
    check("facts_pack: show reports the new budget", info.get("budgets", {})
          .get("review_calls") == 3, show)
    with open(os.path.join(facts_project, "_work", "facts_pack.md"), "a",
              encoding="utf-8") as fh:
        fh.write("facts_hash: deadbeef\n")
    code, out, err = script(facts_project, "facts_pack.py", ["check"])
    check("facts_pack: a duplicate hash header is malformed", code == 1, out + err)
    dup = os.path.join(work, "facts-dup")
    os.makedirs(dup)
    write(os.path.join(dup, "_work", "facts_pack.md"),
          "# F\n\n## budgets\n\n| budget | value |\n|---|---|\n"
          "| review_calls | 2 per milestone |\n| review_minutes | 30 min |\n")
    script(dup, "facts_pack.py", ["freeze"])
    with open(os.path.join(dup, "_work", "facts_pack.md"), "a", encoding="utf-8") as fh:
        fh.write("facts_version: 3\n")
    code, out, err = script(dup, "facts_pack.py", ["check"])
    check("facts_pack: duplicate version/header fields are malformed", code == 1, out + err)

    # ---------------------------------------------------------------- test evidence
    evidence_project = os.path.join(work, "evidence-project")
    os.makedirs(evidence_project)
    log = os.path.join(evidence_project, "_work", "sample.log")
    write(log, "\n".join(f"line-{index:02d}-abcdefghijklmnop" for index in range(1, 26)) + "\n")
    evidence = {
        "id": "tests", "protocol_version": 2,
        "command": "python run-tests.py", "argv": ["python", "run-tests.py"],
        "cwd": evidence_project, "exit_code": 0, "timed_out": False,
        "duration_s": 1.0, "started_at": "2026-10-28T23:00:00+08:00",
        "finished_at": "2026-10-28T23:00:01+08:00", "artifacts": [],
        "log_path": "_work/sample.log", "log_sha256": sha(log),
        "stdout_sha256": sha(log),
        "stderr_sha256": hashlib.sha256(b"").hexdigest(),
        "summary": "\n".join(f"line-{index:02d}-abcdefghijklmnop" for index in range(6, 26)),
    }
    write_json(os.path.join(evidence_project, "_work", "evidence", "tests.json"), evidence)
    code, out, err = script(evidence_project, "test_evidence.py",
                            ["record", "--name", "smoke", "--", sys.executable, "-c",
                             "print('evidence-ok')"])
    check("test_evidence: records a passing command",
          code == 0 and os.path.isfile(os.path.join(evidence_project, "_work", "evidence",
                                                    "smoke.json")), out + err)
    code, out, err = script(evidence_project, "test_evidence.py",
                            ["record", "--name", "fail", "--", sys.executable, "-c",
                             "import sys; sys.exit(7)"])
    check("test_evidence: propagates a failing exit code", code == 7, out + err)

    # ---------------------------------------------------------------- envelope + validator
    facts_for(evidence_project)
    plan_text = ("# Plan\n\n## Work packages\n\n| id | output | DoD |\n"
                 "|---|---|---|\n| WP1 | out.txt | produce the output |\n")
    write(os.path.join(evidence_project, "_work", "plan", "plan.md"), plan_text)
    write(os.path.join(evidence_project, "_work", "plan", "review_ledger.md"),
          "# review ledger\n\n| id | round | where | status | reason | owner | budget_change |\n"
          "|---|---|---|---|---|---|---|\n")
    code, out, err = script(evidence_project, "build_envelope.py", [
        "--artifact", "_work/plan/plan.md", "--out", "_work/smoke/env.yaml",
        "--ledger", "_work/plan/review_ledger.md",
        "--unit", "plan::_work/plan/plan.md::work packages",
        "--test-evidence", "_work/evidence/tests.json", "--scope", "test"])
    check("envelope: facts + evidence build succeeds", code == 0, out + err)
    envelope_text = open(os.path.join(evidence_project, "_work", "smoke", "env.yaml"),
                         encoding="utf-8").read()
    check("envelope: carries facts and evidence units",
          "envelope_version: 2" in envelope_text and "facts_hash:" in envelope_text
          and "evidence:tests" in envelope_text and "test_evidence:" in envelope_text,
          envelope_text[:400])
    env_info = {}
    for line in envelope_text.splitlines():
        match = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
        if match and match.group(1) not in ("units", "test_evidence", "prior_findings"):
            env_info[match.group(1)] = match.group(2).strip().strip('"')

    def result_for(env_path, unit_ids, findings=None, refs=None):
        text = open(env_path, encoding="utf-8").read()
        values = {}
        for line in text.splitlines():
            match = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
            if match:
                values[match.group(1)] = match.group(2).strip().strip('"')
        return {
            "request_id": values["request_id"],
            "artifact_hash": values["version_hash"],
            "artifact_mtime": values["artifact_mtime"],
            "units_seen": unit_ids,
            "units_confirmed": [{"unit": unit, "observed": f"opened {unit}"}
                                for unit in unit_ids],
            "findings": findings or [],
            "confidence": "high",
            "facts_version": values.get("facts_version", ""),
            "test_refs": refs or [],
            "limitations": [],
        }

    result = result_for(os.path.join(evidence_project, "_work", "smoke", "env.yaml"),
                        ["plan", "evidence:tests"],
                        refs=[{"id": "tests", "command": evidence["command"],
                               "exit_code": 0, "log_sha256": evidence["log_sha256"],
                               "observed": "line-01-abcdefghijklmnop"}])
    result_path = os.path.join(evidence_project, "_work", "smoke", "result.json")
    write_json(result_path, result)
    code, out, err = script(evidence_project, "validate_result.py", [
        "--result", result_path, "--envelope", "_work/smoke/env.yaml"])
    check("validator: a real log excerpt is accepted", code == 0, out + err)
    log_text = open(os.path.join(evidence_project, "_work", "sample.log"),
                    encoding="utf-8").read()
    with open(os.path.join(evidence_project, "_work", "sample.log"), "a",
              encoding="utf-8") as fh:
        fh.write("appended-after-record\n")
    code, out, err = script(evidence_project, "validate_result.py", [
        "--result", result_path, "--envelope", "_work/smoke/env.yaml"])
    check("validator: a log changed after evidence recording is rejected", code == 1,
          out + err)
    write(os.path.join(evidence_project, "_work", "sample.log"), log_text)
    result["test_refs"][0]["observed"] = "line-25-abcdefghijklmnop"
    write_json(result_path, result)
    code, out, err = script(evidence_project, "validate_result.py", [
        "--result", result_path, "--envelope", "_work/smoke/env.yaml"])
    check("validator: an excerpt copied from the summary is rejected", code == 1, out + err)
    result["test_refs"][0]["observed"] = "line-01-abcdefghijklmnop"
    result["test_refs"] = []
    write_json(result_path, result)
    code, out, err = script(evidence_project, "validate_result.py", [
        "--result", result_path, "--envelope", "_work/smoke/env.yaml"])
    check("validator: evidence without test_refs is invalid", code == 1, out + err)
    result["test_refs"] = [{"id": "tests", "command": evidence["command"],
                            "exit_code": 0, "log_sha256": evidence["log_sha256"],
                            "observed": "line-01-abcdefghijklmnop"}]
    result["facts_version"] = "999"
    write_json(result_path, result)
    code, out, err = script(evidence_project, "validate_result.py", [
        "--result", result_path, "--envelope", "_work/smoke/env.yaml"])
    check("validator: a wrong facts_version is rejected", code == 1, out + err)
    with open(os.path.join(evidence_project, "_work", "facts_pack.md"), "a",
              encoding="utf-8") as fh:
        fh.write("\n- post-build drift\n")
    result["facts_version"] = "1"
    write_json(result_path, result)
    code, out, err = script(evidence_project, "validate_result.py", [
        "--result", result_path, "--envelope", "_work/smoke/env.yaml"])
    check("validator: a facts pack mutated after build is rejected", code == 1, out + err)
    facts_text = open(os.path.join(evidence_project, "_work", "facts_pack.md"),
                      encoding="utf-8").read()
    facts_hash = next((line.split(":", 1)[1].strip()
                       for line in facts_text.splitlines()
                       if line.startswith("facts_hash:")), "")
    write(os.path.join(evidence_project, "_work", "facts_pack.md"),
          facts_text.replace(f"facts_hash: {facts_hash}", "facts_hash: " + "0" * 64, 1))
    code, out, err = script(evidence_project, "validate_result.py", [
        "--result", result_path, "--envelope", "_work/smoke/env.yaml"])
    check("validator: an edited facts_hash header is rejected", code == 1, out + err)

    # ---------------------------------------------------------------- plan gate
    gate_project = os.path.join(work, "gate-project")
    os.makedirs(gate_project)
    base_a = f"http://127.0.0.1:{port_a}/v1"
    base_b = f"http://127.0.0.1:{port_b}/v1"
    run(gate_project, ["init"], env)
    run(gate_project, ["add-custom", "g_a", "--base-url", base_a, "--models",
                       "mock-ok", "--auth", "none"], env)
    run(gate_project, ["add-custom", "g_b", "--base-url", base_b, "--format",
                       "anthropic-messages", "--models", "claude-mock",
                       "--auth", "apiKey"], env)
    run(gate_project, ["add-custom", "g_r", "--base-url", base_a, "--models",
                       "mock-review", "--auth", "none"], env)
    run(gate_project, ["key", "set", "g_b", "--from-env", "TEST_KEY"], env)
    config_path = os.path.join(gate_project, "_work", "models", "config.json")
    _patch_config(config_path, lambda cfg: cfg["providers"]["g_a"].__setitem__(
        "overrides", {"mock-ok": {"family": "gate_a"}}))
    _patch_config(config_path, lambda cfg: cfg["providers"]["g_b"].__setitem__(
        "overrides", {"claude-mock": {"family": "gate_b"}}))
    _patch_config(config_path, lambda cfg: cfg["providers"]["g_r"].__setitem__(
        "overrides", {"mock-review": {"family": "gate_r"}}))
    facts_for(gate_project)
    write(os.path.join(gate_project, "_work", "plan", "plan.md"), plan_text)
    write(os.path.join(gate_project, "_work", "plan", "review_ledger.md"),
          "# review ledger\n\n| id | round | where | status | reason | owner | budget_change |\n"
          "|---|---|---|---|---|---|---|\n")
    code, out, err = script(gate_project, "build_envelope.py", [
        "--artifact", "_work/plan/plan.md", "--out", "_work/plan/env_plan.yaml",
        "--ledger", "_work/plan/review_ledger.md",
        "--unit", "plan::_work/plan/plan.md::work packages", "--scope", "plan review"])
    check("plan gate: envelope builds", code == 0, out + err)

    def make_plan_review(project, env_name, result_name, reviewer_model,
                         protocol=True, contract="2.2.0"):
        env_path = os.path.join(project, env_name)
        text = open(env_path, encoding="utf-8").read()
        values = {}
        for line in text.splitlines():
            match = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
            if match:
                values[match.group(1)] = match.group(2).strip().strip('"')
        result = {
            "request_id": values["request_id"],
            "artifact_hash": values["version_hash"],
            "artifact_mtime": values["artifact_mtime"],
            "units_seen": ["plan"],
            "units_confirmed": [{"unit": "plan", "observed": "work packages"}],
            "findings": [], "confidence": "high", "limitations": [],
            "facts_version": values.get("facts_version", ""),
        }
        result_path = os.path.join(project, result_name)
        write_json(result_path, result)
        call = {
            "request_id": values["request_id"], "status": "ok", "ephemeral": False,
            "schema_valid": True, "model": reviewer_model,
            "contract": {"pinned": contract},
            "envelope_path": os.path.abspath(env_path),
            "envelope_sha256": sha(env_path),
            "result_path": os.path.abspath(result_path),
            "result_sha256": sha(result_path),
            "artifact": os.path.abspath(os.path.join(project, "_work/plan/plan.md")),
            "artifact_sha256": values["version_hash"],
        }
        if protocol:
            call.update({"protocol_version": 2, "role": "plan-review"})
        write_json(os.path.join(project, "_work", "calls",
                                f"{values['request_id']}.json"), call)
        return env_path, result_path, call

    env_path, result_path, call = make_plan_review(
        gate_project, "_work/plan/env_plan.yaml", "_work/plan/plan.review.json",
        "g_b/claude-mock")
    code, out, err = script(gate_project, "plan_gate.py", [
        "check", "--envelope", "_work/plan/env_plan.yaml",
        "--result", "_work/plan/plan.review.json"])
    check("plan gate: reviewed plan with a different family passes", code == 0, out + err)
    write(os.path.join(gate_project, "_work", "plan", "planner.json"),
          json.dumps({"planner": "g_a/mock-ok", "requestId": "P1",
                      "plan_sha256": sha(os.path.join(gate_project, "_work/plan/plan.md")),
                      "plan_mtime": mtime(os.path.join(gate_project, "_work/plan/plan.md"))}))
    write_json(os.path.join(gate_project, "_work", "calls", "P1.json"),
               {"request_id": "P1", "status": "ok", "model": "g_a/mock-ok",
                "contract": {"pinned": "2.2.0"}})
    call["model"] = "g_a/mock-ok"
    write_json(os.path.join(gate_project, "_work", "calls",
                            f"{call['request_id']}.json"), call)
    code, out, err = script(gate_project, "plan_gate.py", [
        "check", "--envelope", "_work/plan/env_plan.yaml",
        "--result", "_work/plan/plan.review.json"])
    check("plan gate: the same family is refused", code == 6, out + err)
    call["model"] = "g_b/claude-mock"
    write_json(os.path.join(gate_project, "_work", "calls",
                            f"{call['request_id']}.json"), call)
    os.remove(os.path.join(gate_project, "_work", "plan", "planner.json"))
    os.remove(os.path.join(gate_project, "_work", "calls", "P1.json"))
    code, out, err = script(gate_project, "plan_gate.py", [
        "check", "--envelope", "_work/plan/env_plan.yaml",
        "--result", "_work/plan/plan.review.json"])
    check("plan gate: a reviewed plan passes again", code == 0, out + err)
    os.remove(os.path.join(gate_project, "_work", "calls", f"{call['request_id']}.json"))
    code, out, err = script(gate_project, "plan_gate.py", [
        "check", "--envelope", "_work/plan/env_plan.yaml",
        "--result", "_work/plan/plan.review.json"])
    check("plan gate: a missing call record is not evidence", code == 3, out + err)
    write_json(os.path.join(gate_project, "_work", "calls",
                            f"{call['request_id']}.json"), call)
    with open(os.path.join(gate_project, "_work", "plan", "plan.review.json"), "a",
              encoding="utf-8") as fh:
        fh.write(" ")
    code, out, err = script(gate_project, "plan_gate.py", [
        "check", "--envelope", "_work/plan/env_plan.yaml",
        "--result", "_work/plan/plan.review.json"])
    check("plan gate: a result edited after the call is rejected", code == 4, out + err)
    make_plan_review(gate_project, "_work/plan/env_plan.yaml",
                     "_work/plan/plan.review.json", "g_b/claude-mock")
    finding = {"id": "F1", "location": "plan", "target": "scope", "severity": "high",
               "severity_basis": "missing gate", "issue": "no gate", "evidence": "text",
               "fix": "add gate", "origin": "independent"}
    env_path, result_path, call = make_plan_review(
        gate_project, "_work/plan/env_plan.yaml", "_work/plan/plan.review.json",
        "g_b/claude-mock")
    result = json.load(open(result_path, encoding="utf-8"))
    result["findings"] = [finding]
    write_json(result_path, result)
    call["result_sha256"] = sha(result_path)
    write_json(os.path.join(gate_project, "_work", "calls",
                            f"{call['request_id']}.json"), call)
    code, out, err = script(gate_project, "plan_gate.py", [
        "check", "--envelope", "_work/plan/env_plan.yaml",
        "--result", "_work/plan/plan.review.json"])
    check("plan gate: an open high finding blocks dispatch", code == 5, out + err)
    write(os.path.join(gate_project, "_work", "plan", "review_ledger.md"),
          "# review ledger\n\n| id | round | where | status | reason | owner | budget_change |\n"
          "|---|---|---|---|---|---|---|\n| F1 | 1 | plan | rejected | by design | lead | |\n")
    code, out, err = script(gate_project, "plan_gate.py", [
        "check", "--envelope", "_work/plan/env_plan.yaml",
        "--result", "_work/plan/plan.review.json"])
    check("plan gate: a closed finding unblocks", code == 0, out + err)

    # Legacy v1 evidence is accepted only for a call pinned below 2.2.0.
    legacy_env = os.path.join(gate_project, "_work", "plan", "env_legacy.yaml")
    text = open(os.path.join(gate_project, "_work", "plan", "env_plan.yaml"),
                encoding="utf-8").read().replace("envelope_version: 2\n", "")
    write(legacy_env, text)
    legacy_result = result_for(legacy_env, ["plan"], findings=[])
    legacy_result_path = os.path.join(gate_project, "_work", "plan", "legacy.review.json")
    write_json(legacy_result_path, legacy_result)
    legacy_rid = legacy_result["request_id"]
    write_json(os.path.join(gate_project, "_work", "calls", f"{legacy_rid}.json"), {
        "request_id": legacy_rid, "status": "ok", "ephemeral": False,
        "schema_valid": True, "model": "g_b/claude-mock",
        "contract": {"pinned": "2.1.0"}})
    code, out, err = script(gate_project, "plan_gate.py", [
        "check", "--envelope", "_work/plan/env_legacy.yaml",
        "--result", "_work/plan/legacy.review.json"])
    check("plan gate: legacy 2.1 evidence is accepted with a v1 envelope", code == 0,
          out + err)
    code, out, err = script(gate_project, "plan_gate.py", [
        "check", "--envelope", "_work/plan/env_plan.yaml",
        "--result", "_work/plan/legacy.review.json"])
    check("plan gate: a v2 envelope with a legacy record is rejected", code == 6,
          out + err)

    # Stale plan, missing binding field, missing booleans, migrated planner.
    good_plan = open(os.path.join(gate_project, "_work", "plan", "plan.md"),
                     encoding="utf-8").read()
    with open(os.path.join(gate_project, "_work", "plan", "plan.md"), "a",
              encoding="utf-8") as fh:
        fh.write("\n- changed after review\n")
    code, out, err = script(gate_project, "plan_gate.py", [
        "check", "--envelope", "_work/plan/env_plan.yaml",
        "--result", "_work/plan/plan.review.json"])
    check("plan gate: a plan changed after review is rejected", code == 4, out + err)
    write(os.path.join(gate_project, "_work", "plan", "plan.md"), good_plan)
    code, out, err = script(gate_project, "build_envelope.py", [
        "--artifact", "_work/plan/plan.md", "--out", "_work/plan/env_plan.yaml",
        "--ledger", "_work/plan/review_ledger.md",
        "--unit", "plan::_work/plan/plan.md::work packages", "--scope", "plan review"])
    check("plan gate: envelope rebuilds after the stale-plan test", code == 0, out + err)
    env_path, result_path, call = make_plan_review(
        gate_project, "_work/plan/env_plan.yaml", "_work/plan/plan.review.json",
        "g_b/claude-mock")
    call_path = os.path.join(gate_project, "_work", "calls", f"{call['request_id']}.json")
    saved_call = dict(call)
    call.pop("artifact_sha256")
    write_json(call_path, call)
    code, out, err = script(gate_project, "plan_gate.py", [
        "check", "--envelope", "_work/plan/env_plan.yaml",
        "--result", "_work/plan/plan.review.json"])
    check("plan gate: a missing artifact_sha256 is rejected", code == 4, out + err)
    call = dict(saved_call, schema_valid=None)
    write_json(call_path, call)
    code, out, err = script(gate_project, "plan_gate.py", [
        "check", "--envelope", "_work/plan/env_plan.yaml",
        "--result", "_work/plan/plan.review.json"])
    check("plan gate: a missing schema_valid is rejected", code == 4, out + err)
    call = dict(saved_call, ephemeral=None)
    write_json(call_path, call)
    code, out, err = script(gate_project, "plan_gate.py", [
        "check", "--envelope", "_work/plan/env_plan.yaml",
        "--result", "_work/plan/plan.review.json"])
    check("plan gate: a missing ephemeral is rejected", code == 4, out + err)
    write_json(call_path, saved_call)
    write(os.path.join(gate_project, "_work", "plan", "planner.json"),
          json.dumps({"planner": "g_b/claude-mock", "requestId": "P2",
                      "plan_sha256": "0" * 64,
                      "plan_mtime": mtime(os.path.join(gate_project, "_work", "plan",
                                                       "plan.md"))}))
    code, out, err = script(gate_project, "plan_gate.py", [
        "check", "--envelope", "_work/plan/env_plan.yaml",
        "--result", "_work/plan/plan.review.json"])
    check("plan gate: a forged planner record is rejected", code == 4, out + err)
    write(os.path.join(gate_project, "_work", "plan", "planner.json"),
          json.dumps({"planner": "mystery/none", "requestId": "P2",
                      "plan_sha256": sha(os.path.join(gate_project, "_work", "plan",
                                                      "plan.md")),
                      "plan_mtime": mtime(os.path.join(gate_project, "_work", "plan",
                                                       "plan.md"))}))
    write_json(os.path.join(gate_project, "_work", "calls", "P2.json"),
               {"request_id": "P2", "status": "ok", "model": "mystery/none",
                "contract": {"pinned": "2.2.0"}})
    code, out, err = script(gate_project, "plan_gate.py", [
        "check", "--envelope", "_work/plan/env_plan.yaml",
        "--result", "_work/plan/plan.review.json"])
    check("plan gate: an unknown producer family is rejected", code == 6, out + err)
    os.remove(os.path.join(gate_project, "_work", "plan", "planner.json"))
    os.remove(os.path.join(gate_project, "_work", "calls", "P2.json"))
    mixed = json.load(open(os.path.join(gate_project, "_work", "plan", "plan.review.json"),
                           encoding="utf-8"))
    mixed["request_id"] = "R-OTHER"
    write_json(os.path.join(gate_project, "_work", "plan", "mixed.review.json"), mixed)
    code, out, err = script(gate_project, "plan_gate.py", [
        "check", "--envelope", "_work/plan/env_plan.yaml",
        "--result", "_work/plan/mixed.review.json"])
    check("plan gate: a result mixed from another request is rejected", code == 4,
          out + err)

    # HTTP review path: the HTTP call record must carry the v2 bindings.
    code, out, err = script(gate_project, "build_envelope.py", [
        "--artifact", "_work/plan/plan.md", "--out", "_work/plan/env_http.yaml",
        "--ledger", "_work/plan/review_ledger.md",
        "--unit", "plan::_work/plan/plan.md::work packages", "--scope", "http review"])
    check("http review: envelope builds", code == 0, out + err)
    http_env = os.path.join(gate_project, "_work", "plan", "env_http.yaml")
    http_result = result_for(http_env, ["plan"])
    MockState.review_payload = json.dumps(http_result)
    run(gate_project, ["chains", "set", "http-review", "--models",
                       "g_r/mock-review"], env)
    code, out, err = run(gate_project, [
        "call", "--chain", "http-review", "--role", "plan-review", "--milestone",
        "plan", "--request-id", http_result["request_id"], "--prompt", "review",
        "--schema", os.path.join(SKILL, "assets", "result.schema.json"),
        "--envelope", http_env, "--result-path", "_work/plan/http.review.json"], env)
    check("http review: a valid JSON result is accepted and recorded", code == 0,
          out + err)
    code, out, err = script(gate_project, "plan_gate.py", [
        "check", "--envelope", "_work/plan/env_http.yaml",
        "--result", "_work/plan/http.review.json"])
    check("http review: the HTTP call record satisfies the plan gate", code == 0,
          out + err)
    http_calls = [json.loads(line) for line in
                  open(os.path.join(gate_project, "_work", "models", "model_calls.jsonl"),
                       encoding="utf-8") if line.strip()]
    rich = [row for row in http_calls
            if row.get("requestId") == http_result["request_id"]
            and row.get("resultSha256")]
    check("http review: the rich record keeps the call duration",
          bool(rich) and rich[-1].get("duration_s") is not None
          and rich[-1].get("ephemeral") is False, rich[-1] if rich else http_calls)

    # HTTP production: the artifact path/hash are recorded relative to --project.
    run(gate_project, ["chains", "set", "http-produce", "--models",
                       "g_r/mock-review"], env)
    produced = os.path.join(gate_project, "_work", "prod.txt")
    code, out, err = run(gate_project, [
        "call", "--chain", "http-produce", "--role", "produce", "--work-package",
        "WP1", "--produce-artifact", "_work/prod.txt", "--output", produced,
        "--prompt", "produce"], env)
    http_calls = [json.loads(line) for line in
                  open(os.path.join(gate_project, "_work", "models", "model_calls.jsonl"),
                       encoding="utf-8") if line.strip()]
    produced_rows = [row for row in http_calls
                     if row.get("role") == "produce" and row.get("produceArtifactHash")]
    check("http produce: the artifact path and hash point into the project", code == 0
          and bool(produced_rows)
          and produced_rows[-1].get("produceArtifact") == produced
          and produced_rows[-1].get("produceArtifactHash") == sha(produced),
          out + err + str(produced_rows[-1] if produced_rows else http_calls))

    # Omitted-role models.py calls are production; probe is explicit and text-only.
    noplan = os.path.join(work, "noplan-project")
    os.makedirs(noplan)
    run(noplan, ["init"], env)
    run(noplan, ["add-custom", "n_a", "--base-url", base_a, "--models", "mock-ok",
                 "--auth", "none"], env)
    code, out, err = run(noplan, ["call", "--model", "n_a/mock-ok", "--prompt", "hi"], env)
    check("models: an omitted role is production and needs a reviewed plan", code == 2,
          out + err)
    code, out, err = run(noplan, ["call", "--model", "n_a/mock-ok", "--prompt", "hi",
                                  "--schema", "x.json"], env)
    check("models: schema flags without a review role are refused", code == 2, out + err)
    run(noplan, ["chains", "set", "plain", "--models", "n_a/mock-ok"], env)
    code, out, err = run(noplan, ["call", "--chain", "plain", "--prompt", "hi"], env)
    check("models: an omitted role on a chain is production and needs a plan",
          code == 2, out + err)
    code, out, err = run(noplan, ["call", "--role", "probe", "--model", "n_a/mock-ok",
                                  "--prompt", "hi", "--output", "out.txt"], env)
    check("models: probe rejects an output path", code == 2, out + err)
    code, out, err = run(noplan, ["call", "--role", "probe", "--model", "n_a/mock-ok",
                                  "--prompt", "hi", "--schema", "x.json"], env)
    check("models: probe rejects a schema", code == 2, out + err)
    code, out, err = run(noplan, ["call", "--role", "probe", "--model", "n_a/mock-ok",
                                  "--prompt", "hi", "--work-package", "WP1"], env)
    check("models: probe rejects a work package", code == 2, out + err)
    code, out, err = run(noplan, ["call", "--role", "probe", "--model", "n_a/mock-ok",
                                  "--prompt", "hi"], env)
    check("models: probe still works as a text-only call", code == 0, out + err)

    import argparse as _argparse  # noqa: PLC0415
    import models as models_mod  # noqa: PLC0415
    meta = models_mod._call_meta(_argparse.Namespace(
        role="produce", milestone="M1", work_package="WP1",
        produce_artifact="out.txt", schema=None, envelope=None, result_path=None))
    check("models: an HTTP produce record carries its artifact binding",
          meta.get("role") == "produce" and meta.get("workPackage") == "WP1"
          and meta.get("produceArtifact", "").endswith("out.txt")
          and meta.get("ephemeral") is False and meta.get("protocolVersion") == 2, meta)
    check("models: an omitted role is production even with a schema",
          models_mod._effective_role(_argparse.Namespace(role=None, schema="x.json"))
          == "produce")

    # ---------------------------------------------------------------- review budget
    budget_project = os.path.join(work, "budget-project")
    os.makedirs(budget_project)
    facts_for(budget_project)
    for index, duration in (("R1", 20), ("R2", 15)):
        write_json(os.path.join(budget_project, "_work", "calls", f"{index}.json"), {
            "request_id": index, "status": "ok", "role": "review", "milestone": "M1",
            "duration_s": duration, "model": "g_b/claude-mock", "protocol_version": 2})
    code, out, err = script(budget_project, "review_budget.py",
                            ["check", "--milestone", "M1"])
    check("review budget: a third round needs why-unclosed", code == 3, out + err)
    code, out, err = script(budget_project, "review_budget.py",
                            ["check", "--milestone", "M1", "--why-unclosed",
                             "rounds 1-2 did not close the design"])
    check("review budget: over budget needs a change", code == 4, out + err)
    code, out, err = script(budget_project, "review_budget.py",
                            ["check", "--milestone", "M1", "--why-unclosed", "x",
                             "--budget-change", "one-off"])
    check("review budget: an explicit one-off passes the check", code == 0, out + err)
    code, out, err = script(budget_project, "review_budget.py",
                            ["raise", "--to", "4", "--reason", "raise for closure"])
    check("review budget: raise edits the facts pack", code == 0, out + err)
    code, out, err = script(budget_project, "review_budget.py",
                            ["check", "--milestone", "M1", "--why-unclosed", "x"])
    check("review budget: the raised budget admits the third call", code == 0, out + err)
    code, out, err = script(budget_project, "review_budget.py",
                            ["note-minutes", "--milestone", "M1", "--reason", "over"])
    check("review budget: minute notice is recorded", code == 0, out + err)
    code, info = review_budget.preflight(budget_project, "M1", "review", "R1")
    check("review budget: a request id cannot be reused", code == 2, info)
    code, info = review_budget.preflight(budget_project, "M1", "review", "R3",
                                         why_unclosed="x")
    check("review budget: a reservation is written atomically", code == 0
          and os.path.isfile(os.path.join(budget_project, "_work", "calls", "pending",
                                          "R3.json")), info)
    code, info = review_budget.preflight(budget_project, "M1", "review", "R4",
                                         why_unclosed="x")
    check("review budget: a distinct concurrent id also reserves", code == 0, info)
    review_budget.finalize(budget_project, "R3")
    review_budget.finalize(budget_project, "R4")
    report_text = script(budget_project, "review_budget.py", ["report", "--json"])[1]
    report = json.loads(report_text) if report_text.strip().startswith("{") else {}
    check("review budget: report counts the milestone", report.get("milestones")
          and report["milestones"][0].get("review_calls") == 2, report_text)

    minutes_project = os.path.join(work, "minutes-project")
    os.makedirs(minutes_project)
    facts_for(minutes_project)
    write_json(os.path.join(minutes_project, "_work", "calls", "M1.json"), {
        "request_id": "M1", "status": "ok", "role": "review", "milestone": "M2",
        "duration_s": 1900, "model": "g_b/claude-mock", "protocol_version": 2})
    code, out, err = script(minutes_project, "review_budget.py",
                            ["deliver", "--tier", "B"])
    check("review budget: 31 minutes exceeds the 30-minute budget", code == 5, out + err)
    code, out, err = script(minutes_project, "review_budget.py",
                            ["note-minutes", "--milestone", "M2", "--reason", "over"])
    check("review budget: a minutes row unblocks delivery", code == 0, out + err)
    code, out, err = script(minutes_project, "review_budget.py",
                            ["deliver", "--tier", "B"])
    check("review budget: delivery passes after the minutes row", code == 0, out + err)

    http_minutes = os.path.join(work, "http-minutes")
    os.makedirs(http_minutes)
    facts_for(http_minutes)
    write(os.path.join(http_minutes, "_work", "models", "model_calls.jsonl"),
          "\n".join(json.dumps({
              "ts": "2026-10-29T22:00:00+08:00", "requestId": f"H{index}",
              "model": "g_r/mock-review", "lane": "http", "status": "ok",
              "role": "review", "milestone": "M3", "protocolVersion": 2,
              "latencySeconds": 1000}) for index in range(1, 3)) + "\n")
    code, out, err = script(http_minutes, "review_budget.py",
                            ["deliver", "--tier", "B"])
    check("review budget: HTTP review durations count toward minutes", code == 5,
          out + err)

    # ---------------------------------------------------------------- production/deliver
    prod = os.path.join(work, "prod-project")
    os.makedirs(prod)
    facts_for(prod)
    write(os.path.join(prod, "_work", "plan", "plan.md"), plan_text)
    artifact = os.path.join(prod, "out.txt")
    write(artifact, "produced\n")
    produce = {"request_id": "P1", "status": "ok", "role": "produce",
               "milestone": "M1", "model": "g_b/claude-mock", "protocol_version": 2,
               "work_package": "WP1", "produce_artifact": artifact,
               "produce_artifact_hash": sha(artifact), "routing_tier": "S"}
    write_json(os.path.join(prod, "_work", "calls", "P1.json"), produce)
    code, out, err = script(prod, "review_budget.py", ["deliver", "--tier", "S"])
    check("deliver: a produce unit without adoption blocks", code == 5, out + err)
    code, out, err = script(prod, "review_budget.py",
                            ["adopt", "--request-id", "P1", "--decision", "adopted",
                             "--reason", "integrated"])
    check("deliver: adoption is recorded", code == 0, out + err)
    code, out, err = script(prod, "review_budget.py", ["deliver", "--tier", "S"])
    check("deliver: S/A with an adopted plan work package passes", code == 0, out + err)
    produce["work_package"] = "WP9"
    write_json(os.path.join(prod, "_work", "calls", "P1.json"), produce)
    code, out, err = script(prod, "review_budget.py", ["deliver", "--tier", "S"])
    check("deliver: a work package not in the plan is refused", code == 5, out + err)
    produce["work_package"] = "WP1"
    produce["produce_artifact"] = os.path.join(prod, "other.txt")
    write_json(os.path.join(prod, "_work", "calls", "P1.json"), produce)
    code, out, err = script(prod, "review_budget.py", ["deliver", "--tier", "S"])
    check("deliver: the artifact must match the WP output", code == 5, out + err)
    produce["produce_artifact"] = artifact
    produce.pop("routing_tier")
    write_json(os.path.join(prod, "_work", "calls", "P1.json"), produce)
    code, out, err = script(prod, "review_budget.py", ["deliver"])
    check("deliver: a missing tier fails closed", code == 5, out + err)
    code, out, err = script(prod, "review_budget.py", ["deliver", "--tier", "B"])
    check("deliver: B is exempt from the GPT production rule", code == 0, out + err)
    produce["routing_tier"] = "S"
    write_json(os.path.join(prod, "_work", "calls", "P1.json"), produce)
    produce.pop("produce_artifact_hash")
    write_json(os.path.join(prod, "_work", "calls", "P1.json"), produce)
    code, out, err = script(prod, "review_budget.py", ["deliver", "--tier", "S"])
    check("deliver: a missing artifact hash is refused", code == 5, out + err)
    produce["produce_artifact_hash"] = "0" * 64
    write_json(os.path.join(prod, "_work", "calls", "P1.json"), produce)
    code, out, err = script(prod, "review_budget.py", ["deliver", "--tier", "S"])
    check("deliver: a wrong artifact hash is refused", code == 5, out + err)
    produce["produce_artifact_hash"] = sha(artifact)
    write_json(os.path.join(prod, "_work", "calls", "P1.json"), produce)
    approval = os.path.join(prod, "_work", "gates", "approval.md")
    write(approval, "user approved the deviation\n")
    code, out, err = script(prod, "review_budget.py",
                            ["waive", "--tier", "S", "--approval-ref", approval,
                             "--reason", "lead produced this delivery"])
    check("deliver: a stored approval is required and recorded", code == 0, out + err)
    os.remove(os.path.join(prod, "_work", "calls", "P1.json"))
    with open(approval, "a", encoding="utf-8") as fh:
        fh.write("tampered\n")
    code, out, err = script(prod, "review_budget.py", ["deliver", "--tier", "S"])
    check("deliver: a tampered approval file is refused", code == 5, out + err)
    write(approval, "user approved the deviation\n")
    code, out, err = script(prod, "review_budget.py", ["deliver", "--tier", "S"])
    check("deliver: an approved deviation unblocks S/A without a produce unit", code == 0,
          out + err)

    # ---------------------------------------------------------------- workspace identity
    session_project = os.path.join(work, "session-project")
    os.makedirs(session_project)
    code, out, err = script(session_project, "session.py", ["pin"])
    pin_path = os.path.join(session_project, "_work", "skill_pin.json")
    pin = json.load(open(pin_path, encoding="utf-8")) if os.path.isfile(pin_path) else {}
    check("session: pin stores the workspace identity", code == 0
          and pin.get("workspace", {}).get("root")
          and pin.get("workspace", {}).get("volume"), out + err)
    code, out, err = script(session_project, "session.py", ["check"])
    check("session: the same root passes", code == 0, out + err)
    saved = dict(pin)
    pin["workspace"] = dict(pin["workspace"], drive="Z:")
    write_json(pin_path, pin)
    code, out, err = script(session_project, "session.py", ["check"])
    check("session: a drive mismatch requires re-pin", code == 2, out + err)
    pin["workspace"] = dict(saved["workspace"], volume=1)
    write_json(pin_path, pin)
    code, out, err = script(session_project, "session.py", ["check"])
    check("session: a volume mismatch requires re-pin", code == 2, out + err)
    write_json(pin_path, saved)
    copied = os.path.join(work, "session-copy")
    os.makedirs(os.path.join(copied, "_work"))
    shutil.copyfile(pin_path, os.path.join(copied, "_work", "skill_pin.json"))
    code, out, err = script(copied, "session.py", ["check"])
    check("session: a copied pin is refused in another root", code == 2, out + err)
    legacy = {key: value for key, value in saved.items() if key != "workspace"}
    write_json(pin_path, legacy)
    code, out, err = script(session_project, "session.py", ["check"])
    check("session: a legacy pin requires re-pin", code == 2, out + err)
    write_json(pin_path, saved)
    identity_text = script(session_project, "session.py", ["identity", "--json"])[1]
    identity = json.loads(identity_text) if identity_text.strip().startswith("{") else {}
    check("session: identity reports root/drive/volume", bool(identity.get("root"))
          and bool(identity.get("drive")) and identity.get("volume") is not None,
          identity_text)

    # ---------------------------------------------------------------- check_docs + release
    def docs_fixture(root, *, trigger="甲、乙", version="1.0.0", rules=None,
                     extra_script=False, extra_text="", readme_version=None,
                     changelog_count=1):
        if os.path.isdir(root):
            shutil.rmtree(root)
        rules = rules or "1. **One**\n2. **Two**"
        write(os.path.join(root, "SKILL.md"),
              f"---\nname: fixture\ndescription: fixture. 触发词：{trigger}。\n"
              f"metadata:\n  version: {version}\n  updated: 2026-10-28\n---\n\n"
              f"# Fixture\n\n## Non-negotiables\n\n{rules}\n\n## Scripts\n\n"
              f"`python scripts/a.py`\n{extra_text}")
        write(os.path.join(root, "README.md"),
              f"# Fixture\n\n- 版本：{readme_version or version}（2026-10-28）\n"
              f"- 当前离线自测：{changelog_count} 项（mock）\n\n## 目录\n\n`a.py`\n\n"
              f"## 更新日志\n\n| 版本 | 日期 | 变更 |\n|---|---|---|\n"
              f"| {version} | 2026-10-28 | x；{changelog_count} 项离线自测 |\n")
        write(os.path.join(root, "scripts", "a.py"), "print('ok')\n")
        if extra_script:
            write(os.path.join(root, "scripts", "b.py"), "print('ok')\n")

    docs = os.path.join(work, "docs-fixture")
    docs_fixture(docs)
    issues = check_docs.collect_issues(docs)
    check("check_docs: a clean fixture passes", not issues, issues)
    docs_fixture(docs, trigger="甲、甲")
    codes = {issue["code"] for issue in check_docs.collect_issues(docs)}
    check("check_docs: duplicate trigger words are caught", "trigger" in codes, codes)
    docs_fixture(docs, extra_text="\nF:\\old\\path\n")
    codes = {issue["code"] for issue in check_docs.collect_issues(docs)}
    check("check_docs: an old F: path is caught", "old-path" in codes, codes)
    docs_fixture(docs, extra_text="\nliteral \\n in prose\n")
    codes = {issue["code"] for issue in check_docs.collect_issues(docs)}
    check("check_docs: a literal backslash-n is caught", "literal-newline" in codes, codes)
    docs_fixture(docs, rules="1. **One**\n3. **Three**")
    codes = {issue["code"] for issue in check_docs.collect_issues(docs)}
    check("check_docs: broken rule numbering is caught", "rules" in codes, codes)
    docs_fixture(docs, extra_script=True)
    codes = {issue["code"] for issue in check_docs.collect_issues(docs)}
    check("check_docs: an undocumented script is caught", "script-doc" in codes, codes)
    docs_fixture(docs, readme_version="1.0.1")
    codes = {issue["code"] for issue in check_docs.collect_issues(docs)}
    check("check_docs: a version mismatch is caught", "version" in codes, codes)
    docs_fixture(docs)
    codes = {issue["code"] for issue in check_docs.collect_issues(docs, 2)}
    check("check_docs: a wrong test count is caught", "test-count" in codes, codes)
    real = check_docs.collect_issues(SKILL)
    check("check_docs: the real repository is consistent", not real, real)

    release_root = os.path.join(work, "release-fixture")
    docs_fixture(release_root)
    write(os.path.join(release_root, "scripts", "selftest_models.py"),
          "print('3 passed, 0 failed')\n")
    with open(os.path.join(release_root, "SKILL.md"), "a", encoding="utf-8") as fh:
        fh.write("\n`python scripts/selftest_models.py`\n")
    with open(os.path.join(release_root, "README.md"), "a", encoding="utf-8") as fh:
        fh.write("\n`selftest_models.py`\n")
    git = subprocess.run(["git", "init"], cwd=release_root, capture_output=True, text=True)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=release_root,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"],
                   cwd=release_root, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=release_root,
                   capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=release_root, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=release_root,
                   capture_output=True)
    before = open(os.path.join(release_root, "README.md"), encoding="utf-8").read()
    code, out, err = script(release_root, "release.py",
                            ["--version", "1.1.0", "--note", "test release",
                             "--dry-run"])
    after = open(os.path.join(release_root, "README.md"), encoding="utf-8").read()
    check("release: dry-run changes nothing", code == 0 and before == after, out + err)
    code, out, err = script(release_root, "release.py",
                            ["--version", "1.1.0", "--note", "test release",
                             "--no-push"])
    readme_after = open(os.path.join(release_root, "README.md"), encoding="utf-8").read()
    tagged = subprocess.run(["git", "tag", "--list", "v1.1.0"], cwd=release_root,
                            capture_output=True, text=True).stdout.strip()
    check("release: one command updates version/count and tags", code == 0
          and "1.1.0" in readme_after and "3 项离线自测" in readme_after
          and tagged == "v1.1.0", out + err)
    with open(os.path.join(release_root, "README.md"), "a", encoding="utf-8") as fh:
        fh.write("dirty\n")
    code, out, err = script(release_root, "release.py",
                            ["--version", "1.1.1", "--note", "x", "--no-push"])
    check("release: a dirty tree is refused", code == 1, out + err)
    subprocess.run(["git", "restore", "--source=HEAD", "--", "README.md"],
                   cwd=release_root, capture_output=True)
    subprocess.run(["git", "tag", "v1.2.0"], cwd=release_root, capture_output=True)
    code, out, err = script(release_root, "release.py",
                            ["--version", "1.2.0", "--note", "x", "--no-push"])
    check("release: an existing tag is refused", code == 1, out + err)

    # Commit failure: docs and index restored, HEAD unchanged.
    head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=release_root,
                                 capture_output=True, text=True).stdout.strip()
    hook = os.path.join(release_root, ".git", "hooks", "pre-commit")
    write(hook, "#!/bin/sh\nexit 1\n")
    try:
        os.chmod(hook, 0o755)
    except OSError:
        pass
    code, out, err = script(release_root, "release.py",
                            ["--version", "1.3.0", "--note", "x", "--no-push"])
    status = subprocess.run(["git", "status", "--porcelain"], cwd=release_root,
                            capture_output=True, text=True).stdout.strip()
    head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=release_root,
                                capture_output=True, text=True).stdout.strip()
    check("release: a failed commit restores docs and index", code == 1
          and head_before == head_after and status == "", out + err + status)
    os.remove(hook)

    # Tag creation failure keeps the release commit.
    ref_hook = os.path.join(release_root, ".git", "hooks", "reference-transaction")
    write(ref_hook, "#!/bin/sh\n[ \"$1\" = \"prepared\" ] || exit 0\n"
                    "while read old new ref; do case \"$ref\" in "
                    "refs/tags/v1.4.0) exit 1 ;; esac; done\nexit 0\n")
    try:
        os.chmod(ref_hook, 0o755)
    except OSError:
        pass
    code, out, err = script(release_root, "release.py",
                            ["--version", "1.4.0", "--note", "x", "--no-push"])
    tag_missing = subprocess.run(["git", "tag", "--list", "v1.4.0"], cwd=release_root,
                                 capture_output=True, text=True).stdout.strip() == ""
    check("release: a tag failure keeps the commit and reports recovery", code == 1
          and tag_missing and "Recover with: git tag" in (out + err), out + err)
    tag_state = subprocess.run(["git", "status", "--porcelain"], cwd=release_root,
                               capture_output=True, text=True).stdout.strip()
    head_state = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=release_root,
                                capture_output=True, text=True).stdout.strip()
    check("release: a tag failure leaves a clean committed tree", tag_state == ""
          and head_state == "release: v1.4.0", tag_state + head_state)
    os.remove(ref_hook)

    # Branch-push failure keeps the local commit and tag.
    remote = os.path.join(work, "release-remote.git")
    subprocess.run(["git", "init", "--bare", remote], capture_output=True, text=True)
    subprocess.run(["git", "remote", "add", "origin", remote], cwd=release_root,
                   capture_output=True)
    subprocess.run(["git", "config", "branch.master.remote", "origin"],
                   cwd=release_root, capture_output=True)
    subprocess.run(["git", "config", "branch.master.merge", "refs/heads/master"],
                   cwd=release_root, capture_output=True)
    reject = os.path.join(remote, "hooks", "pre-receive")
    write(reject, "#!/bin/sh\nexit 1\n")
    try:
        os.chmod(reject, 0o755)
    except OSError:
        pass
    code, out, err = script(release_root, "release.py",
                            ["--version", "1.5.0", "--note", "x"])
    local_tag = subprocess.run(["git", "tag", "--list", "v1.5.0"], cwd=release_root,
                               capture_output=True, text=True).stdout.strip()
    check("release: a branch-push failure keeps the local commit and tag", code == 1
          and local_tag == "v1.5.0" and "Recover with: git push" in (out + err),
          out + err)

    # Branch accepted, tag rejected on a fresh remote: the local tag survives.
    remote2 = os.path.join(work, "release-remote2.git")
    subprocess.run(["git", "init", "--bare", remote2], capture_output=True, text=True)
    reject2 = os.path.join(remote2, "hooks", "pre-receive")
    write(reject2, "#!/bin/sh\nwhile read old new ref; do case \"$ref\" in "
                   "refs/tags/*) exit 1 ;; esac; done\nexit 0\n")
    try:
        os.chmod(reject2, 0o755)
    except OSError:
        pass
    subprocess.run(["git", "remote", "set-url", "origin", remote2], cwd=release_root,
                   capture_output=True)
    code, out, err = script(release_root, "release.py",
                            ["--version", "1.6.0", "--note", "x"])
    remote_tag = subprocess.run(["git", "--git-dir", remote2, "tag", "--list", "v1.6.0"],
                                capture_output=True, text=True).stdout.strip()
    local_tag = subprocess.run(["git", "tag", "--list", "v1.6.0"], cwd=release_root,
                               capture_output=True, text=True).stdout.strip()
    check("release: a tag-push failure keeps the local tag", code == 1
          and remote_tag == "" and local_tag == "v1.6.0"
          and "Recover with: git push origin v1.6.0" in (out + err), out + err)


if __name__ == "__main__":
    sys.exit(main())
