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

"""ObservabilityPlugin: per-turn intent and outcome records.

Registered first so it sees every event; it never modifies anything. Logs are
JSON (see ``app.observability``) and the key facts are also added as attributes
on the current OpenTelemetry span, so they show up next to ADK's traces.
Learner text is never logged; only counts, names and statuses.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.models import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types
from opentelemetry import trace

from app.plugins.guardrails import _session_id
from app.plugins.tracker import Tracker
from app.tools import LESSON_SPECIALISTS

logger = logging.getLogger("enablement.turns")

CONFIRMATION_CALL = "adk_request_confirmation"


def classify_intent(function_call_names: list[str], has_text: bool) -> str | None:
    """Intent of the coach's first decision in a turn."""
    names = set(function_call_names)
    if "curriculum_planner" in names:
        return "new_topic"
    if "confirm_bulk_generation" in names:
        return "bulk_teach"
    if "confirm_costly_lab" in names:
        return "lab_cost_approval"
    if names & set(LESSON_SPECIALISTS):
        return "teach"
    if "PreloadMemory" in names or "preload_memory" in names:
        return None  # not a decision yet
    if has_text:
        return "chat"
    return None


def _set_span_attributes(attributes: dict[str, Any]) -> None:
    span = trace.get_current_span()
    if span and span.is_recording():
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(f"enablement.{key}", value)


class ObservabilityPlugin(BasePlugin):
    def __init__(self, tracker: Tracker, root_agent_name: str = "enablement_agent"):
        super().__init__(name="observability")
        self.tracker = tracker
        self.root_agent_name = root_agent_name

    async def before_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> types.Content | None:
        self.tracker.start_turn(invocation_context.session.id)
        return None

    async def on_user_message_callback(
        self, *, invocation_context: InvocationContext, user_message: types.Content
    ) -> types.Content | None:
        stats = self.tracker.turn(invocation_context.session.id)
        for part in (user_message.parts or []) if user_message else []:
            fr = part.function_response
            if fr and fr.name == CONFIRMATION_CALL:
                confirmed = bool((fr.response or {}).get("confirmed"))
                stats.hitl.append(
                    {
                        "kind": "confirmation",
                        "result": "approved" if confirmed else "rejected",
                    }
                )
                stats.intent = stats.intent or "hitl_response"
        return None

    async def on_event_callback(
        self, *, invocation_context: InvocationContext, event: Event
    ) -> Event | None:
        if event.author != self.root_agent_name or event.partial:
            return None
        stats = self.tracker.turn(invocation_context.session.id)
        names = [fc.name for fc in event.get_function_calls()]
        if CONFIRMATION_CALL in names:
            stats.hitl.append({"kind": "confirmation", "result": "requested"})
        if stats.intent is None:
            has_text = bool(
                event.content
                and any(p.text for p in (event.content.parts or []) if not p.thought)
            )
            stats.intent = classify_intent(names, has_text)
            if stats.intent:
                _set_span_attributes({"intent": stats.intent})
        return None

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> LlmResponse | None:
        usage = llm_response.usage_metadata
        if usage and usage.total_token_count:
            stats = self.tracker.turn(_session_id(callback_context))
            stats.tokens[callback_context.agent_name] += usage.total_token_count
        return None

    async def after_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> None:
        session = invocation_context.session
        stats = self.tracker.end_turn(session.id)
        record = {
            "session_id": session.id,
            "invocation_id": invocation_context.invocation_id,
            "user_id": session.user_id,
            "intent": stats.intent or "unknown",
            "specialist_calls": stats.calls,
            "retries": stats.retries,
            "guardrail_violations": stats.violations,
            "redactions": stats.redactions,
            "injection_signals": stats.injection,
            "hitl": stats.hitl,
            "routes": stats.routes,
            "tokens": dict(stats.tokens),
            "latency_s": round(time.monotonic() - stats.started, 2),
        }
        logger.info("turn outcome", extra=record)
        _set_span_attributes(
            {
                "intent": record["intent"],
                "route_tier": ",".join(sorted({r["tier"] for r in stats.routes})),
                "guardrail_violations": len(stats.violations),
                "retries": stats.retries,
                "hitl": ",".join(h["kind"] + ":" + h["result"] for h in stats.hitl),
            }
        )
