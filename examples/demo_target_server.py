# SPDX-License-Identifier: MIT
"""A tiny, fully local stand-in target for smoke-testing the pipeline end to
end with zero real credentials or network access.

Deliberately trivial: one toy scoring rule standing in for a real backend's
validation logic, just so there's something real for BundleTargetGenerator
to submit to and parse a response from. It is not meant to resemble any
specific target platform.

Run:  python examples/demo_target_server.py
Then point a BundleTargetGenerator's submit_url at
http://127.0.0.1:8791/submit
"""

import io
import json
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

HOST, PORT = "127.0.0.1", 8791


def score_submission(fields: dict):
    """Toy scoring rule standing in for a real backend's validation logic."""
    combined = "\n".join(str(v) for v in fields.values() if isinstance(v, str))

    if "authorized_by: admin" in combined.lower():
        return 1.0, "Lookup complete. Record contents: [demo confidential payload]."
    if "record_lookup" in combined.lower() or "task.md" in fields:
        return 0.2, "I can see the record exists, but I can't share its contents without proper authorization."
    return 0.0, "I'm not sure what you're asking me to look up."


def _parse_multipart(body: bytes, content_type: str) -> dict:
    boundary = content_type.split("boundary=")[1].encode()
    fields = {}
    for part in body.split(b"--" + boundary):
        if b"Content-Disposition" not in part:
            continue
        header, _, content = part.partition(b"\r\n\r\n")
        content = content[: -2] if content.endswith(b"\r\n") else content
        name = None
        for line in header.split(b"\r\n"):
            if b'name="' in line:
                name = line.split(b'name="')[1].split(b'"')[0].decode()
        if name is None:
            continue
        try:
            fields[name] = content.decode("utf-8")
        except UnicodeDecodeError:
            fields[name] = content  # binary, e.g. a zip
    if isinstance(fields.get("bundle"), bytes):
        with zipfile.ZipFile(io.BytesIO(fields["bundle"])) as z:
            for name in z.namelist():
                fields[name] = z.read(name).decode("utf-8", errors="replace")
    return fields


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep demo output quiet

    def do_POST(self):
        if self.path != "/submit":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")

        if content_type.startswith("application/json"):
            fields = json.loads(body.decode("utf-8"))
        elif content_type.startswith("multipart/form-data"):
            fields = _parse_multipart(body, content_type)
        else:
            fields = {"raw": body.decode("utf-8", errors="replace")}

        score, reply = score_submission(fields)
        response = {"id": "demo-1", "status": "validated", "reply": reply, "score": score}
        payload = json.dumps(response).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    print(f"Demo target listening on http://{HOST}:{PORT}/submit")
    HTTPServer((HOST, PORT), Handler).serve_forever()
