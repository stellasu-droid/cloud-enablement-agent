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

"""Typed contracts between the coach and its specialists.

Input models become each specialist's tool parameters (every field description
is shown to the coach model). Output models are enforced as structured output,
so the coach receives parsed JSON instead of free text.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Level = Literal["beginner", "intermediate", "advanced"]
Letter = Literal["A", "B", "C", "D"]

# ---------------------------------------------------------------------------
# Inputs (what the coach passes to a specialist)
# ---------------------------------------------------------------------------


class TopicRequest(BaseModel):
    """Input for curriculum_planner."""

    topic: str = Field(
        description="Google Cloud or Google AI topic to teach, e.g. 'Cloud Run basics'."
    )
    learner_level: Level = Field(
        description="The learner's level. Use what they said or what memory recalls; 'intermediate' if unknown."
    )
    goals: str = Field(
        default="",
        description="Optional learner goals or constraints, e.g. 'preparing for the Associate Cloud Engineer exam'.",
    )


class SubtopicRequest(BaseModel):
    """Input for explainer, lab_designer and quiz_master."""

    topic: str = Field(description="Overall course topic, exactly as in the outline.")
    subtopic_number: int = Field(
        ge=1, description="1-based number of the subtopic in the outline."
    )
    subtopic_title: str = Field(description="Exact subtopic title from the outline.")
    learning_objective: str = Field(
        description="The subtopic's learning objective from the outline."
    )
    learner_level: Level = Field(description="The learner's level.")
    complexity: int = Field(
        ge=1,
        le=5,
        description="Subtopic complexity from the outline, 1 (easy) to 5 (hard).",
    )
    max_cost_usd: float | None = Field(
        default=None,
        description=(
            "lab_designer only. Leave empty normally. Set to 2.0 only after the "
            "learner declined a costly lab, to request a cheaper variant."
        ),
    )


# ---------------------------------------------------------------------------
# Outputs (what a specialist returns)
# ---------------------------------------------------------------------------


class OutlineItem(BaseModel):
    number: int = Field(ge=1, description="1-based position in the course.")
    title: str = Field(description="Short subtopic title, at most 8 words.")
    objective: str = Field(
        description="One-sentence learning objective starting with a verb."
    )
    complexity: int = Field(ge=1, le=5, description="1 (easy) to 5 (hard).")


class CourseOutline(BaseModel):
    topic: str = Field(description="The course topic.")
    subtopics: list[OutlineItem] = Field(
        min_length=3, max_length=7, description="3 to 7 subtopics in study order."
    )


class DocLink(BaseModel):
    title: str = Field(description="Page title.")
    url: str = Field(
        description="Official documentation URL on cloud.google.com or ai.google.dev."
    )


class Explainer(BaseModel):
    what_it_is: str = Field(
        description="Two or three short paragraphs: the concept and why it matters."
    )
    how_it_works: list[str] = Field(
        min_length=2, description="Key mechanics, one bullet each."
    )
    mermaid: str | None = Field(
        default=None,
        description="Optional small Mermaid flowchart (at most 10 nodes), without code fences.",
    )
    analogy: str = Field(description="One everyday analogy in two or three sentences.")
    key_takeaways: list[str] = Field(
        min_length=3, max_length=5, description="Three to five takeaways."
    )
    learn_more: list[DocLink] = Field(
        min_length=1, max_length=3, description="One to three official doc links."
    )


class LabStep(BaseModel):
    purpose: str = Field(description="One-line purpose of the step.")
    commands: list[str] = Field(description="Shell commands, one per item.")
    expected_result: str = Field(description="What the learner should see.")


class LabGuide(BaseModel):
    goal: str = Field(description="One-sentence goal of the lab.")
    estimated_cost_usd: float = Field(
        ge=0,
        description="Honest estimate of the total cost in USD to run the lab once.",
    )
    cost_drivers: list[str] = Field(
        description="Resources that drive the cost, e.g. 'GKE Standard, 3 x e2-standard-4 for 1 hour'."
    )
    prerequisites: list[str] = Field(description="What the learner needs first.")
    apis_to_enable: list[str] = Field(
        description="Service names, e.g. 'run.googleapis.com'."
    )
    setup_commands: list[str] = Field(
        description="Shell variable exports, starting with PROJECT_ID and REGION."
    )
    steps: list[LabStep] = Field(min_length=1, description="Numbered lab steps.")
    validation: list[str] = Field(
        min_length=1, description="Checks that prove the lab worked."
    )
    cleanup_commands: list[str] = Field(
        min_length=1, description="Commands that delete every resource created."
    )


class QuizQuestion(BaseModel):
    question: str = Field(description="The question text.")
    options: list[str] = Field(
        min_length=4, max_length=4, description="Exactly four options, in order A-D."
    )
    answer: Letter = Field(description="Letter of the single correct option.")
    explanation: str = Field(
        description="Why the answer is right and the most tempting wrong option is wrong."
    )


class Quiz(BaseModel):
    questions: list[QuizQuestion] = Field(
        min_length=4, max_length=4, description="Exactly four questions."
    )


OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "curriculum_planner": CourseOutline,
    "explainer": Explainer,
    "lab_designer": LabGuide,
    "quiz_master": Quiz,
}
