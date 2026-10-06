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

"""Plugin behaviour with fake events and contexts (no LLM calls)."""

import json
import logging
from types import SimpleNamespace

import pytest
from google.adk.events import Event
from google.adk.models import LlmRequest
from google.genai import types

from app.observability import JsonFormatter
from app.plugins.guardrails import (
    GuardrailPlugin,
    evaluate_output,
    guided_error,
    trim_coach_context,
)
from app.plugins.observability import ObservabilityPlugin, classify_intent
from app.plugins.routing import RoutingPlugin
from app.plugins.tracker import Tracker
from app.tools import APPROVED_LAB_KEY, PENDING_LAB_KEY
from tests.unit.samples import ARGS, EXPLAINER, OUTLINE, lab

SID = "s1"


def inv_ctx():
    return SimpleNamespace(
        session=SimpleNamespace(id=SID, user_id="u"), invocation_id="i1"
    )


def cb_ctx(agent, state=None):
    return SimpleNamespace(
        agent_name=agent,
        state=state if state is not None else {},
        _invocation_context=inv_ctx(),
    )


def call_event(name, args):
    return Event(
        author="enablement_agent",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(id="c1", name=name, args=args)
                )
            ],
        ),
    )


def text_event(author, payload):
    return Event(
        author=author,
        content=types.Content(
            role="model", parts=[types.Part(text=json.dumps(payload))]
        ),
    )


def response_event(name, response):
    return Event(
        author="enablement_agent",
        content=types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="c1", name=name, response=response
                    )
                )
            ],
        ),
    )


async def run_specialist(plugin, name, args, payload, fr=None):
    """Simulates: coach calls specialist -> specialist text -> coach receives FR."""
    await plugin.on_event_callback(
        invocation_context=inv_ctx(), event=call_event(name, args)
    )
    shown = await plugin.on_event_callback(
        invocation_context=inv_ctx(), event=text_event(name, payload)
    )
    resp = await plugin.on_event_callback(
        invocation_context=inv_ctx(),
        event=response_event(name, fr if fr is not None else payload),
    )
    return shown, resp.content.parts[0].function_response.response


@pytest.fixture
def plugin():
    return GuardrailPlugin(Tracker())


# --- pure helpers ----------------------------------------------------------------


def test_guided_error_budget() -> None:
    first = guided_error("explainer", 2, 1, "specialist_failed")
    second = guided_error("explainer", 2, 2, "specialist_failed")
    assert "once more" in first["recovery_hint"]
    assert second["recovery_hint"].startswith("Do NOT call explainer again")


def test_evaluate_output_cost_gate() -> None:
    _, outcome, delta = evaluate_output("lab_designer", lab(3.5), ARGS, threshold=2.0)
    assert outcome["status"] == "needs_approval"
    assert delta[PENDING_LAB_KEY]["subtopic_number"] == 2
    display, outcome, _ = evaluate_output("lab_designer", lab(1.5), ARGS, threshold=2.0)
    assert outcome["status"] == "ok" and display.startswith("## Lab")
    _, outcome, _ = evaluate_output(
        "lab_designer", lab(3.5), {**ARGS, "max_cost_usd": 2.0}, threshold=2.0
    )
    assert outcome["error_type"] == "over_budget"


# --- guardrail plugin --------------------------------------------------------------


async def test_markdown_shown_and_ok_envelope(plugin) -> None:
    shown, fr = await run_specialist(plugin, "explainer", ARGS, EXPLAINER)
    assert shown.content.parts[0].text.startswith("# Subtopic 2: Deploying")
    assert fr == {
        "status": "ok",
        "shown_to_learner": True,
        "summary": {"key_takeaways": EXPLAINER["key_takeaways"]},
        "next_step": "Now call lab_designer with the same arguments.",
    }


async def test_outline_keeps_full_data_for_coach(plugin) -> None:
    shown, fr = await run_specialist(
        plugin, "curriculum_planner", {"topic": "x"}, OUTLINE
    )
    assert shown.content.parts[0].text.startswith("**Course:")
    assert fr["data"] == OUTLINE


async def test_generic_error_gets_one_retry(plugin) -> None:
    generic = {"result": "Error running sub-agent: Dynamic node explainer failed"}
    await plugin.on_event_callback(
        invocation_context=inv_ctx(), event=call_event("explainer", ARGS)
    )
    first = await plugin.on_event_callback(
        invocation_context=inv_ctx(), event=response_event("explainer", generic)
    )
    second = await plugin.on_event_callback(
        invocation_context=inv_ctx(), event=response_event("explainer", generic)
    )
    r1 = first.content.parts[0].function_response.response
    r2 = second.content.parts[0].function_response.response
    assert (r1["attempt"], r2["attempt"]) == (1, 2)
    assert r1["error_type"] == "specialist_failed"
    assert "Do NOT call" in r2["recovery_hint"]


async def test_unsafe_lab_withheld(plugin) -> None:
    bad = lab(
        commands=[
            "gcloud projects add-iam-policy-binding $PROJECT_ID --role=roles/owner"
        ]
    )
    shown, fr = await run_specialist(plugin, "lab_designer", ARGS, bad)
    assert "held back by a safety check" in shown.content.parts[0].text
    assert "roles/owner" not in shown.content.parts[0].text
    assert fr["error_type"] == "unsafe_lab" and fr["attempt"] == 1


async def test_retry_after_unsafe_lab_tells_designer_what_to_avoid(plugin) -> None:
    bad = lab(commands=["rm -rf /tmp/x $PROJECT_ID"])
    await run_specialist(plugin, "lab_designer", ARGS, bad)
    req = LlmRequest()
    await plugin.before_model_callback(
        callback_context=cb_ctx("lab_designer"), llm_request=req
    )
    assert "rm_rf" in (req.config.system_instruction or "")


async def test_costly_lab_flow(plugin) -> None:
    costly = lab(3.5)
    shown, fr = await run_specialist(plugin, "lab_designer", ARGS, costly)
    assert "Waiting for your approval" in shown.content.parts[0].text
    assert "gcloud run deploy" not in shown.content.parts[0].text
    assert fr["status"] == "needs_approval" and fr["estimated_cost_usd"] == 3.5
    assert shown.actions.state_delta[PENDING_LAB_KEY]["lab"] == costly

    # Learner approves (confirm_costly_lab sets APPROVED_LAB_KEY), coach calls again.
    state = {
        PENDING_LAB_KEY: shown.actions.state_delta[PENDING_LAB_KEY],
        APPROVED_LAB_KEY: 2,
    }
    cached = await plugin.before_model_callback(
        callback_context=cb_ctx("lab_designer", state), llm_request=LlmRequest()
    )
    assert json.loads(cached.content.parts[0].text) == costly  # no model call
    assert state[PENDING_LAB_KEY] is None
    shown2, fr2 = await run_specialist(plugin, "lab_designer", ARGS, costly)
    assert shown2.content.parts[0].text.startswith("## Lab")
    assert "about $3.50" in shown2.content.parts[0].text
    assert fr2["status"] == "ok"


async def test_cheap_lab_shown_straight_away(plugin) -> None:
    shown, fr = await run_specialist(plugin, "lab_designer", ARGS, lab(1.5))
    assert shown.content.parts[0].text.startswith("## Lab")
    assert fr["status"] == "ok"


async def test_user_message_redacted(plugin) -> None:
    msg = types.Content(
        role="user", parts=[types.Part(text="I'm jane@acme.io, key AIza" + "B" * 35)]
    )
    out = await plugin.on_user_message_callback(
        invocation_context=inv_ctx(), user_message=msg
    )
    text = out.parts[0].text
    assert "jane@acme.io" not in text and "[REDACTED:email]" in text
    assert "[REDACTED:api_key]" in text
    assert plugin.tracker.turn(SID).redactions == ["api_key", "email"]


async def test_clean_user_message_untouched(plugin) -> None:
    msg = types.Content(role="user", parts=[types.Part(text="Teach me BigQuery")])
    assert (
        await plugin.on_user_message_callback(
            invocation_context=inv_ctx(), user_message=msg
        )
        is None
    )


# --- routing plugin ------------------------------------------------------------------


async def test_routing_sets_model_and_escalates_on_retry() -> None:
    tracker = Tracker()
    router = RoutingPlugin(tracker)
    tracker.set_args(SID, "quiz_master", ARGS)
    req = LlmRequest(model="x", config=types.GenerateContentConfig())
    await router.before_model_callback(
        callback_context=cb_ctx("quiz_master"), llm_request=req
    )
    assert req.model == "gemini-3.5-flash"
    assert req.config.labels["route_tier"] == "fast"
    tracker.record_failure(SID, "quiz_master", 2)
    await router.before_model_callback(
        callback_context=cb_ctx("quiz_master"), llm_request=req
    )
    assert req.model == "gemini-3.8-flash"


# --- observability -------------------------------------------------------------------


@pytest.mark.parametrize(
    "names,has_text,intent",
    [
        (["curriculum_planner"], False, "new_topic"),
        (["explainer"], False, "teach"),
        (["confirm_bulk_generation"], False, "bulk_teach"),
        (["confirm_costly_lab"], False, "lab_cost_approval"),
        ([], True, "chat"),
        (["preload_memory"], False, None),
    ],
)
def test_classify_intent(names, has_text, intent) -> None:
    assert classify_intent(names, has_text) == intent


async def test_outcome_record_is_json(caplog) -> None:
    tracker = Tracker()
    obs = ObservabilityPlugin(tracker)
    await obs.before_run_callback(invocation_context=inv_ctx())
    await obs.on_event_callback(
        invocation_context=inv_ctx(),
        event=call_event("curriculum_planner", {"topic": "x"}),
    )
    with caplog.at_level(logging.INFO, logger="enablement.turns"):
        logging.getLogger("enablement.turns").propagate = True
        await obs.after_run_callback(invocation_context=inv_ctx())
    record = next(r for r in caplog.records if r.getMessage() == "turn outcome")
    line = json.loads(JsonFormatter().format(record))
    assert line["severity"] == "INFO" and line["intent"] == "new_topic"
    assert line["session_id"] == SID


def test_json_formatter_redacts() -> None:
    record = logging.LogRecord(
        "enablement.x", logging.INFO, "", 0, "user jane@acme.io", (), None
    )
    record.detail = "key AIza" + "C" * 35
    line = json.loads(JsonFormatter().format(record))
    assert "jane@acme.io" not in line["message"]
    assert "[REDACTED:api_key]" in line["detail"]


def test_trim_coach_context_drops_current_turn_noise_only() -> None:
    def user(text):
        return types.Content(role="user", parts=[types.Part(text=text)])

    quoted = "For context: [explainer] said: <<<BEGIN_QUOTED_AGENT_CONTENT>>> old"
    echo = json.dumps(ARGS)
    contents = [
        user("1"),
        user(echo),
        user(quoted),  # earlier turn: kept for follow-up questions
        user("2"),
        user(echo),
        user(quoted.replace("old", "new")),  # current turn: dropped
    ]
    texts = [c.parts[0].text for c in trim_coach_context(contents)]
    assert texts == ["1", quoted, "2"]
