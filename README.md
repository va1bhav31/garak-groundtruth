# garak-groundtruth

[![PyPI version](https://img.shields.io/pypi/v/garak-groundtruth.svg)](https://pypi.org/project/garak-groundtruth/)

A target adapter and scoring path that lets [garak](https://github.com/NVIDIA/garak)'s
own `agent_breaker` probe run against a target that (a) expects a
structured, multi-file payload instead of one prompt string, and (b) gives
back a real ground-truth score instead of just free text.

See [SETUP.md](SETUP.md) for a step-by-step install and configuration
walkthrough. This README covers the concept and full reference.

## Why this exists

Garak's normal mode is breadth: point it at a target and run a large probe
catalog looking for whatever breaks. `agent_breaker` is different -- it's
already a scored, adaptive, iterative-refinement loop (analyze the tool ->
generate an exploit -> try it -> learn from the real response -> try
again), which is exactly the shape needed for *depth*: proving or
disproving one specific tool-call vulnerability on one specific agent,
thoroughly.

Two things stood between `agent_breaker` and that use case out of the box:

1. **Payload shape.** `agent_breaker` sends one text prompt per turn.
   Some targets -- anything that expects a task note plus a separate
   "skill"/procedure document, or a multi-file bundle -- need that single
   block of attacker text split across named parts before submission.
2. **Scoring.** `agent_breaker` decides success with an LLM judge reading
   the target's reply text. That's the right default when text is all you
   have. If your target already tells you, authoritatively, whether the
   tool call happened and succeeded, trusting that beats guessing from
   text.

This repo is the adapter layer for both, plus the config-only payload
templating that makes #1 reusable across differently-shaped targets. It
deliberately does **not** reimplement model connectivity, prompt-based
attack generation, or the core iterative loop -- garak's `agent_breaker`
already does all of that well; see [docs/lessons.md](docs/lessons.md) for
what we learned building this and where the real edges are.

## Architecture

```
 red-team model  --generate()-->  ScoredAgentBreaker (garak's agent_breaker,
 (any garak                        loop control unchanged except scoring)
  generator: openai,                      |
  rest, nim, ...)                         | attack text (one block)
                                            v
                                  payloads/template.py
                                  (config: split into named parts,
                                   or use verbatim)
                                            |
                                            v
                                  BundleTargetGenerator
                                  (config: submit shape, sync or
                                   submit-then-poll, field mappings)
                                            |
                                            v
                                     your target agent
                                            |
                                            v
                                  reply text + real score
                                  (Message.notes["target_score"])
                                            |
                                            v
                                    PassthroughScore
                                  (reads the target's own verdict,
                                   no LLM judge involved)
```

- **`garak_groundtruth/generators/bundle_target.py`** --
  `BundleTargetGenerator`, a garak `Generator` subclass. Everything
  target-specific (submit URL, whether it's synchronous or
  submit-then-poll, which JSON fields hold the reply/score/status,
  auth header, duplicate-submission handling) is config, not code.
- **`garak_groundtruth/payloads/template.py`** -- turns the
  attacker's one block of text into named parts (files, form fields)
  per a config template. Ask your red-team prompt for JSON with named
  keys (`{"task_md": ..., "skill_md": ...}`) and pull each key out by
  name, or just pass the text through verbatim for single-prompt targets.
- **`garak_groundtruth/probes/scored_agent_breaker.py`** --
  `ScoredAgentBreaker`, a thin subclass of garak's `agent_breaker.AgentBreaker`
  that uses the target's real score (from `Message.notes["target_score"]`)
  for loop control and final scoring, instead of garak's built-in LLM
  judge. Only use this when your target generator sets
  `result_score_field`; otherwise use garak's `AgentBreaker` unmodified,
  it already ships a solid judge for the text-only case.
- **`garak_groundtruth/detectors/passthrough_score.py`** --
  `PassthroughScore`, reads that same real score for final reporting.

## Install

```bash
pip install garak-groundtruth
```

This gets you the importable library (`garak_groundtruth.generators`,
`.probes`, `.detectors`, `.payloads`) for wiring into your own driver
script, per the "Pointing this at a real target" section below.

## Quickstart (no API keys, no real target needed)

The example scripts (`examples/demo_target_server.py`,
`examples/run_local.py`) live in this repo, not in the installed package
-- clone it to run them:

```bash
git clone https://github.com/va1bhav31/garak-groundtruth.git
cd garak-groundtruth
pip install -e .     # or: pip install garak-groundtruth
python examples/demo_target_server.py &     # a toy local target on :8791
python examples/run_local.py
```

This proves out the actual new code in this repo -- the payload
templating and submission/response plumbing -- against a trivial local
target, using garak's built-in `test.Repeat` generator as a stand-in
attacker. You should see a real HTTP round trip: a bundle built from the
sample attack text, submitted as multipart form data, and a reply + score
read back through `PassthroughScore`.

## Pointing this at a real target

Edit the `CONFIG` dict (see `examples/run_local.py` or
`config/example.generator_config.yaml` for the full field reference on
`BundleTargetGenerator`):

- `submit_url`, `submit_encoding` (`multipart` or `json`), `static_fields`
  (any fixed fields your target's API needs alongside the payload).
- `payload_template` -- how to turn the attacker's one text block into
  what your target expects. `bundle_format: zip` packs named parts into a
  zip under one `bundle` field; `multipart_fields` sends each part as its
  own form field; `json_fields` sends a JSON body.
- `async_poll: true` + `poll_url_template` + `submission_id_field` if your
  target accepts a submission and makes you poll for a result, rather
  than answering synchronously.
- `result_text_field` / `result_score_field` / `result_status_field` --
  dotted paths into the (final) response JSON.
- `auth_header_env` -- name of an environment variable holding a bearer
  token. Never put credentials in the config itself.

## Using a real red-team model

`ScoredAgentBreaker` takes the same `red_team_model_type` /
`red_team_model_name` / `red_team_model_config` params as garak's
`agent_breaker.AgentBreaker`, because it *is* that probe with only the
scoring swapped out. Point it at any generator garak already supports --
run `python -m garak --list_generators` to see what's available in your
install (OpenAI, Anthropic-compatible REST endpoints, NIM, HuggingFace,
local models, etc.). This is the part of "built on top of garak" that's
genuinely free: model connectivity for the attacking side isn't this
repo's problem to solve.

You'll also need an `agent.yaml` describing your target's tools, in
garak's own `agent_breaker` format (see `config/example.agent.yaml`).
Garak loads it relative to its own data directory, not an arbitrary path
-- find yours with:

```bash
python -c "import garak.data as d; print(d.path)"
```

and place the file at `<that path>/agent_breaker/agent.yaml` (or point
`agent_config_file` at a different name under that same directory). If
you omit `tools` from the YAML, `agent_breaker` will ask your target agent
what tools it has and infer the file automatically.

## Why not `garak --config ...`?

Garak's plugin loader (as of garak 0.16) only resolves plugins living
inside its own installed package tree (`garak.<category>.<module>`) --
there's no external-plugin-path mechanism yet. That's fine for the
attacking model, which is always a real garak-shipped generator, but it
means this repo's own generator/probe/detector need to be wired up
directly in Python rather than referenced by a CLI config string --
that's exactly what `examples/run_local.py` does. It's also the more
robust option: no risk of your changes getting wiped out by a garak
upgrade or reinstall.

## Scope

Built for testing one specific, authorized target deeply -- not for
scanning many targets for general vulnerabilities (garak's stock probes
already do that well). Every submission this makes is live traffic
against whatever `submit_url` you configure; only point it at targets
you're authorized to test.

See [docs/lessons.md](docs/lessons.md) for the non-obvious things learned
building this: why some generation framings get refused and others don't,
why response latency is a useful signal, and why calibration (prompt
phrasing, timing thresholds) doesn't transfer automatically when you swap
models or targets even though connectivity does.
