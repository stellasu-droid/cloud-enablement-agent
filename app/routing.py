# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Model routing policy: which Gemini tier serves each request.

Base tier per role, then dynamic escalation by one tier for a retry, a hard
subtopic (explainer / lab) or an advanced learner (lab), capped at Pro.
"""

from __future__ import annotations

import os

ORDER = ["fast", "flash", "pro"]

_DEFAULT_MODELS = {
    "fast": "gemini-3.5-flash",
    "flash": "gemini-3.8-flash",
    "pro": "gemini-3.1-pro-preview",
}

BASE_TIER = {
    "enablement_agent": "flash",  # coach: tool selection and short glue
    "curriculum_planner": "pro",  # shapes the whole course
    "explainer": "flash",  # accuracy at good speed
    "lab_designer": "flash",  # correct gcloud syntax matters
    "quiz_master": "fast",  # short, derived from the subtopic
}
COMPLEXITY_ESCALATES = {"explainer", "lab_designer"}
LEVEL_ESCALATES = {"lab_designer"}


def model_for(tier: str) -> str:
    """Model ID for a tier; override with MODEL_FAST / MODEL_FLASH / MODEL_PRO."""
    return os.environ.get(f"MODEL_{tier.upper()}") or _DEFAULT_MODELS[tier]


def choose_tier(
    agent: str,
    *,
    complexity: int | None = None,
    learner_level: str | None = None,
    attempt: int = 0,
) -> tuple[str, str]:
    """Returns (tier, reason) for one model request."""
    tier = BASE_TIER.get(agent, "flash")
    reasons = [f"base:{tier}"]
    bumps = 0
    if attempt >= 1:
        bumps += 1
        reasons.append(f"retry:{attempt}")
    if agent in COMPLEXITY_ESCALATES and complexity is not None and complexity >= 4:
        bumps += 1
        reasons.append(f"complexity:{complexity}")
    if agent in LEVEL_ESCALATES and learner_level == "advanced":
        bumps += 1
        reasons.append("level:advanced")
    index = min(ORDER.index(tier) + bumps, len(ORDER) - 1)
    return ORDER[index], ",".join(reasons)
