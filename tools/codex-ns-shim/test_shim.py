#!/usr/bin/env python3
"""Tests for codex-ns-shim: unit tests for flatten_tools, plus integration
tests that run the shim against a mock upstream (real HTTP, SSE included)."""

import json
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Path of the shim, independent of cwd (was: hard-coded "tools/codex-ns-shim",
# which only worked when run from the repo root).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import shim  # noqa: E402

import requests  # noqa: E402


# ---------- Unit tests ----------

class TestFlatten(unittest.TestCase):
    def test_namespace_flattened_with_prefix(self):
        tools = [
            {"type": "function", "name": "exec_command", "parameters": {}},
            {
                "type": "namespace",
                "name": "mcp__trl-retrieve__",
                "tools": [
                    {"type": "function", "name": "search", "parameters": {"type": "object"}},
                    {"type": "function", "name": "fetch", "parameters": {"type": "object"}},
                ],
            },
        ]
        out, n_ns, n_fn, fwd = shim.flatten_tools(tools)
        self.assertEqual(n_ns, 1)
        self.assertEqual(n_fn, 2)
        self.assertEqual(len(out), 3)
        fn_tools = [t for t in out if t["type"] == "function"]
        names = {t["name"] for t in fn_tools}
        self.assertIn("exec_command", names)
        self.assertIn("mcp__trl-retrieve__search", names)
        self.assertIn("mcp__trl-retrieve__fetch", names)
        for t in fn_tools:
            self.assertEqual(t["type"], "function")
        # fwd_map: flat name -> (namespace, original name), for the response side.
        self.assertEqual(fwd["mcp__trl-retrieve__search"], ("mcp__trl-retrieve__", "search"))
        # Tools that were already flat must not enter the map.
        self.assertNotIn("exec_command", fwd)

    def test_no_namespace_passthrough_unchanged_bytes(self):
        raw = json.dumps({"tools": [{"type": "function", "name": "f"}]}).encode()
        out, rewritten, fwd, rev = shim.rewrite_body(raw)
        self.assertFalse(rewritten)
        self.assertEqual(out, raw)
        self.assertEqual((fwd, rev), ({}, {}))

    def test_non_json_body_passthrough(self):
        raw, rewritten, fwd, rev = shim.rewrite_body(b"not json")
        self.assertFalse(rewritten)
        self.assertEqual(raw, b"not json")
        self.assertEqual((fwd, rev), ({}, {}))

    def test_empty_body_passthrough(self):
        raw, rewritten, fwd, rev = shim.rewrite_body(b"")
        self.assertFalse(rewritten)
        self.assertEqual((fwd, rev), ({}, {}))

    def test_no_tools_field_passthrough(self):
        raw = json.dumps({"model": "x", "input": "hi"}).encode()
        out, rewritten, fwd, rev = shim.rewrite_body(raw)
        self.assertFalse(rewritten)
        self.assertEqual(out, raw)
        self.assertEqual((fwd, rev), ({}, {}))

    def test_tools_field_not_list_passthrough(self):
        raw = json.dumps({"tools": "weird"}).encode()
        out, rewritten, fwd, rev = shim.rewrite_body(raw)
        self.assertFalse(rewritten)
        self.assertEqual(out, raw)
        self.assertEqual((fwd, rev), ({}, {}))

    def test_nested_function_shape_flattened(self):
        tools = [{
            "type": "namespace",
            "name": "mcp__ctx7__",
            "tools": [{
                "type": "function",
                "function": {"name": "resolve",
                             "parameters": {"type": "object", "properties": {}},
                             "description": "d"},
            }],
        }]
        out, n_ns, n_fn, fwd = shim.flatten_tools(tools)
        self.assertEqual((n_ns, n_fn), (1, 1))
        self.assertEqual(out[0]["type"], "function")
        self.assertEqual(out[0]["name"], "mcp__ctx7__resolve")
        self.assertEqual(out[0]["description"], "d")
        self.assertEqual(fwd["mcp__ctx7__resolve"], ("mcp__ctx7__", "resolve"))

    def test_degenerate_namespace_gets_sane_default_name(self):
        out, n_ns, n_fn, fwd = shim.flatten_tools(
            [{"type": "namespace", "tools": [{"type": "function", "name": "t"}]}])
        self.assertEqual((n_ns, n_fn), (1, 1))
        self.assertEqual(out[0]["name"], "mcp__mcp__t")

    def test_flat_mcp_prefixed_tool_not_double_prefixed(self):
        out, n_ns, n_fn, fwd = shim.flatten_tools(
            [{"type": "namespace", "name": "mcp__srv__",
              "tools": [{"type": "function", "name": "mcp__srv__t"}]}])
        self.assertEqual(out[0]["name"], "mcp__srv__t")

    def test_fwd_rev_maps_are_inverse(self):
        """rev_map phải dựng lại đúng history: flat -> original, giữ namespace."""
        tools = [{"type": "namespace", "name": "mcp__srv__",
                  "tools": [{"type": "function", "name": "a"},
                            {"type": "function", "name": "b"}]}]
        raw = json.dumps({"tools": tools}).encode()
        _, rewritten, fwd, rev = shim.rewrite_body(raw)
        self.assertTrue(rewritten)
        self.assertEqual(fwd, {
            "mcp__srv__a": ("mcp__srv__", "a"),
            "mcp__srv__b": ("mcp__srv__", "b"),
        })
        # rev = {v: k for k, v in fwd.items()} → (namespace, original) -> flat
        self.assertEqual(rev[("mcp__srv__", "a")], "mcp__srv__a")
        self.assertEqual(rev[("mcp__srv__", "b")], "mcp__srv__b")


# ---------- Mock upstream + integration ----------

class MockUpstream(BaseHTTPRequestHandler):
    """Rejects any request whose tools[] still contains a `namespace` variant,
    exactly like the strict backend behind Bifrost does."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_POST(self):
        body = self._read_body()
        try:
            req = json.loads(body)
        except ValueError:
            req = {}
        tools = req.get("tools", [])
        bad = [t for t in tools
               if isinstance(t, dict) and t.get("type") not in
               ("function", "web_search_preview", "code_interpreter", "mcp")]
        if bad:
            msg = json.dumps({"error": {"type": "invalid_request_error",
                                        "message": f"unknown variant `namespace` x{len(bad)}"}}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return
        # SSE-style reply
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        self.wfile.write(b"data: {\"type\":\"response.created\"}\n\n")
        self.wfile.write(b"data: [DONE]\n\n")

    do_GET = do_POST


class TestIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), MockUpstream)
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        cls.upstream_port = cls.upstream.server_port
        cls.shim_port = cls._start_shim()

    @classmethod
    def tearDownClass(cls):
        cls.upstream.shutdown()

    @classmethod
    def _start_shim(cls):
        shim.UPSTREAM = f"http://127.0.0.1:{cls.upstream_port}"
        from urllib.parse import urlsplit
        shim.UPSTREAM_PARTS = urlsplit(shim.UPSTREAM)
        shim.UPSTREAM_PREFIX = shim.UPSTREAM_PARTS.path.rstrip("/")
        shim.LISTEN_HOST = "127.0.0.1"
        shim.LISTEN_PORT = 0  # ephemeral port
        server = shim.ThreadingHTTPServer((shim.LISTEN_HOST, shim.LISTEN_PORT), shim.Handler)
        cls.shim_server = server
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server.server_address[1]

    def test_codex_like_request_flattened(self):
        payload = {
            "model": "agnes/agnes-2.5-flash",
            "stream": True,
            "tools": [
                {"type": "function", "name": "shell", "parameters": {}},
                # NOTE: no {"type": "web_search"} here — Codex omits it when
                # web_search = "disabled" (and the strict backend rejects it).
                {"type": "namespace", "name": "mcp__trl-retrieve__", "tools": [
                    {"type": "function", "name": "search",
                     "parameters": {"type": "object", "properties": {}}},
                ]},
            ],
        }
        r = requests.post(f"http://127.0.0.1:{self.shim_port}/openai/v1/responses",
                          json=payload, timeout=10)
        self.assertEqual(r.status_code, 200)  # 400 would mean a namespace survived
        self.assertIn(b"data: [DONE]", r.content)
        self.assertIn(b"response.created", r.content)

    def test_base_url_path_not_doubled(self):
        payload = {"model": "m", "tools": [{"type": "function", "name": "f"}]}
        # Client base_url includes the /openai/v1 prefix, so request path does.
        r = requests.post(f"http://127.0.0.1:{self.shim_port}/openai/v1/responses",
                          json=payload, timeout=10)
        self.assertEqual(r.status_code, 200)

    def test_request_without_namespace_passes_through(self):
        r = requests.post(f"http://127.0.0.1:{self.shim_port}/openai/v1/responses",
                          json={"model": "m", "tools": [{"type": "function", "name": "f"}]},
                          timeout=10)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"[DONE]", r.content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
