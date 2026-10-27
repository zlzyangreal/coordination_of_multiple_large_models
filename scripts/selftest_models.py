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
import json
import os
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

    code, out, err = run(project, ["call", "--model", "mock_a/mock-ok",
                                   "--prompt", "hi", "--json"], env)
    result = json.loads(out) if code == 0 else {}
    check("a keyless completion succeeds",
          code == 0 and "hello from mock A" in result.get("text", ""), out + err)
    calls_path = os.path.join(project, "_work", "models", "model_calls.jsonl")
    check("every attempt is logged", os.path.isfile(calls_path)
          and "mock_a/mock-ok" in open(calls_path, encoding="utf-8").read())

    code, out, err = run(project, ["call", "--chain", "r429", "--prompt", "hi", "--json"], env)
    result = json.loads(out) if code == 0 else {}
    attempts = result.get("attempts") or []
    check("a 429 falls over to the next chain link",
          code == 0 and "hello from mock A" in result.get("text", "")
          and attempts and attempts[0].get("kind") == "rate_limit", out + err)

    code, out, err = run(project, ["call", "--chain", "bad400", "--prompt", "hi", "--json"], env)
    check("a 400 is a request defect and does not fall over", code == 4, out + err)

    config_path = os.path.join(project, "_work", "models", "config.json")
    config = json.load(open(config_path, encoding="utf-8"))
    config["defaults"]["retryTransient"] = 2
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)
    code, out, err = run(project, ["call", "--model", "mock_a/mock-flaky",
                                   "--prompt", "hi", "--json"], env)
    result = json.loads(out) if code == 0 else {}
    attempts = result.get("attempts") or []
    check("a transient 500 is retried before the link is dropped",
          code == 0 and len(attempts) == 3
          and "flaky recovered" in result.get("text", ""), out + err)

    code, out, err = run(project, ["call", "--model", "mock_b/claude-mock",
                                   "--prompt", "hi", "--json"], env)
    result = json.loads(out) if code == 0 else {}
    check("the Anthropic Messages wire works",
          code == 0 and result.get("text") == "anthropic ok", out + err)

    code, out, err = run(project, ["call", "--model", "mock_c/resp-mock",
                                   "--prompt", "hi", "--json"], env)
    result = json.loads(out) if code == 0 else {}
    check("the OpenAI Responses wire works",
          code == 0 and result.get("text") == "responses ok", out + err)

    code, out, err = run(project, ["call", "--model", "mock_leak/claude-mock",
                                   "--prompt", "hi"], env)
    state_text = open(os.path.join(project, "_work", "models", "state.json"),
                      encoding="utf-8").read()
    check("an echoed credential is redacted everywhere",
          code == 5 and "sk-leak-SECRET-9999" not in (out + err + state_text),
          out + err)

    code, out, err = run(project, ["call", "--model", "dsh:auto", "--prompt", "hi"], env)
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
    code, out, err = run(project, ["call", "--model", "mock_c/resp-mock",
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
    code, out, err = run(project, ["call", "--role", "produce", "--prompt", "hi",
                                   "--dry-run"], env)
    check("call --role produce reaches the planner without a traceback",
          code == 0 and "dry-run" in out.lower(), out + err)

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
    code, out, err = run(project, ["call", "--model", "mock_echo/echo-key",
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
    code, out, err = run(project, ["route", "--role", "lead", "--pin", "nope/nope",
                                   "--no-quota"], env)
    check("an unknown pin fails instead of substituting another model",
          code == 3 and "not in this project's catalog" in (out + err), out + err)
    code, out, err = run(project, ["route", "--role", "review", "--pin",
                                   "agg_openai_locked/gpt-4o", "--no-quota"], env)
    check("a pinned protected-family row is refused", code == 3, out + err)

    # R1-MODEL-COOLDOWN: a 429 cools that model only, and route sees it.
    run(project, ["call", "--chain", "r429", "--prompt", "hi"], env)
    code, out, err = run(project, ["list", "--json"], env)
    entries = {entry["id"]: entry for entry in json.loads(out)} if code == 0 else {}
    check("a model-level cooldown marks the model unusable",
          entries.get("mock_a/mock-429", {}).get("usable") is False
          and "cooling" in str(entries["mock_a/mock-429"].get("reason")), out + err)
    check("the sibling model stays usable after a model-scoped 429",
          entries.get("mock_a/mock-ok", {}).get("usable") is True, out + err)

    # R1-HTTP-404: a wrong endpoint is a request defect, not a model miss.
    code, out, err = run(project, ["call", "--model", "mock_404/x", "--prompt", "hi",
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
    code, out, err = run(project, ["call", "--model", "mock_cmd/mock-ok",
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
    code, out, err = run(project, ["call", "--model", "mock_a/mock-500", "--prompt", "hi",
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
    code, out, err = run(project, ["call", "--model", "codex:auto", "--prompt", "hi",
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
    code, out, err = run(project, ["call", "--model", "codex:auto", "--prompt", "hi"],
                         dict(env, COORD_RUN_GPT=empty))
    check("an empty codex payload is a failed attempt, not a success", code == 5,
          out + err)
    log_path = os.path.join(project, "_work", "models", "model_changes.log")
    check("model changes are audited",
          os.path.isfile(log_path) and "add-custom" in open(log_path,
                                                            encoding="utf-8").read())

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


if __name__ == "__main__":
    sys.exit(main())
