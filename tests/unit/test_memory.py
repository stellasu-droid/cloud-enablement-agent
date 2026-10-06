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

"""Memory selection, background ingest and service selection (no cloud calls)."""

import asyncio
from types import SimpleNamespace

from google.adk.events import Event
from google.genai import types

from app import memory
from app.app_utils import services
from tests.unit.samples import ARGS

ROOT = "enablement_agent"


def _user(text, inv="i1"):
    return Event(
        invocation_id=inv,
        author="user",
        content=types.Content(role="user", parts=[types.Part(text=text)]),
    )


def _call(name, args, cid, inv="i1"):
    return Event(
        invocation_id=inv,
        author=ROOT,
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(id=cid, name=name, args=args)
                )
            ],
        ),
    )


def _resp(name, status, cid, inv="i1"):
    return Event(
        invocation_id=inv,
        author=ROOT,
        content=types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=cid, name=name, response={"status": status}
                    )
                )
            ],
        ),
    )


def _lesson_text():
    return Event(
        invocation_id="i1",
        author="explainer",
        content=types.Content(
            role="model", parts=[types.Part(text="# Subtopic 2 ...")]
        ),
    )


def test_only_learner_messages_and_progress_note_are_ingested() -> None:
    events = [
        _user("old", inv="i0"),
        _user("2"),
        _call("explainer", ARGS, "a"),
        _lesson_text(),
        _resp("explainer", "ok", "a"),
        _call("quiz_master", ARGS, "b"),
        _resp("quiz_master", "ok", "b"),
    ]
    selected = memory.events_for_memory(events, "i1", ROOT)
    texts = [e.content.parts[0].text or "" for e in selected]
    assert texts[0] == "2"
    assert (
        "completed subtopic 2 'Deploying' of the course 'Cloud Run basics'" in texts[1]
    )
    assert all("# Subtopic" not in t for t in texts)  # lesson content never ingested


def test_failed_quiz_is_not_counted_as_completed() -> None:
    events = [
        _user("2"),
        _call("quiz_master", ARGS, "b"),
        _resp("quiz_master", "error", "b"),
    ]
    assert memory.progress_notes(events, "i1", ROOT) == []


def test_course_start_note_includes_level_and_goal() -> None:
    args = {
        "topic": "BigQuery",
        "learner_level": "beginner",
        "goals": "pass the PCA exam",
    }
    events = [
        _call("curriculum_planner", args, "p"),
        _resp("curriculum_planner", "ok", "p"),
    ]
    note = memory.progress_notes(events, "i1", ROOT)[0]
    assert "BigQuery" in note and "beginner" in note and "pass the PCA exam" in note


async def test_save_turn_runs_in_background_and_swallows_errors() -> None:
    started = asyncio.Event()

    async def slow_failing_ingest(**_):
        started.set()
        await asyncio.sleep(0.05)
        raise RuntimeError("memory bank down")

    session = SimpleNamespace(events=[_user("hello")])
    inv = SimpleNamespace(memory_service=object(), session=session, invocation_id="i1")
    ctx = SimpleNamespace(
        _invocation_context=inv,
        agent_name=ROOT,
        add_events_to_memory=slow_failing_ingest,
    )
    assert await memory.save_turn_to_memory(ctx) is None  # returns immediately
    await asyncio.wait_for(started.wait(), 1)
    await asyncio.sleep(0.1)  # failure is logged, not raised


def _ctx(events, **methods):
    session = SimpleNamespace(events=events)
    inv = SimpleNamespace(memory_service=object(), session=session, invocation_id="i1")
    return SimpleNamespace(_invocation_context=inv, agent_name=ROOT, **methods)


_LESSON_TURN = [
    _user("2"),
    _call("quiz_master", ARGS, "b"),
    _resp("quiz_master", "ok", "b"),
]


async def test_progress_goes_to_direct_memory_with_consolidation() -> None:
    calls = {}

    async def add_memory(memories, custom_metadata):
        calls["memories"] = [m.content.parts[0].text for m in memories]
        calls["memory_meta"] = custom_metadata

    async def add_events_to_memory(events, custom_metadata):
        calls["events"] = [e.content.parts[0].text for e in events]

    ctx = _ctx(
        _LESSON_TURN, add_memory=add_memory, add_events_to_memory=add_events_to_memory
    )
    await memory.save_turn_to_memory(ctx)
    await asyncio.gather(*memory._background)
    assert "completed subtopic 2" in calls["memories"][0]
    assert calls["memory_meta"] == {"enable_consolidation": True}
    assert calls["events"] == ["2"]  # progress is not duplicated into extraction


async def test_progress_falls_back_to_events_without_direct_writes() -> None:
    calls = {}

    async def add_memory(**_):
        raise NotImplementedError

    async def add_events_to_memory(events, custom_metadata):
        calls["events"] = [e.content.parts[0].text for e in events]

    ctx = _ctx(
        _LESSON_TURN, add_memory=add_memory, add_events_to_memory=add_events_to_memory
    )
    await memory.save_turn_to_memory(ctx)
    await asyncio.gather(*memory._background)
    assert calls["events"][0] == "2"
    assert "completed subtopic 2" in calls["events"][1]


async def test_save_turn_skipped_without_memory_service() -> None:
    inv = SimpleNamespace(memory_service=None)
    assert (
        await memory.save_turn_to_memory(SimpleNamespace(_invocation_context=inv))
        is None
    )


def test_memory_service_selection(monkeypatch) -> None:
    services.get_memory_service.cache_clear()
    monkeypatch.delenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", raising=False)
    assert type(services.get_memory_service()).__name__ == "InMemoryMemoryService"
    services.get_memory_service.cache_clear()
    monkeypatch.setenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", "123")
    monkeypatch.setenv("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION", "us-central1")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    svc = services.get_memory_service()
    assert type(svc).__name__ == "VertexAiMemoryBankService"
    services.get_memory_service.cache_clear()
