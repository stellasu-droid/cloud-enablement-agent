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

"""Structural tests for the multi-agent wiring (no LLM calls)."""

from google.adk.flows.llm_flows.agent_transfer import _get_transfer_targets

from app import prompts
from app.agent import app, root_agent

SPECIALISTS = ["curriculum_planner", "explainer", "lab_designer", "quiz_master"]


def test_app_name_matches_directory() -> None:
    assert app.name == "app"
    assert app.root_agent is root_agent


def test_root_has_expected_specialists() -> None:
    assert [a.name for a in root_agent.sub_agents] == SPECIALISTS


def test_specialists_are_single_turn_with_descriptions() -> None:
    for agent in root_agent.sub_agents:
        assert agent.mode == "single_turn", agent.name
        assert agent.description, agent.name


def test_specialists_exposed_as_tools_not_transfer_targets() -> None:
    assert [t.name for t in root_agent.tools] == SPECIALISTS
    assert _get_transfer_targets(root_agent) == []


def test_instructions_have_no_state_placeholders() -> None:
    for name in dir(prompts):
        if name.endswith("_INSTRUCTION"):
            text = getattr(prompts, name)
            assert "{" not in text and "}" not in text, name


def test_lab_instruction_contains_safety_rules() -> None:
    lab = prompts.LAB_INSTRUCTION
    for phrase in ["$PROJECT_ID", "roles/owner", "allUsers", "Clean up"]:
        assert phrase in lab, phrase


def test_coach_does_not_repeat_and_specialists_label_themselves() -> None:
    assert "NEVER repeat" in prompts.COACH_INSTRUCTION
    assert "# Subtopic N" in prompts.EXPLAINER_INSTRUCTION
    assert "## Explainer" in prompts.EXPLAINER_INSTRUCTION
    assert "## Lab" in prompts.LAB_INSTRUCTION
    assert "## Quiz" in prompts.QUIZ_INSTRUCTION
