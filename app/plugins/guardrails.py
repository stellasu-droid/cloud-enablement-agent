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

"""GuardrailPlugin: input guard, output guard, rendering and guided errors.

- Input: redacts PII/secrets from learner messages before any model or log sees
  them, and flags prompt-injection attempts.
- Output: validates each specialist's JSON, lints labs for unsafe commands,
  holds back labs above the cost limit until the learner approves, and shows the
  learner rendered markdown instead of JSON.
- Tool results: rewrites the coach's function responses into a uniform
  ``{"status": ...}`` envelope with a ``recovery_hint`` and a retry budget of one.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.models import LlmRequest, LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types
from pydantic import ValidationError

from app import guardrails, render
from app.plugins.tracker import Tracker
from app.schemas import OUTPUT_MODELS, LabGuide
from app.tools import (
    APPROVED_LAB_KEY,
    PENDING_LAB_KEY,
    SPECIALISTS,
    lab_cost_threshold,
)

logger = logging.getLogger("enablement.guardrails")

GENERIC_ERROR_PREFIX = "Error running sub-agent"
MAX_ATTEMPTS = 2  # first try + one retry


def _session_id(ctx: Any) -> str:
    inv = getattr(ctx, "_invocation_context", ctx)
    return inv.session.id


def _text(event: Event) -> str:
    if not event.content or not event.content.parts:
        return ""
    return "".join(p.text or "" for p in event.content.parts if not p.thought)


def _replace_text(event: Event, text: str) -> None:
    event.content = types.Content(role="model", parts=[types.Part(text=text)])


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested directly)
# ---------------------------------------------------------------------------


def guided_error(
    agent: str, subtopic: int, attempt: int, error_type: str, detail: Any = None
) -> dict[str, Any]:
    """Builds the error envelope the coach sees, with a recovery hint."""
    if error_type == "over_budget":
        hint = (
            "Do NOT call lab_designer again for this subtopic. Tell the learner in "
            "one sentence that even the cheapest lab variant is above the cost "
            "limit, so the lab is skipped, then call quiz_master."
        )
    elif attempt < MAX_ATTEMPTS:
        hint = (
            f"Call {agent} once more with the same arguments; a stronger model "
            "will be used"
            + (
                "; the listed safety issues will be avoided."
                if error_type == "unsafe_lab"
                else "."
            )
        )
    else:
        hint = (
            f"Do NOT call {agent} again this turn. Tell the learner in one "
            "sentence that this section is unavailable right now and offer to "
            "try again later, then continue with the remaining specialists."
        )
    envelope: dict[str, Any] = {
        "status": "error",
        "error_type": error_type,
        "specialist": agent,
        "subtopic_number": subtopic,
        "attempt": attempt,
        "recovery_hint": hint,
    }
    if detail:
        envelope["detail"] = detail
    return envelope


def evaluate_output(
    agent: str,
    data: dict[str, Any],
    args: dict[str, Any],
    *,
    approved: bool = False,
    threshold: float = 2.0,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Decides what the learner sees and what the coach is told.

    Returns (display_markdown, outcome, state_delta). ``outcome["status"]`` is
    "ok", "needs_approval" or "error".
    """
    try:
        model = OUTPUT_MODELS[agent].model_validate(data)
    except ValidationError as e:
        return (
            "",
            {"status": "error", "error_type": "schema_invalid", "detail": str(e)[:300]},
            {},
        )

    if isinstance(model, LabGuide):
        violations = guardrails.lint_lab(model)
        if violations:
            return (
                "## Lab\n\n_The lab draft was held back by a safety check. "
                "Preparing a safer version._",
                {
                    "status": "error",
                    "error_type": "unsafe_lab",
                    "detail": [f"{v.rule}: {v.detail}" for v in violations],
                },
                {},
            )
        if model.estimated_cost_usd > threshold and not approved:
            if args.get("max_cost_usd") is not None:
                return (
                    "",
                    {
                        "status": "error",
                        "error_type": "over_budget",
                        "detail": f"cheapest variant ~${model.estimated_cost_usd:.2f}",
                    },
                    {},
                )
            drivers = "; ".join(model.cost_drivers) or "cloud resources"
            return (
                f"## Lab\n\n_This lab is estimated to cost about "
                f"${model.estimated_cost_usd:.2f} ({drivers}). Waiting for your "
                f"approval before showing it._",
                {
                    "status": "needs_approval",
                    "subtopic_number": args.get("subtopic_number"),
                    "estimated_cost_usd": model.estimated_cost_usd,
                    "cost_drivers": model.cost_drivers,
                    "recovery_hint": (
                        "Call confirm_costly_lab with these values. If approved, "
                        "call lab_designer again with the same arguments. If "
                        "rejected, call lab_designer again with max_cost_usd 2.0."
                    ),
                },
                {
                    PENDING_LAB_KEY: {
                        "subtopic_number": args.get("subtopic_number"),
                        "lab": data,
                    },
                    APPROVED_LAB_KEY: None,
                },
            )

    return render.render(agent, data, args), {"status": "ok"}, {}


# Deterministic hint for what the coach does after each successful specialist,
# so the explainer -> lab -> quiz sequence doesn't depend on the model's memory.
NEXT_STEP = {
    "curriculum_planner": "Reply with exactly one line: Pick a subtopic number, say next, or say all.",
    "explainer": "Now call lab_designer with the same arguments.",
    "lab_designer": "Now call quiz_master with the same arguments (without max_cost_usd).",
    "quiz_master": (
        "If more approved subtopics remain, continue with the next one's explainer. "
        "Otherwise reply with one short line suggesting what to do next."
    ),
}


def ok_envelope(agent: str, data: dict[str, Any]) -> dict[str, Any]:
    """Success envelope. Lesson content is already on screen, so the coach gets
    a compact summary instead of the full payload (saves context tokens)."""
    if agent == "curriculum_planner":
        return {
            "status": "ok",
            "shown_to_learner": True,
            "data": data,
            "next_step": NEXT_STEP[agent],
        }
    summary: dict[str, Any] = {}
    if agent == "explainer":
        summary = {"key_takeaways": data.get("key_takeaways", [])}
    elif agent == "lab_designer":
        summary = {
            "goal": data.get("goal"),
            "estimated_cost_usd": data.get("estimated_cost_usd"),
        }
    elif agent == "quiz_master":
        summary = {
            "answers": [q.get("answer") for q in data.get("questions", [])],
        }
    return {
        "status": "ok",
        "shown_to_learner": True,
        "summary": summary,
        "next_step": NEXT_STEP[agent],
    }


QUOTED_MARKERS = ("For context:", "<<<BEGIN_QUOTED_AGENT_CONTENT>>>")


def _is_input_echo(part: types.Part) -> bool:
    """A specialist's input arguments, replayed into history as a user text."""
    text = (part.text or "").strip()
    if not (text.startswith("{") and text.endswith("}")):
        return False
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and "topic" in data


def _is_quoted(part: types.Part) -> bool:
    return bool(part.text) and any(m in part.text for m in QUOTED_MARKERS)


def trim_coach_context(contents: list[types.Content]) -> list[types.Content]:
    """Removes history noise that makes the coach stall mid-lesson.

    ADK replays each specialist's input as a user text and quotes its output
    ("For context: ... said ..."). Within the current turn the coach already
    has the structured function responses, and live tests showed these extra
    user messages make it end the turn with an empty reply instead of calling
    the next specialist. Quotes from earlier turns are kept so the coach can
    answer follow-up questions about lessons already shown.
    """
    last_user_turn = -1
    for i, c in enumerate(contents):
        if c.role == "user" and any(
            p.text and not _is_quoted(p) and not _is_input_echo(p)
            for p in c.parts or []
        ):
            last_user_turn = i
    trimmed: list[types.Content] = []
    for i, c in enumerate(contents):
        parts = [
            p
            for p in c.parts or []
            if not _is_input_echo(p) and not (i > last_user_turn and _is_quoted(p))
        ]
        if parts:
            trimmed.append(types.Content(role=c.role, parts=parts))
    return trimmed


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class GuardrailPlugin(BasePlugin):
    def __init__(self, tracker: Tracker, root_agent_name: str = "enablement_agent"):
        super().__init__(name="guardrails")
        self.tracker = tracker
        self.root_agent_name = root_agent_name

    # --- input guard --------------------------------------------------------
    async def on_user_message_callback(
        self, *, invocation_context: InvocationContext, user_message: types.Content
    ) -> types.Content | None:
        if not user_message or not user_message.parts:
            return None
        sid = invocation_context.session.id
        stats = self.tracker.turn(sid)
        changed = False
        parts: list[types.Part] = []
        for part in user_message.parts:
            if part.text:
                stats.injection += guardrails.injection_signals(part.text)
                redacted, kinds = guardrails.redact_pii(part.text)
                if kinds:
                    stats.redactions += kinds
                    changed = True
                    part = types.Part(text=redacted)
            parts.append(part)
        if stats.injection:
            logger.warning(
                "prompt injection signals",
                extra={"session_id": sid, "signals": stats.injection},
            )
        if not changed:
            return None
        return types.Content(role=user_message.role or "user", parts=parts)

    # --- coach context trim; serve an approved costly lab without a model call
    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        if callback_context.agent_name == self.root_agent_name:
            llm_request.contents = trim_coach_context(list(llm_request.contents))
            return None
        if callback_context.agent_name != "lab_designer":
            return None
        sid = _session_id(callback_context)
        state = callback_context.state
        pending = state.get(PENDING_LAB_KEY)
        approved = state.get(APPROVED_LAB_KEY)
        if (
            pending
            and approved is not None
            and approved == pending.get("subtopic_number")
        ):
            state[PENDING_LAB_KEY] = None
            state[APPROVED_LAB_KEY] = None
            self.tracker.mark_served_from_cache(sid)
            self.tracker.turn(sid).hitl.append(
                {"kind": "lab_cost", "result": "approved", "subtopic": approved}
            )
            return LlmResponse(
                content=types.Content(
                    role="model", parts=[types.Part(text=json.dumps(pending["lab"]))]
                )
            )
        # Retry after an unsafe draft: tell the lab designer what to avoid.
        last = self.tracker.args(sid, "__unsafe_lab__")
        if last.get("violations"):
            llm_request.append_instructions(
                [
                    "Your previous draft was rejected by a safety check for: "
                    + "; ".join(last["violations"])
                    + ". Write a lab that avoids all of these."
                ]
            )
        return None

    # --- output guard + guided errors ---------------------------------------
    async def on_event_callback(
        self, *, invocation_context: InvocationContext, event: Event
    ) -> Event | None:
        sid = invocation_context.session.id
        author = event.author

        # 1. Remember the arguments of each specialist call made by the coach.
        if author == self.root_agent_name:
            for fc in event.get_function_calls():
                if fc.name in SPECIALISTS:
                    self.tracker.set_args(sid, fc.name, fc.args or {})

        # 2. Specialist output: validate, lint, gate on cost, render markdown.
        if author in SPECIALISTS and event.content and event.content.parts:
            if event.get_function_calls() or event.get_function_responses():
                return None
            if event.partial:
                _replace_text(event, "")  # never stream raw JSON to the learner
                return event
            text = _text(event).strip()
            if not text:
                return None
            args = self.tracker.args(sid, author)
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                self.tracker.set_outcome(
                    sid, author, {"status": "error", "error_type": "schema_invalid"}
                )
                _replace_text(event, "")
                return event
            approved = author == "lab_designer" and self.tracker.take_served_from_cache(
                sid
            )
            display, outcome, state_delta = evaluate_output(
                author, data, args, approved=approved, threshold=lab_cost_threshold()
            )
            self.tracker.set_outcome(sid, author, {**outcome, "data": data})
            if outcome.get("error_type") == "unsafe_lab":
                self.tracker.turn(sid).violations += outcome["detail"]
                self.tracker.set_args(
                    sid, "__unsafe_lab__", {"violations": outcome["detail"]}
                )
            elif author == "lab_designer":
                self.tracker.set_args(sid, "__unsafe_lab__", {})
            if outcome["status"] == "needs_approval":
                self.tracker.turn(sid).hitl.append(
                    {
                        "kind": "lab_cost",
                        "result": "requested",
                        "estimated_cost_usd": outcome["estimated_cost_usd"],
                    }
                )
            if state_delta:
                event.actions.state_delta.update(state_delta)
            _replace_text(event, display)
            return event

        # 3. Coach-facing function responses: uniform envelope + retry budget.
        changed = False
        for fr in event.get_function_responses():
            if fr.name not in SPECIALISTS:
                continue
            response = fr.response if isinstance(fr.response, dict) else {}
            args = self.tracker.args(sid, fr.name)
            subtopic = int(args.get("subtopic_number") or 0)
            outcome = self.tracker.pop_outcome(sid, fr.name)
            generic_error = str(response.get("result", "")).startswith(
                GENERIC_ERROR_PREFIX
            )

            if outcome and outcome["status"] == "ok" and not generic_error:
                fr.response = ok_envelope(fr.name, outcome["data"])
            elif outcome and outcome["status"] == "needs_approval":
                fr.response = {k: v for k, v in outcome.items() if k != "data"}
            else:
                error_type = (outcome or {}).get("error_type") or "specialist_failed"
                detail = (outcome or {}).get("detail")
                if generic_error and not outcome:
                    detail = str(response.get("result"))[:300]
                attempt = (
                    1
                    if error_type == "over_budget"
                    else self.tracker.record_failure(sid, fr.name, subtopic)
                )
                fr.response = guided_error(
                    fr.name, subtopic, attempt, error_type, detail
                )
                logger.warning(
                    "specialist error",
                    extra={
                        "session_id": sid,
                        "agent": fr.name,
                        "error_type": error_type,
                        "attempt": attempt,
                    },
                )
            changed = True
            self.tracker.turn(sid).calls.append(
                {
                    "agent": fr.name,
                    "subtopic": subtopic or None,
                    "status": fr.response.get("status"),
                    "error_type": fr.response.get("error_type"),
                }
            )
        return event if changed else None
