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

"""RoutingPlugin: picks the Gemini model for every model request."""

from __future__ import annotations

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.adk.plugins.base_plugin import BasePlugin

from app.plugins.guardrails import _session_id
from app.plugins.tracker import Tracker
from app.routing import choose_tier, model_for


class RoutingPlugin(BasePlugin):
    def __init__(self, tracker: Tracker):
        super().__init__(name="routing")
        self.tracker = tracker

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        agent = callback_context.agent_name
        sid = _session_id(callback_context)
        args = self.tracker.args(sid, agent)
        subtopic = int(args.get("subtopic_number") or 0)
        tier, reason = choose_tier(
            agent,
            complexity=args.get("complexity"),
            learner_level=args.get("learner_level"),
            attempt=self.tracker.attempt(sid, agent, subtopic),
        )
        model = model_for(tier)
        llm_request.model = model
        if llm_request.config is not None:
            labels = dict(llm_request.config.labels or {})
            labels.update({"agent": agent, "route_tier": tier})
            llm_request.config.labels = labels
        self.tracker.turn(sid).routes.append(
            {"agent": agent, "tier": tier, "model": model, "reason": reason}
        )
        return None
