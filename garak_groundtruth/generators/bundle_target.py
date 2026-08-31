# SPDX-License-Identifier: MIT
"""Generic target adapter for scripted, feedback-driven agent red-teaming.

Wraps *any* target that accepts an attack payload and returns a text reply
as a garak ``Generator``, so garak's own probes (e.g. ``agent_breaker``)
can drive it without knowing anything about the target's transport.

Two things vary between targets and are handled entirely through config,
never through code changes:

1. **Payload shape.** Some targets take one prompt string; others take a
   structured bundle (several named files, form fields, a zip). See
   ``garak_groundtruth.payloads.template``.
2. **Delivery pattern.** Some targets answer synchronously in the HTTP
   response; others accept a submission and require polling a status
   endpoint until it stops being "pending". Both are implemented here
   behind one ``_call_model``; which one runs is a config flag.

Everything specific to one target -- URLs, field names, auth header, how
to pull the reply text and (optionally) a ground-truth score out of the
response JSON -- lives in the generator's config block, not in this file.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import List, Optional, Union

from garak.attempt import Conversation, Message
from garak.generators.base import Generator

from garak_groundtruth.payloads.template import build_payload


def _dig(obj, dotted_path: str):
    """Pull a value out of nested dicts using a dotted path, e.g. 'validation.score.total_score'."""
    cur = obj
    for key in dotted_path.split("."):
        if cur is None:
            return None
        cur = cur.get(key) if isinstance(cur, dict) else None
    return cur


class BundleTargetGenerator(Generator):
    """Submits a (possibly multi-file) payload to a target endpoint and
    returns its reply as a garak generation, optionally carrying the
    target's own ground-truth score through in ``Message.notes``.

    Config lives under ``generators.bundle_target.BundleTargetGenerator``
    in a garak run config. See ``config/example.generator_config.yaml``
    for a worked example.
    """

    generator_family_name = "bundle_target"
    name = "BundleTarget"
    supports_multiple_generations = False
    parallel_capable = False  # most of these targets rate-limit hard; stay serial by default

    DEFAULT_PARAMS = Generator.DEFAULT_PARAMS | {
        # -- submission --
        "submit_url": None,  # required
        "submit_method": "POST",
        "submit_encoding": "multipart",  # "multipart" | "json"
        "static_fields": {},  # extra fields/values sent on every submission (target-specific ids, categories, etc.)
        "auth_header_env": None,  # env var holding a bearer token, e.g. "TARGET_API_TOKEN"
        "auth_header_name": "Authorization",
        "auth_header_prefix": "Bearer ",
        # -- payload shape (see payloads/template.py) --
        "payload_template": [{"name": "prompt.txt", "from": "text"}],
        "bundle_format": "multipart_fields",  # "multipart_fields" | "zip" | "json_fields"
        "zip_root": "",  # optional path prefix inside the zip, e.g. "attack/"
        # -- delivery pattern --
        "async_poll": False,
        "poll_url_template": None,  # e.g. "https://host/api/submissions/{submission_id}" -- {submission_id} filled from submit response
        "submission_id_field": "id",  # dotted path in the submit response holding the id to poll with
        "poll_interval_s": 5,
        "poll_max_tries": 25,
        "poll_status_field": "status",
        "pending_statuses": ["pending", "validating", "running"],
        # -- result extraction --
        "result_text_field": "reply",  # dotted path to the reply text in the (final) response JSON
        "result_score_field": None,  # optional dotted path to a ground-truth score, e.g. "validation.score.total_score"
        "result_status_field": "status",
        # -- duplicate-submission handling --
        # some targets 409/400 on a byte-identical resubmission; if a substring from this
        # list appears in an error body, retry once with a trivial whitespace change.
        "duplicate_error_markers": ["identical", "duplicate", "already submitted"],
    }

    def _load_deps(self):
        if not self.submit_url:
            raise ValueError(
                "BundleTargetGenerator requires 'submit_url' in its config"
            )
        if self.async_poll and not self.poll_url_template:
            raise ValueError(
                "async_poll=true requires 'poll_url_template' in its config"
            )

    def _auth_headers(self) -> dict:
        if not self.auth_header_env:
            return {}
        token = os.environ.get(self.auth_header_env)
        if not token:
            logging.warning(
                "%s: auth_header_env=%s is not set in the environment",
                self.__class__.__name__,
                self.auth_header_env,
            )
            return {}
        return {self.auth_header_name: f"{self.auth_header_prefix}{token}"}

    def _http_json(self, url: str, method: str = "GET", body: Optional[bytes] = None,
                    headers: Optional[dict] = None) -> Union[dict, dict]:
        req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return {"_error": e.code, "_message": e.read().decode("utf-8", errors="replace")}

    def _submit(self, attack_text: str) -> dict:
        parts = build_payload(attack_text, self.payload_template)
        headers = dict(self._auth_headers())
        fields = dict(self.static_fields)

        if self.bundle_format == "json_fields":
            fields.update(parts)
            body = json.dumps(fields).encode("utf-8")
            headers["Content-Type"] = "application/json"
            return self._http_json(self.submit_url, self.submit_method, body, headers)

        if self.bundle_format == "zip":
            import io
            import zipfile

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for name, content in parts.items():
                    z.writestr(f"{self.zip_root}{name}", content)
            fields["bundle"] = buf.getvalue()
            return self._multipart_submit(fields, file_field="bundle", filename="bundle.zip",
                                            content_type="application/zip", headers=headers)

        # default: multipart_fields -- every payload part is its own form field/file
        fields.update(parts)
        return self._multipart_submit(fields, headers=headers)

    def _multipart_submit(self, fields: dict, headers: dict,
                           file_field: Optional[str] = None,
                           filename: str = "file",
                           content_type: str = "application/octet-stream") -> dict:
        boundary = uuid.uuid4().hex

        def text_field(name, value):
            return (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
            ).encode("utf-8")

        parts_bytes = []
        for name, value in fields.items():
            if name == file_field:
                parts_bytes.append((
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                    f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'
                ).encode("utf-8") + value + b"\r\n")
            else:
                value_bytes = value if isinstance(value, bytes) else str(value).encode("utf-8")
                parts_bytes.append(text_field(name, value_bytes.decode("utf-8", errors="replace")))

        body = b"".join(parts_bytes) + f"--{boundary}--\r\n".encode("utf-8")
        headers = dict(headers)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        return self._http_json(self.submit_url, self.submit_method, body, headers)

    def _poll(self, submission_id: str) -> dict:
        url = self.poll_url_template.format(submission_id=submission_id)
        headers = self._auth_headers()
        data = {}
        for _ in range(self.poll_max_tries):
            data = self._http_json(url, "GET", None, headers)
            status = _dig(data, self.poll_status_field)
            if "_error" in data or status not in self.pending_statuses:
                return data
            time.sleep(self.poll_interval_s)
        return data

    def _call_model(
        self, prompt: Conversation, generations_this_call: int = 1
    ) -> List[Union[Message, None]]:
        attack_text = prompt.last_message().text

        result = self._submit(attack_text)

        if "_error" in result:
            body = result.get("_message", "")
            if any(marker in body for marker in self.duplicate_error_markers):
                logging.info(
                    "%s: duplicate submission rejected, retrying with a trivial change",
                    self.__class__.__name__,
                )
                result = self._submit(attack_text + " ")
            if "_error" in result:
                logging.warning(
                    "%s: submission failed (%s): %s",
                    self.__class__.__name__, result.get("_error"), result.get("_message"),
                )
                return [Message(text=None, notes={"submit_error": result})]

        if self.async_poll:
            submission_id = _dig(result, self.submission_id_field)
            if submission_id is None:
                logging.warning(
                    "%s: no submission id found at '%s' in submit response",
                    self.__class__.__name__, self.submission_id_field,
                )
                return [Message(text=None, notes={"submit_response": result})]
            result = self._poll(str(submission_id))

        reply_text = _dig(result, self.result_text_field)
        score = _dig(result, self.result_score_field) if self.result_score_field else None
        status = _dig(result, self.result_status_field)

        notes = {"target_status": status, "raw_response": result}
        if score is not None:
            notes["target_score"] = score

        return [Message(text=reply_text, notes=notes)]
