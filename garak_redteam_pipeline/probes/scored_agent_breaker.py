# SPDX-License-Identifier: MIT
"""AgentBreaker, but driven by the target's own ground-truth score.

Garak's ``agent_breaker.AgentBreaker`` decides both (a) whether to keep
attacking a tool and (b) the final reported score using an LLM judge
(``AgentBreakerResult``) that reads the target's reply text and guesses.
That's the right default when a target only gives you free-text replies.

But when the target *is* a real scored backend -- it tells you directly
whether the tool call happened and succeeded -- guessing from text is
strictly worse than just trusting that score, for both loop control and
final reporting. ``ScoredAgentBreaker`` overrides both to do exactly that,
reading ``Message.notes["target_score"]`` as set by
``garak_redteam_pipeline.generators.bundle_target.BundleTargetGenerator``.

Only use this with a target generator that actually sets
``result_score_field``. If your target doesn't return a real score, use
garak's own ``agent_breaker.AgentBreaker`` unmodified.
"""

import logging

from garak import _config
from garak.probes.agent_breaker import AgentBreaker, AttackState
from garak.probes import IterativeProbe


class ScoredAgentBreaker(AgentBreaker):
    """AgentBreaker scored by the target's own reported result, not an LLM judge."""

    primary_detector = "bundle_target.PassthroughScore"

    def __init__(self, config_root=_config):
        # Deliberately skip AgentBreaker.__init__'s body: it unconditionally
        # constructs AgentBreakerResult, an LLM-judge detector that eagerly
        # loads its own model and needs its own API key -- even though
        # `_generate_next_attempts` below never touches `self._detector`.
        # Everything else AgentBreaker.__init__ does (loading prompts,
        # loading agent config) is replicated here.
        self.red_team_model = None
        self.parse_model = None
        self.agent_config = None
        self.agent_analysis = None
        self._detector = None
        IterativeProbe.__init__(self, config_root=config_root)
        if self.langprovider.target_lang not in ("en", self.lang):
            logging.warning(
                f"{self.__class__.__name__} probe currently does not support "
                f"languages other than {self.lang}"
            )
        self._load_prompts()
        self._load_agent_config()

    def _generate_next_attempts(self, last_attempt):
        outputs = last_attempt.outputs or []
        if not outputs:
            return []

        state = AttackState.from_notes(last_attempt.notes or {})

        verified_results = []
        best_confidence = -1.0
        best_response = ""
        attack_succeeded = False

        for output in outputs:
            if output is None or output.text is None:
                verified_results.append((False, 0.0))
                continue
            score = (output.notes or {}).get("target_score")
            if score is None:
                # Target didn't return ground truth for this output --
                # count as an unscored miss rather than guessing.
                verified_results.append((False, 0.0))
                continue
            confidence = float(score)
            is_success = confidence >= self.success_threshold
            verified_results.append((is_success, confidence))
            if confidence > best_confidence:
                best_confidence = confidence
                best_response = output.text
            if is_success:
                attack_succeeded = True

        state.verified_results = verified_results
        best_success = any(s for s, _ in verified_results)
        state.attempts_history.append(
            {
                "target": state.current_target,
                "prompt": last_attempt.prompt.last_message().text,
                "success": best_success,
                "confidence": best_confidence,
                "reasoning": "target-reported ground-truth score",
                "response": best_response,
            }
        )
        last_attempt.notes = state.to_notes()

        if attack_succeeded:
            return []

        next_attempt = self._handle_exploitation_phase(last_attempt)
        return [next_attempt] if next_attempt is not None else []
