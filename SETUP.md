# Setup guide

Step-by-step, in the order you'll actually do them. See `README.md` for
the conceptual overview and full config field reference.

## 1. Requirements

- Python 3.10+
- `garak >= 0.16.0` (this was built and verified against 0.16.0 specifically
  — check yours: `python -c "import garak; print(garak.__version__)"`.
  Garak's plugin internals move between versions; if you're on something
  materially newer or older, expect to double check the `ScoredAgentBreaker`
  override in `garak_groundtruth/probes/scored_agent_breaker.py`
  still matches `garak.probes.agent_breaker.AgentBreaker`'s current shape.)

## 2. Install

Just want the library, to wire into your own driver script?

```bash
pip install garak-groundtruth
```

Want to run the included examples (step 3 below)? Clone the repo instead
and install from it -- `-e` (editable) means your local edits take effect
immediately, useful if you're modifying the adapter itself:

```bash
git clone https://github.com/va1bhav31/garak-groundtruth.git
cd garak-groundtruth
pip install -e .
```

Either way this installs `garak` and `pyyaml` as dependencies and makes
`garak_groundtruth` importable.

## 3. Verify with the zero-credential smoke test

Proves the actual new code in this repo (payload templating, submission,
response parsing, score passthrough) works, using a trivial local target
and garak's built-in echo generator — no API keys, no network beyond
localhost.

```bash
# terminal 1
python examples/demo_target_server.py

# terminal 2
python examples/run_local.py
```

Expect a reply and a `score: 1.0` printed. If this doesn't run cleanly,
stop here and fix it before moving on — everything past this point builds
on the same plumbing.

## 4. Point it at your real target

Edit the `CONFIG` dict in `examples/run_local.py` (or build your own
driver script from it — it's ~80 lines). At minimum you'll set:

- `submit_url` — your target's real endpoint.
- `submit_encoding` / `bundle_format` — how your target wants the payload
  shaped. See `config/example.generator_config.yaml` for the full field
  reference and comments on each option.
- `async_poll` + `poll_url_template` if your target is submit-then-poll
  rather than synchronous.
- `result_text_field` / `result_score_field` / `result_status_field` —
  dotted paths into your target's response JSON.
- `auth_header_env` — set to the name of an environment variable, then
  export the real token in your shell before running. **Never put a token
  in the config file itself.**

Before trusting the automated loop on a new target: run one known-good
and one known-bad case through by hand first, and confirm
`result_score_field` actually reflects success/failure the way you expect.
The pipeline trusts that field completely — it doesn't independently
verify it means what you think it means.

## 5. Set up your target's `agent.yaml`

`ScoredAgentBreaker` (and garak's own `agent_breaker.AgentBreaker`) load
tool descriptions from a YAML file, in garak's own data directory — not an
arbitrary path. Find yours:

```bash
python -c "import garak.data as d; print(d.path)"
```

Then place your filled-in copy of `config/example.agent.yaml` at:

```
<that path>/agent_breaker/agent.yaml
```

Leave `tools:` empty to have the probe ask your target agent what tools it
has and infer the file automatically instead of writing it by hand.

## 6. Pick your red-team model

Set `red_team_model_type` / `red_team_model_name` in the `probes` section
of your config to any generator garak already supports:

```bash
python -m garak --list_generators
```

Export whatever API key that generator needs (garak will tell you the
exact environment variable name if you forget — it raises
`APIKeyMissingError` with the variable name in the message).

## 7. Run it

```bash
python examples/run_local.py   # or your own driver script built from it
```

Watch the printed `attempts_history` / score output each round. If a
round's result looks wrong, check step 4's field mappings first — a
misconfigured `result_score_field` is the most common cause of a run that
looks like it's failing every attempt when the target is actually
succeeding (see `docs/lessons.md`).

## Troubleshooting

- **`APIKeyMissingError` mentioning NIM** even though you configured a
  different `red_team_model_type` — you're hitting garak's own
  `agent_breaker.AgentBreaker` instead of this repo's
  `ScoredAgentBreaker` (default red-team model is `nim`). Double check
  you imported `garak_groundtruth.probes.scored_agent_breaker.ScoredAgentBreaker`.
- **Every attempt scores `0.0`** — almost always a `result_score_field`
  pointing at the wrong dotted path, or a target that doesn't return a
  score at all (see README's "Using `ScoredAgentBreaker` vs `AgentBreaker`"
  guidance — an unscored target needs garak's stock `AgentBreaker`, not
  this one).
- **`Agent config file not found`** — your `agent.yaml` isn't under
  garak's data directory (step 5), or `agent_config_file` in your probe
  config doesn't match the filename you used.
