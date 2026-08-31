# Lessons from building this

Generalized from running this style of pipeline against a real scored
target over many rounds. None of this is specific to any one platform;
it's about the shape of the problem: an LLM writing attack content against
another LLM-based agent, refined on real responses.

## A judge model refuses framings a generation model won't

The same underlying model, asked to *analyze* a tool for weaknesses
("what could go wrong with this tool's design, framed as security
research") reliably produces usable output. The same model asked to
directly *write manipulative content* ("be deceptive, get the agent to do
something it shouldn't") gets refused far more often -- even with
authorization context attached. garak's own `agent_breaker` prompts
(`ANALYSIS` vs `EXPLOIT` in `garak/data/agent_breaker/prompts.yaml`)
already reflect this split. If your generation step is getting refused,
try reframing it as analysis/documentation rather than instruction, before
concluding the target is unreachable.

This boundary is not uniform. It hardens further when the scenario maps
onto a real, specifically-named legal or regulatory framework (e.g.
something that reads as an actual compliance-reporting law, not a
fictional analogue). That hardening is doing its job -- route around a
generic refusal, but treat a refusal anchored to a real legal framework as
a signal to stop, not an obstacle to word around.

## Response latency is a signal, not noise

Against an async, submit-then-poll target, how *long* a rejection takes
correlates with what kind of rejection it was: a fast rejection tends to
be a reflexive pattern match ("this looks like a bypass attempt"); a slow
one tends to mean the target genuinely reasoned about the request and/or
attempted a tool call before declining. Treat turnaround time as a cheap
extra feature when deciding whether the next round needs a materially
different approach or just a small variation -- but don't hardcode a
specific threshold; calibrate it against your own target's typical
latency first (a target that's synchronous end-to-end won't have this
signal at all).

## Two error types need different retry logic, not more retries

Two failure modes look similar (both are non-2xx-and-not-a-real-result)
but need opposite fixes:

- **Duplicate/identical submission** rejected by the target itself
  (a target that hashes payloads to prevent no-op resubmission). Fix:
  make a trivial content change (whitespace is enough) and resubmit
  immediately -- this is not a signal to change strategy.
- **Generation failure** -- the attacking model itself declined to
  produce content this round. Fix: fall back to a previously-successful
  bundle shape with a small parameter cycled (see `duplicate_error_markers`
  and the generation-failure fallback pattern in
  `ScoredAgentBreaker`/`BundleTargetGenerator`) rather than crashing the
  loop or silently resubmitting empty content.

Conflating these two -- e.g., treating every non-success as "try a
cosmetic tweak" -- wastes attempts on the wrong kind of change.

## Ground truth beats a judge, when you have it

An LLM-as-judge detector (garak's `AgentBreakerResult`, for instance) is a
reasonable default when a target only returns free text. But if the
target is a real scored backend that tells you directly whether the
tool call happened and succeeded, use that score for both loop control
*and* final reporting (see `ScoredAgentBreaker` /
`PassthroughScore` in this repo) -- a judge guessing from reply text is
strictly worse information than the target's own verdict, and using it
anyway just adds noise to a loop that already has a better signal
available.

## Calibration doesn't travel with connectivity

Swapping which model writes the attacks, or which target you point at, is
just config once the generator/probe abstractions are in place -- that
part really is free. What is *not* free: prompt phrasing tuned to dodge
one model's specific refusal patterns, and timing thresholds tuned to one
target's specific latency, do not carry over to a different model or
target automatically. Re-calibrate both per (attacker model, target)
pair rather than assuming a config swap preserves behavior.
