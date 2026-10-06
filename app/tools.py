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

"""Constants and the two human-in-the-loop tools used by the coach."""

from __future__ import annotations

import os

from google.adk.tools import FunctionTool, ToolContext

SPECIALISTS = ("curriculum_planner", "explainer", "lab_designer", "quiz_master")
LESSON_SPECIALISTS = ("explainer", "lab_designer", "quiz_master")

# Session-state keys (persisted with the session).
PENDING_LAB_KEY = "enablement_pending_lab"
APPROVED_LAB_KEY = "enablement_approved_lab"


def lab_cost_threshold() -> float:
    """Labs estimated above this (USD) need the learner's approval."""
    return float(os.environ.get("LAB_COST_APPROVAL_USD", "2.0"))


def confirm_bulk_generation(subtopic_numbers: list[int], reason: str) -> dict:
    """Asks the learner to approve generating several lessons in one go.

    Call this, and wait for approval, before teaching more than one subtopic in
    a single turn (for example when the learner says "all").

    Args:
        subtopic_numbers: The outline numbers of the subtopics to teach, in order.
        reason: One short sentence for the learner, e.g. "Teach all 5 subtopics
            (about 4 minutes of generation)."

    Returns:
        {"status": "approved", "subtopic_numbers": [...]} when approved. A
        rejection comes back as an error; then offer to teach one subtopic.
    """
    return {"status": "approved", "subtopic_numbers": subtopic_numbers}


def confirm_costly_lab(
    subtopic_number: int,
    estimated_cost_usd: float,
    cost_drivers: list[str],
    tool_context: ToolContext,
) -> dict:
    """Asks the learner to approve a lab whose estimated cost is above the limit.

    Call this only when lab_designer returned status "needs_approval". Pass the
    values from that response unchanged.

    Args:
        subtopic_number: Outline number of the subtopic the lab belongs to.
        estimated_cost_usd: The lab's estimated cost in USD.
        cost_drivers: The resources that drive the cost.

    Returns:
        {"status": "approved", ...} when approved: call lab_designer again with
        the same arguments to show the approved lab. A rejection comes back as
        an error: call lab_designer again with max_cost_usd set to 2.0.
    """
    tool_context.state[APPROVED_LAB_KEY] = subtopic_number
    return {
        "status": "approved",
        "subtopic_number": subtopic_number,
        "next_step": "Call lab_designer again with the same arguments.",
    }


confirm_bulk_tool = FunctionTool(confirm_bulk_generation, require_confirmation=True)
confirm_lab_tool = FunctionTool(confirm_costly_lab, require_confirmation=True)
