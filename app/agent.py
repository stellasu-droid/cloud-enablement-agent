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

"""Enablement Agent: a coach that turns a Google Cloud / AI topic into lessons.

Architecture: one coordinator (``enablement_agent``, the coach the learner
talks to) plus four ``single_turn`` specialist sub-agents that ADK exposes to
the coordinator as tools.
"""

from google.adk.agents import Agent
from google.adk.apps import App

from app import prompts
from app.agents import (
    make_curriculum_planner,
    make_explainer,
    make_lab_designer,
    make_model,
    make_quiz_master,
)

root_agent = Agent(
    # Keep in sync with agents-cli-manifest.yaml: agents-cli derives this name
    # from the project `name:` recorded there, and telemetry reports it as
    # gen_ai.agent.name. Renaming the agent only here makes the two disagree,
    # and anything selecting traces by name stops finding this agent's.
    name="enablement_agent",
    model=make_model(),
    description="Enablement coach for Google Cloud and Google AI topics.",
    instruction=prompts.COACH_INSTRUCTION,
    sub_agents=[
        make_curriculum_planner(),
        make_explainer(),
        make_lab_designer(),
        make_quiz_master(),
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)
