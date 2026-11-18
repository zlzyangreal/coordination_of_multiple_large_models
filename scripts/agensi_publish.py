#!/usr/bin/env python3
"""Publish this skill package to Agensi.io, syncing minor/major versions only.

Agensi (https://www.agensi.io) lists SKILL.md packages and serves them to agents over an MCP
connector. Its creator tools (submit_skill / update_skill / my_skills) are MCP tools reachable only
with an OAuth token - there is no API key. This script is the whole client: OAuth 2.1 + PKCE with
dynamic client registration, a minimal Streamable-HTTP MCP client, the package builder, and the
version policy.

Version policy (the reason this script exists)
    - major or minor change (3.2.0 -> 3.3.0)   -> publish an update
    - patch-only change   (3.3.0 -> 3.3.1)     -> do not publish
    - same version                              -> nothing to do
    - lower version                             -> refuse
    `--force` overrides the patch rule for one run; `check` never writes anything.

Commands
    login     one-time OAuth: opens the Agensi sign-in page, stores a refreshable token
    status    creator mode, listings and their review state
    check     print the publish decision for the current version (no network, no writes)
    package   build the upload zip for a manual submission and print its hash
    sync      decide, then submit (first listing) or update (new minor/major), with --yes

State lives in agensi/publish-state.json (committed, so every machine agrees on what was shipped).
The token lives in _work/agensi/token.json (gitignored, chmod 600) - never commit it.

Examples
    python scripts/agensi_publish.py check
    python scripts/agensi_publish.py login
    python scripts/agensi_publish.py sync --yes
    python scripts/agensi_publish.py sync --version 3.3.0 --dry-run

Exit codes
    0  ok, skipped or previewed
    2  usage, package or state error
    3  sign-in required (or refresh failed)
    4  Agensi/MCP error
    5  version policy refused the publish
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

DEFAULT_ENDPOINT = "https://mcp.agensi.io/mcp/auth"
DEFAULT_SCOPE = "read install"
PROTOCOL_VERSION = "2025-06-18"
USER_AGENT = "dsh-agensi-publisher/1.0"
CALLBACK_PATH = "/callback"
MAX_FILES = 100
MAX_BYTES = 5 * 1024 * 1024
TEXT_SUFFIXES = {
    ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".csv", ".tsv",
    ".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".ps1", ".bat", ".html", ".css", ".svg",
}
SKIP_PARTS = {".git", "__pycache__", ".DS_Store", "node_modules", ".venv"}


class PublishError(RuntimeError):
    """A package, state or protocol error that should stop the run."""


class AuthRequired(PublishError):
    """The token is missing, expired and unrefreshable, or rejected."""


# ----------------------------------------------------------------------------- versions

def parse_version(text: str) -> tuple[int, int, int]:
    raw = str(text).strip().lstrip("v")
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?$", raw)
    if not match:
        raise PublishError(f"not a version number: {text!r} (expected MAJOR.MINOR[.PATCH])")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def decide(last: str | None, current: str) -> dict:
    """The version policy. Pure: no files, no network."""
    cur = parse_version(current)
    if last is None or not str(last).strip():
        return {"action": "submit", "last": None, "current": current,
                "reason": "no published version recorded - first listing"}
    prev = parse_version(last)
    if cur == prev:
        return {"action": "none", "last": last, "current": current,
                "reason": f"{current} is already published"}
    if cur < prev:
        return {"action": "refuse", "last": last, "current": current,
                "reason": f"current {current} is lower than published {last}"}
    if cur[:2] != prev[:2]:
        return {"action": "update", "last": last, "current": current,
                "reason": f"minor/major change {last} -> {current}"}
    return {"action": "skip", "last": last, "current": current,
            "reason": f"patch-only change {last} -> {current} (policy: minor/major only)"}


# ----------------------------------------------------------------------------- files

def _read_text(path: Path) -> str:
    data = path.read_bytes()
    if b"\x00" in data:
        raise PublishError(f"binary file in the package: {path.name}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PublishError(f"not UTF-8: {path.name} ({exc})") from exc


def parse_front_matter(text: str) -> tuple[dict, str]:
    """Minimal YAML front matter parser: scalars and `- item` lists. Enough for SKILL.md."""
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block = text[3:end].strip("\r\n")
    body = text[end + 4:]
    meta: dict = {}
    key = None
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^\s+-\s+", line) and key:
            value = line.split("-", 1)[1].strip().strip('"\'')
            if not isinstance(meta.get(key), list):
                meta[key] = []
            meta[key].append(value)
            continue
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if match:
            key = match.group(1)
            value = match.group(2).strip()
            if value.startswith("[") and value.endswith("]"):
                meta[key] = [v.strip().strip('"\'') for v in value[1:-1].split(",") if v.strip()]
            elif value:
                meta[key] = value.strip('"\'')
            else:
                meta[key] = None
    return meta, body


def set_front_matter_version(text: str, version: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    head = text[:end]
    if re.search(r"^version:\s*.*$", head, re.M):
        head = re.sub(r"^version:\s*.*$", f"version: {version}", head, count=1, flags=re.M)
    else:
        head = head.rstrip("\r\n") + f"\nversion: {version}"
    return head + text[end:]


def build_package(root: Path, version: str) -> dict:
    """Collect the Agensi skill folder into [{path, content}] with the release version stamped in."""
    skill_dir = root / "agensi" / "skill"
    if not (skill_dir / "SKILL.md").is_file():
        raise PublishError(f"missing {skill_dir / 'SKILL.md'}")
    files: list[dict] = []
    total = 0
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(skill_dir)
        if any(part in SKIP_PARTS for part in rel.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            raise PublishError(f"not a text file Agensi accepts: {rel.as_posix()}")
        content = _read_text(path)
        if rel.as_posix() == "SKILL.md":
            content = set_front_matter_version(content, version)
        total += len(content.encode("utf-8"))
        files.append({"path": rel.as_posix(), "content": content})
    if len(files) > MAX_FILES:
        raise PublishError(f"{len(files)} files; Agensi accepts at most {MAX_FILES}")
    if total > MAX_BYTES:
        raise PublishError(f"{total} bytes; Agensi accepts at most {MAX_BYTES} from an agent")
    digest = hashlib.sha256()
    for item in files:
        digest.update(item["path"].encode())
        digest.update(b"\0")
        digest.update(item["content"].encode())
    skill = next(item for item in files if item["path"] == "SKILL.md")
    meta, _ = parse_front_matter(skill["content"])
    return {"files": files, "bytes": total, "hash": digest.hexdigest(), "meta": meta}


def load_listing(root: Path, version: str, meta: dict) -> dict:
    path = root / "agensi" / "listing.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PublishError(f"missing {path}") from exc
    except json.JSONDecodeError as exc:
        raise PublishError(f"{path.name} is not valid JSON: {exc}") from exc
    listing = dict(data.get("listing") or {})
    name = listing.get("name") or meta.get("name")
    if not name:
        raise PublishError("listing has no name")
    summary = listing.get("summary") or meta.get("summary") or meta.get("description")
    if not summary:
        raise PublishError("listing has no summary")
    if len(summary) > 300:
        raise PublishError(f"summary is {len(summary)} chars; Agensi allows 300")
    listing["name"] = name
    listing["summary"] = summary
    listing["version"] = version
    listing.setdefault("pricing_type", "free")
    for key, limit in (("tags", 10), ("keywords", 10), ("use_cases", 8)):
        values = listing.get(key) or []
        if len(values) > limit:
            raise PublishError(f"{key}: {len(values)} items; Agensi allows {limit}")
        listing[key] = values
    return listing


def changelog_for(root: Path, version: str) -> str:
    path = root / "CHANGELOG.md"
    if not path.is_file():
        return f"Release {version}."
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(rf"^##\s+{re.escape(version)}\b[^\n]*\n(.*?)(?=^##\s|\Z)",
                      text, re.M | re.S)
    if not match:
        return f"Release {version}."
    body = match.group(1).strip()
    return (body[:1997] + "...") if len(body) > 2000 else body


def build_zip(root: Path, version: str) -> Path:
    import zipfile
    pkg = build_package(root, version)
    out_dir = root / "_work" / "agensi"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", (pkg["meta"].get("name") or "skill").lower()).strip("-")
    out = out_dir / f"{slug}-{version}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in pkg["files"]:
            zf.writestr(item["path"], item["content"])
    return out


# ----------------------------------------------------------------------------- state / token

def state_path(root: Path) -> Path:
    return root / "agensi" / "publish-state.json"


def load_state(root: Path) -> dict:
    path = state_path(root)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PublishError(f"{path.name} is corrupt: {exc}") from exc


def save_state(root: Path, state: dict) -> None:
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def token_path(root: Path) -> Path:
    override = os.environ.get("AGENSI_TOKEN_FILE")
    return Path(override) if override else root / "_work" / "agensi" / "token.json"


def load_token(root: Path) -> dict | None:
    path = token_path(root)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save_token(root: Path, token: dict) -> None:
    path = token_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(token, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ----------------------------------------------------------------------------- http

def _request(url: str, *, data: bytes | None = None, headers: dict | None = None,
             method: str | None = None, timeout: int = 30) -> tuple[int, dict, str]:
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", USER_AGENT)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        raise PublishError(f"network error for {url}: {exc.reason}") from exc


def _json_body(text: str):
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def get_json(url: str) -> dict:
    status, _, body = _request(url, headers={"Accept": "application/json"})
    data = _json_body(body)
    if status != 200 or data is None:
        raise PublishError(f"GET {url} -> HTTP {status}: {body[:200]}")
    return data


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


# ----------------------------------------------------------------------------- OAuth

def discover(issuer: str | None = None) -> dict:
    """Resource metadata -> authorization server metadata. Falls back to the documented hosts."""
    servers: list[str] = []
    try:
        resource = get_json("https://mcp.agensi.io/.well-known/oauth-protected-resource")
        servers = list(resource.get("authorization_servers") or [])
    except PublishError:
        pass
    if issuer:
        servers.insert(0, issuer)
    if not servers:
        servers = ["https://www.agensi.io", "https://mcp.agensi.io"]
    last_error = None
    for base in servers:
        base = base.rstrip("/")
        for suffix in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
            try:
                data = get_json(base + suffix)
            except PublishError as exc:
                last_error = exc
                continue
            if data.get("authorization_endpoint") and data.get("token_endpoint"):
                data["_issuer"] = base
                return data
    raise PublishError(f"OAuth discovery failed: {last_error}")


def register_client(metadata: dict, redirect_uri: str, scope: str) -> dict:
    endpoint = metadata.get("registration_endpoint")
    if not endpoint:
        raise PublishError("the authorization server exposes no registration_endpoint")
    payload = {
        "client_name": "Multi-Model Coordination publisher (DSH)",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": scope,
    }
    body = json.dumps(payload).encode()
    status, _, text = _request(endpoint, data=body, method="POST",
                               headers={"Content-Type": "application/json",
                                        "Accept": "application/json"})
    data = _json_body(text)
    if status not in (200, 201) or not data:
        raise PublishError(f"client registration failed: HTTP {status} {text[:300]}")
    data.setdefault("client_id_issued_at", int(time.time()))
    return data


def _callback_server(port: int = 0):
    result: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != CALLBACK_PATH:
                self.send_response(404)
                self.end_headers()
                return
            params = urllib.parse.parse_qs(parsed.query)
            result.update({k: v[0] for k, v in params.items()})
            body = (b"<html><body style='font-family:sans-serif;padding:2rem'>"
                    b"<h2>Agensi sign-in captured</h2>"
                    b"<p>You can close this tab and return to the terminal.</p>"
                    b"</body></html>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 2
    return server, result


def login(root: Path, *, endpoint: str, scope: str, timeout: int = 300,
          open_browser: bool = True) -> dict:
    server, result = _callback_server()
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}{CALLBACK_PATH}"
    try:
        metadata = discover()
        client = register_client(metadata, redirect_uri, scope)
        verifier = b64url(secrets.token_bytes(48))
        challenge = b64url(hashlib.sha256(verifier.encode()).digest())
        state_value = b64url(secrets.token_bytes(16))
        resource = endpoint[:-5] if endpoint.endswith("/auth") else endpoint
        params = {
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state_value,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": resource,
        }
        auth_url = metadata["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)
        print("Agensi sign-in required. Open this URL and approve with zlzyangreal@gmail.com:\n")
        print(auth_url + "\n")
        print(f"Waiting for the callback on {redirect_uri} (up to {timeout}s)...")
        if open_browser:
            try:
                webbrowser.open(auth_url)
            except Exception:
                pass
        deadline = time.time() + timeout
        while time.time() < deadline and "code" not in result and "error" not in result:
            server.handle_request()
        server.server_close()
        if "error" in result:
            raise AuthRequired(f"authorization denied: {result.get('error')} "
                               f"{result.get('error_description', '')}".strip())
        if "code" not in result:
            raise AuthRequired("no authorization code received (timed out)")
        if result.get("state") != state_value:
            raise AuthRequired("state mismatch on the callback - refusing the code")
        payload = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": redirect_uri,
            "client_id": client["client_id"],
            "code_verifier": verifier,
            "resource": params["resource"],
        }).encode()
        status, _, text = _request(metadata["token_endpoint"], data=payload, method="POST",
                                   headers={"Content-Type": "application/x-www-form-urlencoded",
                                            "Accept": "application/json"})
        data = _json_body(text)
        if status != 200 or not data or "access_token" not in data:
            raise AuthRequired(f"token exchange failed: HTTP {status} {text[:300]}")
        token = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "token_type": data.get("token_type", "Bearer"),
            "scope": data.get("scope", scope),
            "expires_at": int(time.time()) + int(data.get("expires_in", 3600)) - 60,
            "client_id": client["client_id"],
            "token_endpoint": metadata["token_endpoint"],
            "resource": params["resource"],
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        save_token(root, token)
        print(f"Token stored in {token_path(root)} (valid ~1h, refreshable for 30 days).")
        return token
    finally:
        try:
            server.server_close()
        except Exception:
            pass


def refresh(root: Path, token: dict) -> dict:
    if not token.get("refresh_token") or not token.get("token_endpoint"):
        raise AuthRequired("no refresh token; run `agensi_publish.py login` again")
    payload = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"],
        "client_id": token.get("client_id", ""),
    }).encode()
    status, _, text = _request(token["token_endpoint"], data=payload, method="POST",
                               headers={"Content-Type": "application/x-www-form-urlencoded",
                                        "Accept": "application/json"})
    data = _json_body(text)
    if status != 200 or not data or "access_token" not in data:
        raise AuthRequired(f"token refresh failed: HTTP {status} {text[:200]}")
    token.update({
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", token.get("refresh_token")),
        "expires_at": int(time.time()) + int(data.get("expires_in", 3600)) - 60,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })
    save_token(root, token)
    return token


def usable_token(root: Path) -> str:
    token = load_token(root)
    if not token:
        raise AuthRequired("not signed in; run `agensi_publish.py login`")
    if int(token.get("expires_at", 0)) <= int(time.time()):
        token = refresh(root, token)
    return token["access_token"]


# ----------------------------------------------------------------------------- MCP client

def _decode_messages(text: str) -> list[dict]:
    if not text.strip():
        return []
    if text.lstrip().startswith("{"):
        data = _json_body(text)
        return [data] if isinstance(data, dict) else []
    messages: list[dict] = []
    for block in re.split(r"\r?\n\r?\n", text):
        data_lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if not data_lines:
            continue
        data = _json_body("\n".join(data_lines))
        if isinstance(data, dict):
            messages.append(data)
    return messages


class McpClient:
    def __init__(self, endpoint: str, token: str):
        self.endpoint = endpoint
        self.token = token
        self.session_id: str | None = None
        self._next_id = 0

    def _post(self, payload: dict) -> tuple[int, dict, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "Authorization": f"Bearer {self.token}",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        status, response_headers, text = _request(self.endpoint, data=json.dumps(payload).encode(),
                                                  method="POST", headers=headers)
        for key, value in response_headers.items():
            if key.lower() == "mcp-session-id":
                self.session_id = value
        return status, response_headers, text

    def initialize(self) -> dict:
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": "initialize",
                   "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                              "clientInfo": {"name": "dsh-agensi-publisher",
                                             "version": "1.0.0"}}}
        status, headers, text = self._post(payload)
        if status == 401:
            raise AuthRequired(_auth_hint(headers, text))
        message = next((m for m in _decode_messages(text) if m.get("id") == self._next_id), None)
        if message is None:
            raise PublishError(f"initialize failed: HTTP {status} {text[:300]}")
        if "error" in message:
            raise PublishError(f"initialize error: {message['error']}")
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        except PublishError:
            pass
        return message.get("result", {})

    def call(self, name: str, arguments: dict) -> dict:
        self._next_id += 1
        request_id = self._next_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                   "params": {"name": name, "arguments": arguments}}
        status, headers, text = self._post(payload)
        if status == 401:
            raise AuthRequired(_auth_hint(headers, text))
        message = next((m for m in _decode_messages(text) if m.get("id") == request_id), None)
        if message is None:
            raise PublishError(f"{name}: unreadable response (HTTP {status}) {text[:300]}")
        if "error" in message:
            raise PublishError(f"{name}: {json.dumps(message['error'])[:500]}")
        result = message.get("result", {})
        text_parts = [item.get("text", "") for item in (result.get("content") or [])
                      if item.get("type") == "text"]
        body = "\n".join(part for part in text_parts if part).strip()
        if result.get("isError"):
            raise PublishError(f"{name} failed: {body[:800]}")
        parsed = _json_body(body)
        return {"text": body, "data": parsed if parsed is not None else body}


def _auth_hint(headers: dict, text: str) -> str:
    challenge = next((v for k, v in headers.items() if k.lower() == "www-authenticate"), "")
    return f"Agensi rejected the token ({challenge or text[:120]}). Run `agensi_publish.py login`."


# ----------------------------------------------------------------------------- commands

def cmd_check(root: Path, args) -> int:
    version = args.version or read_package_version(root)
    pkg = build_package(root, version)
    listing = load_listing(root, version, pkg["meta"])
    state = load_state(root)
    decision = decide(state.get("version"), version)
    if args.force and decision["action"] == "skip":
        decision = {"action": "update", "last": state.get("version"), "current": version,
                    "reason": f"patch sync forced ({decision['reason']})"}
    payload = {
        "version": version,
        "published_version": state.get("version"),
        "action": decision["action"],
        "reason": decision["reason"],
        "slug": state.get("slug"),
        "files": len(pkg["files"]),
        "bytes": pkg["bytes"],
        "package_hash": pkg["hash"],
        "listing_name": listing["name"],
        "pricing_type": listing["pricing_type"],
        "front_matter": {k: pkg["meta"].get(k) for k in ("name", "version", "summary")},
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"version        {version} (published: {state.get('version') or 'never'})")
        print(f"package        {len(pkg['files'])} files, {pkg['bytes']} bytes, sha256 {pkg['hash'][:16]}")
        print(f"listing        {listing['name']} ({listing['pricing_type']})")
        print(f"decision       {decision['action']}: {decision['reason']}")
    return 5 if decision["action"] == "refuse" else 0


def cmd_package(root: Path, args) -> int:
    version = args.version or read_package_version(root)
    out = build_zip(root, version)
    data = out.read_bytes()
    print(f"{out} ({len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()[:16]})")
    return 0


def connect(root: Path, endpoint: str) -> McpClient:
    client = McpClient(endpoint, usable_token(root))
    client.initialize()
    return client


def cmd_login(root: Path, args) -> int:
    try:
        login(root, endpoint=args.endpoint, scope=args.scope, timeout=args.timeout,
              open_browser=not args.no_browser)
    except AuthRequired as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 3
    try:
        client = connect(root, args.endpoint)
        info = client.call("creator_status", {})
        print("creator_status: " + (info["text"] or "(empty)"))
    except PublishError as exc:
        print(f"[WARN] signed in, but creator_status failed: {exc}", file=sys.stderr)
    return 0


def cmd_status(root: Path, args) -> int:
    client = connect(root, args.endpoint)
    for tool in ("creator_status", "my_skills"):
        result = client.call(tool, {})
        print(f"== {tool} ==\n{result['text']}\n")
    return 0


def cmd_sync(root: Path, args) -> int:
    version = args.version or read_package_version(root)
    pkg = build_package(root, version)
    listing = load_listing(root, version, pkg["meta"])
    state = load_state(root)
    decision = decide(state.get("version"), version)
    if args.force and decision["action"] == "skip":
        decision = {"action": "update", "last": state.get("version"), "current": version,
                    "reason": f"patch sync forced ({decision['reason']})"}
    print(f"[{decision['action']}] {decision['reason']}")

    if decision["action"] in ("none", "skip"):
        return 0
    if decision["action"] == "refuse":
        print("[FAIL] refusing to publish a lower version than the live listing", file=sys.stderr)
        return 5

    fields = {k: listing[k] for k in (
        "name", "summary", "description", "pricing_type", "price",
        "subscription_price_monthly", "annual_billing", "annual_discount_percent",
        "tags", "keywords", "use_cases", "requirements", "optional_requirements",
        "requirement_note", "nothing_needed", "permissions", "sample_input", "sample_output",
        "docs_url", "usage_example", "faq", "support_method", "known_limitations",
        "compatibility_note", "agensi_tools") if k in listing}
    # Agensi defaults a new listing to 1.0; passing the release version keeps the market page in
    # step with the repository from the first submission (undocumented but accepted field).
    fields["version"] = version

    if args.dry_run or not args.yes:
        print(f"would {'submit a new listing' if decision['action'] == 'submit' else 'update ' + str(state.get('slug'))}")
        print(f"  files   {len(pkg['files'])} ({', '.join(f['path'] for f in pkg['files'][:5])}"
              f"{'...' if len(pkg['files']) > 5 else ''})")
        print(f"  bytes   {pkg['bytes']}")
        print(f"  listing {listing['name']} - {listing['pricing_type']}")
        print("(no --yes: nothing was sent)")
        return 0

    client = connect(root, args.endpoint)
    creator = client.call("creator_status", {})
    print("creator_status: " + (creator["text"] or "(empty)"))

    if decision["action"] == "submit":
        preview = client.call("submit_skill", {**fields, "files": pkg["files"]})
        print(preview["text"])
        draft = _extract(preview, ("draft_id", "draftId"))
        if args.preview:
            print("(preview only; re-run without --preview to publish)")
            return 0
        if not draft:
            raise PublishError("submit_skill returned no draft_id; cannot confirm")
        final = client.call("submit_skill", {"draft_id": draft, "confirm": True})
        print(final["text"])
        slug = _extract(final, ("slug", "skill_slug")) or _find_slug(client, listing["name"])
        action = "submitted"
    else:
        slug = state.get("slug") or args.slug
        if not slug:
            slug = _find_slug(client, listing["name"])
        if not slug:
            raise PublishError("no slug recorded and none found in my_skills; pass --slug")
        changelog = changelog_for(root, version)
        final = client.call("update_skill", {"skill_slug": slug, "version": version,
                                             "changelog": changelog, "files": pkg["files"]})
        print(final["text"])
        action = "updated"

    state = {
        "schema": 1,
        "slug": slug,
        "name": listing["name"],
        "version": version,
        "package_hash": pkg["hash"],
        "published_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "action": action,
    }
    save_state(root, state)
    print(f"[OK] {action} {slug} -> {version}; state written to {state_path(root)}")
    return 0


def _extract(result: dict, keys: tuple[str, ...]) -> str | None:
    data = result.get("data")
    if isinstance(data, dict):
        for key in keys:
            if data.get(key):
                return str(data[key])
    text = result.get("text") or ""
    for key in keys:
        match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', text)
        if match:
            return match.group(1)
    return None


def _find_slug(client: McpClient, name: str) -> str | None:
    try:
        result = client.call("my_skills", {})
    except PublishError:
        return None
    data = result.get("data")
    entries = data if isinstance(data, list) else (data.get("skills") if isinstance(data, dict) else None)
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and str(entry.get("name", "")).lower() == name.lower():
                return entry.get("slug") or entry.get("skill_slug")
    text = result.get("text") or ""
    match = re.search(rf"{re.escape(name)}[^\n]*?slug[\"']?\s*[:=]\s*[\"']?([a-z0-9-]+)", text, re.I)
    return match.group(1) if match else None


# ----------------------------------------------------------------------------- cli

def read_package_version(root: Path) -> str:
    path = root / "package.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PublishError(f"missing {path}") from exc
    version = data.get("version")
    if not version:
        raise PublishError(f"{path.name} has no version")
    return str(version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agensi_publish.py",
        description="Publish this skill to Agensi.io; sync minor/major versions only.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent),
                        help="repository root (default: the parent of scripts/)")
    parser.add_argument("--endpoint", default=os.environ.get("AGENSI_MCP_ENDPOINT", DEFAULT_ENDPOINT),
                        help="MCP endpoint (default: the sign-in-first endpoint)")
    parser.add_argument("--version", help="version to publish (default: package.json)")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        # Accept --root/--endpoint/--version after the subcommand too; SUPPRESS keeps the value the
        # top-level parser already parsed instead of overwriting it with None.
        p.add_argument("--root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        p.add_argument("--endpoint", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        p.add_argument("--version", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    p_check = sub.add_parser("check", help="print the publish decision; no network, no writes")
    add_common(p_check)
    p_check.add_argument("--json", action="store_true")
    p_check.add_argument("--force", action="store_true", help="pretend a patch bumps publish")

    p_pkg = sub.add_parser("package", help="build the upload zip for a manual submission")
    add_common(p_pkg)

    p_login = sub.add_parser("login", help="OAuth sign-in (opens the browser once)")
    add_common(p_login)
    p_login.add_argument("--scope", default=DEFAULT_SCOPE)
    p_login.add_argument("--timeout", type=int, default=300)
    p_login.add_argument("--no-browser", action="store_true")

    p_status = sub.add_parser("status", help="creator status and listings")
    add_common(p_status)

    p_sync = sub.add_parser("sync", help="submit or update the listing (needs --yes)")
    add_common(p_sync)
    p_sync.add_argument("--yes", action="store_true", help="actually publish")
    p_sync.add_argument("--dry-run", action="store_true", help="show the payload only")
    p_sync.add_argument("--preview", action="store_true", help="submit preview, do not confirm")
    p_sync.add_argument("--force", action="store_true", help="allow a patch-only sync")
    p_sync.add_argument("--slug", help="existing listing slug (skips discovery)")
    p_sync.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        if args.command == "check":
            return cmd_check(root, args)
        if args.command == "package":
            return cmd_package(root, args)
        if args.command == "login":
            return cmd_login(root, args)
        if args.command == "status":
            return cmd_status(root, args)
        if args.command == "sync":
            return cmd_sync(root, args)
    except AuthRequired as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 3
    except PublishError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
