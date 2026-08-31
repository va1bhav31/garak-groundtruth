# SPDX-License-Identifier: MIT
"""Turns one piece of attacker-generated text into the named parts a target expects.

The red-team model (e.g. garak's ``agent_breaker`` probe) always produces a
single block of text per turn. Some targets want that verbatim, as one
prompt. Others want it split across several named files (a task note, a
"skill"/procedure doc, attachments) -- a convention specific to whichever
target you're pointed at, not something the pipeline should hardcode.

Two ways to drive that split, both config-only:

1. The attacker's text *is* JSON with keys matching your part names
   (this is the easiest way to get multi-part output: ask for it in your
   red-team prompt template, e.g. ``{"task_md": "...", "skill_md": "..."}``).
   Each template entry with ``"from": "json:<key>"`` pulls that key out.

2. ``"from": "text"`` on a single-entry template just uses the whole
   attacker output verbatim as that one part -- the common case for
   plain single-prompt targets.

Static, non-generated parts (headers, boilerplate) can be included with
``"from": "static"`` and a ``"value"``.
"""

import json


def build_payload(attack_text: str, template: list) -> dict:
    """Returns {part_name: content} per the template.

    :param attack_text: the raw text produced by the attacking generator this turn.
    :param template: list of {"name": str, "from": "text"|"json:<key>"|"static", "value": optional}
    """
    parsed_json = None

    def as_json():
        nonlocal parsed_json
        if parsed_json is None:
            try:
                parsed_json = json.loads(attack_text)
            except (json.JSONDecodeError, TypeError):
                parsed_json = {}
        return parsed_json

    parts = {}
    for entry in template:
        name = entry["name"]
        source = entry.get("from", "text")

        if source == "text":
            parts[name] = attack_text
        elif source == "static":
            parts[name] = entry.get("value", "")
        elif source.startswith("json:"):
            key = source.split(":", 1)[1]
            value = as_json().get(key, "")
            parts[name] = value if isinstance(value, str) else json.dumps(value)
        else:
            raise ValueError(f"Unknown payload template source: {source!r}")

    return parts
