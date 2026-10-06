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
the coordinator as typed tools. Cross-cutting concerns live in App plugins:
observability, guardrails (incl. the lab cost gate) and model routing.
"""

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.apps.app import EventsCompactionConfig
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.models import Gemini
from google.adk.tools.preload_memory_tool import PreloadMemoryTool

from app import prompts
from app.agents import (
    make_curriculum_planner,
    make_explainer,
    make_lab_designer,
    make_model,
    make_quiz_master,
)
from app.memory import save_turn_to_memory
from app.observability import setup_logging
from app.plugins.guardrails import GuardrailPlugin
from app.plugins.observability import ObservabilityPlugin
from app.plugins.routing import RoutingPlugin
from app.plugins.tracker import Tracker
from app.routing import model_for
from app.tools import confirm_bulk_tool, confirm_lab_tool

ROOT_AGENT_NAME = "enablement_agent"

setup_logging()

root_agent = Agent(
    # Keep in sync with agents-cli-manifest.yaml: agents-cli derives this name
    # from the project `name:` recorded there, and telemetry reports it as
    # gen_ai.agent.name. Renaming the agent only here makes the two disagree,
    # and anything selecting traces by name stops finding this agent's.
    name=ROOT_AGENT_NAME,
    model=make_model(ROOT_AGENT_NAME),
    description="Enablement coach for Google Cloud and Google AI topics.",
    instruction=prompts.COACH_INSTRUCTION,
    tools=[PreloadMemoryTool(), confirm_bulk_tool, confirm_lab_tool],
    sub_agents=[
        make_curriculum_planner(),
        make_explainer(),
        make_lab_designer(),
        make_quiz_master(),
    ],
    after_agent_callback=save_turn_to_memory,
)

_tracker = Tracker()

app = App(
    root_agent=root_agent,
    name="app",
    # Order matters: plugin callbacks stop at the first non-None result.
    # Observability only observes (always None), guardrails may replace events
    # or serve a cached lab, routing only edits the model request.
    plugins=[
        ObservabilityPlugin(_tracker, ROOT_AGENT_NAME),
        GuardrailPlugin(_tracker, ROOT_AGENT_NAME),
        RoutingPlugin(_tracker),
    ],
    # Summarize older turns so long courses don't fill the context window.
    events_compaction_config=EventsCompactionConfig(
        compaction_interval=4,
        overlap_size=1,
        summarizer=LlmEventSummarizer(llm=Gemini(model=model_for("fast"))),
    ),
)
