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

"""Deterministic markdown rendering of specialist outputs for the learner."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.schemas import CourseOutline, Explainer, LabGuide, Quiz

LETTERS = "ABCD"


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _bash(commands: list[str]) -> str:
    return "```bash\n" + "\n".join(commands) + "\n```"


def render_outline(outline: CourseOutline) -> str:
    lines = [f"**Course: {outline.topic}**", ""]
    lines += [
        f"{item.number}. **{item.title}** - {item.objective}"
        for item in outline.subtopics
    ]
    return "\n".join(lines)


def render_explainer(explainer: Explainer, number: int | None, title: str) -> str:
    heading = f"# Subtopic {number}: {title}" if number else f"# {title}"
    parts = [
        heading,
        "## Explainer",
        f"**What it is**\n\n{explainer.what_it_is}",
        f"**How it works**\n\n{_bullets(explainer.how_it_works)}",
    ]
    if explainer.mermaid and explainer.mermaid.strip():
        body = explainer.mermaid.strip().removeprefix("```mermaid").removesuffix("```")
        parts.append(f"```mermaid\n{body.strip()}\n```")
    parts += [
        f"**Analogy**\n\n{explainer.analogy}",
        f"**Key takeaways**\n\n{_bullets(explainer.key_takeaways)}",
        "**Learn more**\n\n"
        + _bullets([f"[{link.title}]({link.url})" for link in explainer.learn_more]),
    ]
    return "\n\n".join(parts)


def render_lab(lab: LabGuide) -> str:
    parts = [
        "## Lab",
        f"**Goal** - {lab.goal}",
        f"**Estimated cost** - about ${lab.estimated_cost_usd:.2f}"
        + (f" ({'; '.join(lab.cost_drivers)})" if lab.cost_drivers else ""),
        "**Prerequisites**\n\n" + _bullets(lab.prerequisites),
    ]
    if lab.apis_to_enable:
        parts.append(
            "Enable the APIs:\n\n"
            + _bash([f"gcloud services enable {' '.join(lab.apis_to_enable)}"])
        )
    parts.append("**Setup**\n\n" + _bash(lab.setup_commands))
    steps = ["**Steps**"]
    for i, step in enumerate(lab.steps, 1):
        block = f"{i}. {step.purpose}"
        if step.commands:
            block += "\n\n" + _bash(step.commands)
        block += f"\n\n   Expected result: {step.expected_result}"
        steps.append(block)
    parts.append("\n\n".join(steps))
    parts.append(
        "**Validate**\n\n" + "\n".join(f"- [ ] {check}" for check in lab.validation)
    )
    parts.append("**Clean up**\n\n" + _bash(lab.cleanup_commands))
    return "\n\n".join(parts)


def render_quiz(quiz: Quiz) -> str:
    parts = ["## Quiz"]
    for i, q in enumerate(quiz.questions, 1):
        options = "\n".join(
            f"   {LETTERS[j]}. {opt}" for j, opt in enumerate(q.options)
        )
        parts.append(f"{i}. {q.question}\n\n{options}")
    answers = "\n".join(
        f"{i}. **{q.answer}** - {q.explanation}"
        for i, q in enumerate(quiz.questions, 1)
    )
    parts.append(f"**Answers**\n\n{answers}")
    return "\n\n".join(parts)


def render(specialist: str, data: Mapping[str, Any], args: Mapping[str, Any]) -> str:
    """Renders a validated specialist payload; raises on invalid data."""
    if specialist == "curriculum_planner":
        return render_outline(CourseOutline.model_validate(data))
    if specialist == "explainer":
        return render_explainer(
            Explainer.model_validate(data),
            args.get("subtopic_number"),
            args.get("subtopic_title") or "Explainer",
        )
    if specialist == "lab_designer":
        return render_lab(LabGuide.model_validate(data))
    if specialist == "quiz_master":
        return render_quiz(Quiz.model_validate(data))
    raise ValueError(f"Unknown specialist: {specialist}")
