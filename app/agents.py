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
tool, it does one job without chatting with the learner, and control always
returns to the coach.
"""

from google.adk.agents import Agent
from google.adk.models import Gemini
from google.genai import types

from app import prompts

MODEL = "gemini-3.8-flash"


def make_model() -> Gemini:
    """Returns the Gemini model shared by every agent, with retries on 429/5xx."""
    return Gemini(model=MODEL, retry_options=types.HttpRetryOptions(attempts=3))


def _specialist(name: str, description: str, instruction: str) -> Agent:
    return Agent(
        name=name,
        model=make_model(),
        mode="single_turn",
        description=description,
        instruction=instruction,
    )


def make_curriculum_planner() -> Agent:
    return _specialist(
        "curriculum_planner",
        "Breaks a Google Cloud / Google AI topic into an ordered outline of 3-7 "
        "subtopics, each with a one-sentence learning objective.",
        prompts.PLANNER_INSTRUCTION,
    )


def make_explainer() -> Agent:
    return _specialist(
        "explainer",
        "Writes the concept explanation (what it is, how it works, analogy, key "
        "takeaways, docs links) for ONE subtopic.",
        prompts.EXPLAINER_INSTRUCTION,
    )


def make_lab_designer() -> Agent:
    return _specialist(
        "lab_designer",
        "Writes a safe, hands-on Google Cloud lab (setup, steps, validation, "
        "cleanup) for ONE subtopic.",
        prompts.LAB_INSTRUCTION,
    )


def make_quiz_master() -> Agent:
    return _specialist(
        "quiz_master",
        "Writes a four-question knowledge-check quiz with answers and "
        "explanations for ONE subtopic.",
        prompts.QUIZ_INSTRUCTION,
    )
