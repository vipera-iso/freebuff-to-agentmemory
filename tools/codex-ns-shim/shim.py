#!/usr/bin/env python3
"""
codex-ns-shim — flattens OpenAI Codex's proprietary `type: "namespace"` MCP
tool wrappers into plain `type: "function"` tools before forwarding requests
to an OpenAI-compatible upstream.

Why: Codex CLI (see openai/codex#23186) wraps MCP server tools as
    {"type": "namespace", "name": "mcp__<server>__", "tools": [...]}
when using wire_api = "responses". OpenAI's ChatGPT backend unwraps this
server-side, but most strict OpenAI-compatible gateways reject it with:
    tools[N].type: unknown variant `namespace`, expected one of
    `function`, `web_search_preview`, `code_interpreter`, `mcp`
This shim flattens each namespace wrapper into top-level function tools
whose names keep the `mcp__<server>__<tool>` form, so Codex can still
route tool calls back to the right server.

Everything else — including SSE streaming bodies — is passed through
byte-for-byte.

Config via environment variables:
    SHIM_LISTEN_HOST  (default 127.0.0.1)
    SHIM_LISTEN_PORT  (default 8081)
    SHIM_UPSTREAM     (default http://127.0.0.1:8080/openai/v1)
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import requests

LISTEN_HOST = os.environ.get("SHIM_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("SHIM_LISTEN_PORT", "8081"))
UPSTREAM = os.environ.get("SHIM_UPSTREAM", "http://127.0.0.1:8080/openai/v1").rstrip("/")

UPSTREAM_PARTS = urlsplit(UPSTREAM)
if UPSTREAM_PARTS.scheme not in ("http", "https") or not UPSTREAM_PARTS.netloc:
    sys.exit(f"invalid SHIM_UPSTREAM: {UPSTREAM!r}")
# Path prefix of the upstream base URL (e.g. "/openai/v1"). Used to avoid
# doubling the prefix when the client's base_url already contains it.
UPSTREAM_PREFIX = UPSTREAM_PARTS.path.rstrip("/")

SESSION = requests.Session()
SESSION.trust_env = False  # don't inherit proxy env vars for the local hop

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}
# NOTE: Content-Length is deliberately NOT hop-by-hop here: the body is
# forwarded byte-for-byte, so the upstream's framing stays valid and must be
# preserved. Stripping it would leave responses with no length framing and a
# live keep-alive connection, hanging the client until timeout.

STATS = {"requests": 0, "flattened": 0, "namespaces": 0, "reasoning_stripped": 0}
STATS_LOCK = threading.Lock()

# Agnes (và nhiều gateway OpenAI-compatible khác) không có variant `reasoning`
# trong enum ResponseInput → 400 khi Codex gửi lại reasoning items của turn trước
# trong `input`. Set SHIM_KEEP_REASONING=1 để tắt hành vi strip này.
KEEP_REASONING = bool(os.environ.get("SHIM_KEEP_REASONING"))


def strip_reasoning(body):
    """Remove `type: "reasoning"` items from body["input"]. Returns count removed."""
    if KEEP_REASONING or not isinstance(body, dict):
        return 0
    inp = body.get("input")
    if not isinstance(inp, list):
        return 0
    kept = [it for it in inp
            if not (isinstance(it, dict) and it.get("type") == "reasoning")]
    n = len(inp) - len(kept)
    if n:
        body["input"] = kept
        with STATS_LOCK:
            STATS["reasoning_stripped"] += n
        print(f"[shim] stripped {n} reasoning item(s) from input", flush=True)
    return n


def flatten_tools(tools):
    """Flatten namespace-wrapped MCP tools into top-level function tools.

    Returns (new_tools_list, namespaces_seen, functions_created).
    """
    out = []
    n_ns = 0
    n_fn = 0
    for tool in tools:
        if isinstance(tool, dict) and tool.get("type") == "namespace":
            n_ns += 1
            prefix = str(tool.get("name") or "").rstrip("_") or "mcp"
            prefix = f"mcp__{prefix}__" if not prefix.startswith("mcp__") else f"{prefix}__"
            for sub in tool.get("tools") or []:
                if not isinstance(sub, dict):
                    continue
                if sub.get("type") == "function":
                    fn = sub.get("function") if isinstance(sub.get("function"), dict) else sub
                else:
                    # Responses-style function tool: fields live at top level.
                    fn = {k: v for k, v in sub.items() if k != "type"}
                name = fn.get("name") or "unnamed"
                if not str(name).startswith(prefix):
                    name = f"{prefix}{name}"
                out.append({
                    "type": "function",
                    "name": name,
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
                })
                n_fn += 1
        else:
            out.append(tool)
    return out, n_ns, n_fn


def rewrite_body(raw):
    """Rewrite a request body: flatten namespace tools + strip reasoning items.

    Returns (raw, changed). Non-JSON bodies pass through untouched.
    """
    if not raw:
        return raw, False
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return raw, False  # not JSON (e.g. multipart) — pass through
    if not isinstance(body, dict):
        return raw, False

    changed = False
    if isinstance(body.get("tools"), list):
        tools, n_ns, n_fn = flatten_tools(body["tools"])
        if n_ns:
            body["tools"] = tools
            with STATS_LOCK:
                STATS["flattened"] += 1
                STATS["namespaces"] += n_ns
            print(f"[shim] flattened {n_ns} namespace(s) -> {n_fn} function tool(s)", flush=True)
            changed = True

    if strip_reasoning(body):
        changed = True

    if not changed:
        return raw, False
    return json.dumps(body, ensure_ascii=False).encode("utf-8"), True


DEBUG = bool(os.environ.get("SHIM_DEBUG"))
# SHIM_DUMP=<dir>: lưu mỗi request body vào <dir>/NNN-METHOD-path.json để debug payload.
DUMP_DIR = os.environ.get("SHIM_DUMP") or None
_DUMP_SEQ = [0]
_DUMP_LOCK = threading.Lock()


def dump_request(method, path, raw):
    if not DUMP_DIR:
        return
    try:
        os.makedirs(DUMP_DIR, exist_ok=True)
        with _DUMP_LOCK:
            _DUMP_SEQ[0] += 1
            n = _DUMP_SEQ[0]
        safe = "".join(c if c.isalnum() else "_" for c in path)[:40]
        fn = os.path.join(DUMP_DIR, f"{n:03d}-{method}-{safe}.json")
        try:
            body = json.loads(raw) if raw else None
        except (ValueError, UnicodeDecodeError):
            body = raw.decode("utf-8", "replace") if raw else None
        with open(fn, "w") as f:
            json.dump({"method": method, "path": path, "body": body}, f, ensure_ascii=False, indent=1)
        print(f"[shim] dumped request -> {fn}", flush=True)
    except OSError as exc:
        print(f"[shim] dump failed: {exc}", flush=True)


def dbg(msg):
    if DEBUG:
        sys.stderr.write(f"[shim:dbg] {msg}\n")
        sys.stderr.flush()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "codex-ns-shim/1.0"

    def log_message(self, fmt, *args):  # quieter default logging
        if os.environ.get("SHIM_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _proxy(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        dump_request(method, self.path, raw)
        raw, rewritten = rewrite_body(raw)

        path = self.path
        if UPSTREAM_PREFIX and path.startswith(UPSTREAM_PREFIX):
            path = path[len(UPSTREAM_PREFIX):] or "/"
        url = f"{UPSTREAM}{path}"
        drop = HOP_BY_HOP | ({"content-length"} if rewritten else set())
        headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in drop and k.lower() != "host"
        }
        if rewritten:
            headers["Content-Length"] = str(len(raw))
        headers["Host"] = UPSTREAM_PARTS.netloc

        with STATS_LOCK:
            STATS["requests"] += 1

        dbg(f"<- {method} {self.path} ({length}B body, rewritten={rewritten})")
        try:
            resp = SESSION.request(
                method, url, data=raw, headers=headers,
                stream=True, allow_redirects=False, timeout=600,
            )
            dbg(f"-> upstream {resp.status_code}")
        except requests.RequestException as exc:
            msg = json.dumps({"error": {"message": f"shim: upstream error: {exc}"}}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return

        dbg("<- upstream headers: " + str(dict(resp.headers)))
        self.send_response(resp.status_code)
        has_length = False
        for k, v in resp.headers.items():
            lk = k.lower()
            if lk == "content-length":
                has_length = True
            # Server/Date are skipped because send_response() emits fresh ones.
            if lk not in HOP_BY_HOP and lk not in ("server", "date"):
                self.send_header(k, v)
        if not has_length:
            # Chunked/EOF-delimited upstream body (typical for SSE): tell the
            # client the body ends at connection close, since we cannot
            # forward `Transfer-Encoding: chunked` hop-by-hop.
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()

        # Stream the body through in chunks (SSE-safe: flush per chunk).
        dbg("relaying body...")
        try:
            for chunk in resp.iter_content(chunk_size=8192):
                dbg(f"  chunk {len(chunk)}B")
                if chunk:
                    self.wfile.write(chunk)
                    self.wfile.flush()
            dbg("body relayed, closing upstream resp")
        except (BrokenPipeError, ConnectionResetError):
            resp.close()
            return
        resp.close()
        dbg("done")
        if resp.raw and hasattr(resp.raw, "release_conn"):
            resp.raw.release_conn()

    def do_GET(self):
        self._proxy("GET")

    def do_POST(self):
        self._proxy("POST")

    def do_PUT(self):
        self._proxy("PUT")

    def do_PATCH(self):
        self._proxy("PATCH")

    def do_DELETE(self):
        self._proxy("DELETE")

    def do_OPTIONS(self):
        self._proxy("OPTIONS")

    def do_HEAD(self):
        self._proxy("HEAD")

    def do_HEALTH(self):
        with STATS_LOCK:
            body = json.dumps({"ok": True, "upstream": UPSTREAM, **STATS}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    print(f"[shim] listening on http://{LISTEN_HOST}:{LISTEN_PORT} -> {UPSTREAM}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
