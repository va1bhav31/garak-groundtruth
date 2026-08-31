# SPDX-License-Identifier: MIT
"""Detector that trusts the target's own scoring instead of guessing.

Many red-team targets (CTF/arena-style scoring backends especially) already
return a ground-truth score for each submission -- there's no need for an
LLM-as-judge when the target tells you directly whether the tool call
happened and succeeded. ``BundleTargetGenerator`` stashes that score in
``Message.notes["target_score"]`` when ``result_score_field`` is configured;
this detector just reads it back out.

If a target doesn't provide a real score, don't use this detector --
fall back to garak's own ``agent_breaker.AgentBreakerResult``, which uses
an LLM judge against the reply text instead.
"""

import logging

from garak import _config
from garak.attempt import Attempt
from garak.detectors.base import Detector


class PassthroughScore(Detector):
    """Reads the target's own ground-truth score straight through.

    Scores are expected already normalized to the 0.0-1.0 range used by
    garak detectors; anything outside that range is clipped, with a
    warning, rather than silently misreported.
    """

    lang_spec = "*"
    doc_uri = ""

    def __init__(self, config_root=_config):
        super().__init__(config_root=config_root)

    def detect(self, attempt: Attempt) -> list:
        results = []
        for output in attempt.outputs or []:
            if output is None or output.notes is None:
                results.append(0.0)
                continue
            score = output.notes.get("target_score")
            if score is None:
                logging.warning(
                    "PassthroughScore: no target_score in output notes -- "
                    "did you set result_score_field on the generator? Scoring as 0.0."
                )
                results.append(0.0)
                continue
            score = float(score)
            if not 0.0 <= score <= 1.0:
                logging.warning(
                    "PassthroughScore: target_score %s outside [0,1], clipping", score
                )
                score = max(0.0, min(1.0, score))
            results.append(score)
        return results
