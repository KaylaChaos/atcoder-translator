# -*- coding: utf-8 -*-
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

import index


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/healthz"):
            self._send_json(200, {"ok": True, "service": "atcoder-translator"})
            return
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path == "/init":
            self._send_function_response(200, {"ok": True, "init": True})
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            event = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            event = {"raw": raw.decode("utf-8", errors="replace")}

        result = index.handler(event, None)
        status = 200 if isinstance(result, dict) and result.get("ok", True) else 500
        self._send_function_response(status, result)

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_function_response(self, status, payload):
        body_text = json.dumps(payload, ensure_ascii=False)
        envelope = {
            "isBase64Encoded": False,
            "statusCode": status,
            "headers": {"Content-Type": "application/json; charset=utf-8"},
            "body": body_text,
        }
        self._send_json(200, envelope)


def main():
    port = int(os.getenv("PORT", "8000"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"container server listening on 0.0.0.0:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
