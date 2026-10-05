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

Response side: the model only ever sees the FLAT names, so it echoes them
back with no namespace. Codex's registry keys tools by (namespace, name) —
an un-namespaced call misses and dies with `unsupported call: ...` (which
made Codex simulate subagents inline instead of spawning real threads).
So the shim also rewrites `function_call` items in SSE/JSON responses back
into {name, namespace} using a per-request map built while flattening, and
re-flattens namespaced history items in `input` so the upstream always sees
one consistent flat naming. Everything else passes through unchanged.

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

STATS = {"requests": 0, "flattened": 0, "namespaces": 0, "reasoning_stripped": 0,
         "history_flat": 0, "unflattened": 0, "keyed": 0}
STATS_LOCK = threading.Lock()

# Agnes (và nhiều gateway OpenAI-compatible khác) không có variant `reasoning`
# trong enum ResponseInput → 400 khi Codex gửi lại reasoning items của turn trước
# trong `input`. Set SHIM_KEEP_REASONING=1 để tắt hành vi strip này.
KEEP_REASONING = bool(os.environ.get("SHIM_KEEP_REASONING"))

# Bifrost's weighted-random key picker proved sticky for a single streaming
# client: a whole codex session (20+ requests) landed on ONE key, exhausted
# its free-tier quota -> 429. The shim therefore pins each upstream request
# to the next key in the pool via `x-bf-api-key` (round-robin), guaranteeing
# an even spread; an explicit pin from the client is always respected.
# Set SHIM_KEY_POOL="" to disable pinning and fall back to Bifrost's picker.
KEY_POOL = [k.strip() for k in os.environ.get(
    "SHIM_KEY_POOL", "agnes-key-1,agnes-key-2,agnes-key-3,agnes-key-4"
).split(",") if k.strip()]
_RR = [0]
_RR_LOCK = threading.Lock()


def next_key():
    with _RR_LOCK:
        key = KEY_POOL[_RR[0] % len(KEY_POOL)]
        _RR[0] += 1
        return key

# Item types that carry a (name, namespace) pair in Codex's transcript and must
# therefore be re-flattened when replayed to the upstream.
CALL_ITEM_TYPES = {"function_call", "function_call_output",
                   "custom_tool_call", "custom_tool_call_output"}


def _ns_prefix(ns):
    """Prefix used when flattening namespace `ns` into a flat function name."""
    prefix = str(ns or "").rstrip("_") or "mcp"
    return f"mcp__{prefix}__" if not prefix.startswith("mcp__") else f"{prefix}__"


def flatten_history(body, rev):
    """Re-flatten namespaced call items in body["input"].

    After the response side un-flattens a call, Codex replays it in the next
    request with `namespace` + short name — but the upstream model only ever
    saw the flat names (and Agnes is strict about payload shape). Map them
    back to the flat form and drop `namespace`. `rev` maps
    (namespace, short_name) -> flat_name. Returns items rewritten.
    """
    inp = body.get("input")
    if not isinstance(inp, list):
        return 0
    n = 0
    for it in inp:
        if not isinstance(it, dict) or it.get("type") not in CALL_ITEM_TYPES:
            continue
        if it.get("namespace") is None:
            continue
        ns = it.get("namespace")
        if ns:
            flat = rev.get((ns, it.get("name")))
            if flat:
                it["name"] = flat
        it.pop("namespace", None)
        n += 1
    if n:
        with STATS_LOCK:
            STATS["history_flat"] += n
    return n


def unflatten_calls(obj, fwd):
    """Split flat function_call names back into {name, namespace} (response side).

    `fwd` maps flat_name -> (namespace, original_name), built from the request's
    namespace tool specs — so the (namespace, name) pair exactly matches the
    key Codex registered the handler under. Recurses through SSE event JSON
    (output_item.added / output_item.done / response.completed.output).
    Returns count of items rewritten.
    """
    n = 0
    if isinstance(obj, dict):
        if obj.get("type") in ("function_call", "custom_tool_call") \
                and not obj.get("namespace"):
            tgt = fwd.get(obj.get("name"))
            if tgt:
                obj["name"] = tgt[1]
                obj["namespace"] = tgt[0]
                n += 1
        for v in obj.values():
            n += unflatten_calls(v, fwd)
    elif isinstance(obj, list):
        for v in obj:
            n += unflatten_calls(v, fwd)
    return n


def _rewrite_sse_line(line, fwd):
    """Un-flatten one SSE line (trailing newline already stripped).

    Returns (line_bytes, changed_count). Non-`data:` lines, blank lines,
    `[DONE]` sentinels and unparseable payloads relay byte-for-byte.
    """
    if not line.startswith(b"data:"):
        return line, 0
    payload = line[5:]
    if payload.startswith(b" "):
        payload = payload[1:]
    had_cr = payload.endswith(b"\r")
    if had_cr:
        payload = payload[:-1]
    if not payload:
        return line, 0
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return line, 0
    if not isinstance(data, (dict, list)):
        return line, 0
    n = unflatten_calls(data, fwd)
    if not n:
        return line, 0
    out = b"data: " + json.dumps(
        data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if had_cr:
        out += b"\r"
    return out, n


def _iter_sse_rewritten(resp, fwd):
    """Yield the SSE response body with flat tool-call names un-flattened.

    Buffers across chunk boundaries so a `data:` line is only rewritten once
    complete; framing/comments/other events relay byte-for-byte. Stats are
    recorded in `finally` so early client disconnects still count.
    """
    buf = b""
    total = 0
    try:
        for chunk in resp.iter_content(chunk_size=8192):
            if not chunk:
                continue
            buf += chunk
            while True:
                i = buf.find(b"\n")
                if i == -1:
                    break
                line, buf = buf[:i], buf[i + 1:]
                out, n = _rewrite_sse_line(line, fwd)
                total += n
                yield out + b"\n"
        if buf:  # stream ended without a trailing newline
            out, n = _rewrite_sse_line(buf, fwd)
            total += n
            yield out
    finally:
        if total:
            with STATS_LOCK:
                STATS["unflattened"] += total
            print(f"[shim] un-flattened {total} tool call(s) in sse response",
                  flush=True)


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

    Returns (new_tools_list, namespaces_seen, functions_created, fwd_map)
    where fwd_map maps each flat function name -> (namespace, original_name),
    i.e. exactly the pair Codex's tool registry expects back on responses.
    """
    out = []
    n_ns = 0
    n_fn = 0
    fwd = {}
    for tool in tools:
        if isinstance(tool, dict) and tool.get("type") == "namespace":
            n_ns += 1
            ns_name = tool.get("name")
            prefix = _ns_prefix(ns_name)
            for sub in tool.get("tools") or []:
                if not isinstance(sub, dict):
                    continue
                if sub.get("type") == "function":
                    fn = sub.get("function") if isinstance(sub.get("function"), dict) else sub
                else:
                    # Responses-style function tool: fields live at top level.
                    fn = {k: v for k, v in sub.items() if k != "type"}
                orig = fn.get("name") or "unnamed"
                name = orig if str(orig).startswith(prefix) else f"{prefix}{orig}"
                if ns_name:
                    fwd[name] = (ns_name, orig)
                out.append({
                    "type": "function",
                    "name": name,
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
                })
                n_fn += 1
        else:
            out.append(tool)
    return out, n_ns, n_fn, fwd


def rewrite_body(raw):
    """Rewrite a request body: flatten namespace tools + strip reasoning items.

    Returns (raw, changed, fwd_map, rev_map). fwd maps flat name ->
    (namespace, name) for the response side; rev is its inverse for history.
    Non-JSON bodies pass through untouched (empty maps).
    """
    if not raw:
        return raw, False, {}, {}
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return raw, False, {}, {}  # not JSON (e.g. multipart) — pass through
    if not isinstance(body, dict):
        return raw, False, {}, {}

    changed = False
    fwd = {}
    if isinstance(body.get("tools"), list):
        tools, n_ns, n_fn, fwd = flatten_tools(body["tools"])
        if n_ns:
            body["tools"] = tools
            with STATS_LOCK:
                STATS["flattened"] += 1
                STATS["namespaces"] += n_ns
            print(f"[shim] flattened {n_ns} namespace(s) -> {n_fn} function tool(s)", flush=True)
            changed = True
    rev = {v: k for k, v in fwd.items()}

    if flatten_history(body, rev):
        changed = True
    if strip_reasoning(body):
        changed = True

    if not changed:
        return raw, False, fwd, rev
    return json.dumps(body, ensure_ascii=False).encode("utf-8"), True, fwd, rev


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
    server_version = "codex-ns-shim/2.0"

    def log_message(self, fmt, *args):  # quieter default logging
        if os.environ.get("SHIM_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _proxy(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        dump_request(method, self.path, raw)
        raw, rewritten, fwd, rev = rewrite_body(raw)

        path = self.path
        if UPSTREAM_PREFIX and path.startswith(UPSTREAM_PREFIX):
            path = path[len(UPSTREAM_PREFIX):] or "/"
        url = f"{UPSTREAM}{path}"
        drop = HOP_BY_HOP | ({"content-length"} if rewritten else set())
        headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in drop and k.lower() != "host"
        }
        # Never let the upstream compress: we may rewrite the body, and a
        # decoded-but-gzip-header'd response would break framing downstream.
        headers["Accept-Encoding"] = "identity"
        # Round-robin key assignment (see KEY_POOL): guarantees all pool keys
        # get traffic even when Bifrost's own picker sticks to one.
        if KEY_POOL and not any(k.lower() == "x-bf-api-key" for k in headers):
            assigned = next_key()
            headers["x-bf-api-key"] = assigned
            with STATS_LOCK:
                STATS["keyed"] += 1
            print(f"[shim] key -> {assigned}", flush=True)
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

        # --- response-side un-flatten -------------------------------------
        # Codex routes tool calls by (namespace, name); the model only ever
        # saw flat names, so split them back before the registry sees them.
        ce = (resp.headers.get("Content-Encoding") or "").strip().lower()
        ctype = (resp.headers.get("Content-Type") or "").lower()
        mode = "passthrough"
        if fwd and not ce:
            if "text/event-stream" in ctype:
                mode = "sse"
            elif "application/json" in ctype:
                mode = "json"
        dbg(f"response mode={mode} ctype={ctype!r} encoding={ce!r}")

        send_body = None
        if mode == "json" and method != "HEAD":
            # Buffer: we may rewrite, so the framing must be ours anyway.
            send_body = resp.content
            try:
                data = json.loads(send_body)
            except (ValueError, UnicodeDecodeError):
                data = None
            if isinstance(data, (dict, list)):
                n = unflatten_calls(data, fwd)
                if n:
                    send_body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                    with STATS_LOCK:
                        STATS["unflattened"] += n
                    print(f"[shim] un-flattened {n} tool call(s) in json response",
                          flush=True)
            else:
                mode = "buffered"  # not JSON — relay original bytes verbatim

        self.send_response(resp.status_code)
        has_length = False
        drop_len = mode in ("sse", "json", "buffered")
        for k, v in resp.headers.items():
            lk = k.lower()
            if lk == "content-length":
                has_length = True
            # Server/Date are skipped because send_response() emits fresh ones.
            if lk in HOP_BY_HOP and lk not in ("server", "date"):
                continue
            if lk in ("server", "date"):
                continue
            if lk == "content-length" and drop_len:
                continue  # body (possibly rewritten) — own the framing below
            self.send_header(k, v)
        if mode in ("json", "buffered"):
            # Body was buffered (and possibly rewritten): declare its length.
            self.send_header("Content-Length", str(len(send_body)))
            has_length = True
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
            if mode in ("json", "buffered"):
                self.wfile.write(send_body)
                self.wfile.flush()
            elif mode == "sse":
                for out in _iter_sse_rewritten(resp, fwd):
                    self.wfile.write(out)
                    self.wfile.flush()
            else:
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
