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

"""Specialist sub-agents used by the enablement coach.

Each specialist runs in ``single_turn`` mode: ADK exposes it to the coach as a
tool whose parameters come from ``input_schema`` (with field descriptions), it
returns JSON matching ``output_schema``, and control always returns to the
coach. The RoutingPlugin picks the actual model per request; the model set here
is only the base tier.
"""

from google.adk.agents import Agent
from google.adk.models import Gemini
from google.genai import types
from pydantic import BaseModel

from app import prompts
from app.routing import BASE_TIER, model_for
from app.schemas import (
    CourseOutline,
    Explainer,
    LabGuide,
    Quiz,
    SubtopicRequest,
    TopicRequest,
)


def make_model(agent_name: str) -> Gemini:
    """Base-tier Gemini model for an agent, with retries on 429/5xx."""
    return Gemini(
        model=model_for(BASE_TIER[agent_name]),
        retry_options=types.HttpRetryOptions(attempts=3),
    )


def _specialist(
    name: str,
    description: str,
    instruction: str,
    input_schema: type[BaseModel],
    output_schema: type[BaseModel],
) -> Agent:
    return Agent(
        name=name,
        model=make_model(name),
        mode="single_turn",
        description=description,
        instruction=instruction,
        input_schema=input_schema,
        output_schema=output_schema,
        # Without these, a specialist with output_schema is still offered
        # transfer_to_agent; calling it fails the specialist.
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )


def make_curriculum_planner() -> Agent:
    return _specialist(
        "curriculum_planner",
        "Breaks a Google Cloud / Google AI topic into an ordered outline of 3-7 "
        "subtopics, each with a learning objective and a complexity from 1 to 5. "
        "The learner sees the outline directly.",
        prompts.PLANNER_INSTRUCTION,
        TopicRequest,
        CourseOutline,
    )


def make_explainer() -> Agent:
    return _specialist(
        "explainer",
        "Writes the concept explanation for ONE subtopic. The learner sees it "
        "directly. Call before lab_designer.",
        prompts.EXPLAINER_INSTRUCTION,
        SubtopicRequest,
        Explainer,
    )


def make_lab_designer() -> Agent:
    return _specialist(
        "lab_designer",
        "Writes a safe hands-on Google Cloud lab (setup, steps, validation, "
        "clean-up, cost estimate) for ONE subtopic. May return needs_approval "
        "when the lab is estimated above the cost limit.",
        prompts.LAB_INSTRUCTION,
        SubtopicRequest,
        LabGuide,
    )


def make_quiz_master() -> Agent:
    return _specialist(
        "quiz_master",
        "Writes a four-question knowledge-check quiz with answers for ONE "
        "subtopic. The learner sees it directly. Call after lab_designer.",
        prompts.QUIZ_INSTRUCTION,
        SubtopicRequest,
        Quiz,
    )
