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

"""Asynchronous long-term memory writes (Agent Platform Memory Bank).

After each coach turn, only the learner's own messages are sent to Memory Bank
for topic extraction (level, goals), never the generated lessons. Course
progress is written as direct memories with server-side consolidation. Both
writes run as a background task, so the learner never waits for them.
"""

from __future__ import annotations

import asyncio
import logging

from google.adk.agents.callback_context import CallbackContext
from google.adk.events import Event
from google.adk.memory.memory_entry import MemoryEntry
from google.genai import types

logger = logging.getLogger("enablement.memory")

# Generate memories once a session has been idle for a minute.
INGEST_METADATA = {
    "generation_trigger_config": {"generation_rule": {"idle_duration": "60s"}}
}

_background: set[asyncio.Task] = set()


def progress_notes(events: list[Event], invocation_id: str, root: str) -> list[str]:
    """Plain-language facts about what happened in this turn."""
    statuses: dict[str, str] = {}
    for e in events:
        if e.invocation_id != invocation_id:
            continue
        for fr in e.get_function_responses():
            if fr.id and isinstance(fr.response, dict):
                statuses[fr.id] = str(fr.response.get("status", ""))
    notes: list[str] = []
    for e in events:
        if e.invocation_id != invocation_id or e.author != root:
            continue
        for fc in e.get_function_calls():
            a = fc.args or {}
            if statuses.get(fc.id or "") != "ok":
                continue
            if fc.name == "curriculum_planner":
                note = (
                    f"The learner started a course on '{a.get('topic')}' at "
                    f"{a.get('learner_level', 'intermediate')} level"
                )
                if a.get("goals"):
                    note += f", with the goal: {a['goals']}"
                notes.append(note + ".")
            elif fc.name == "quiz_master":
                notes.append(
                    f"The learner completed subtopic {a.get('subtopic_number')} "
                    f"'{a.get('subtopic_title')}' of the course '{a.get('topic')}' "
                    f"(explainer, lab and quiz delivered)."
                )
    return notes


def learner_events(events: list[Event], invocation_id: str) -> list[Event]:
    """The learner's own text messages from this turn (never lesson content)."""
    return [
        e
        for e in events
        if e.invocation_id == invocation_id
        and e.author == "user"
        and e.content
        and any(p.text for p in (e.content.parts or []))
    ]


def note_event(notes: list[str], invocation_id: str, root: str) -> Event:
    return Event(
        invocation_id=invocation_id,
        author=root,
        content=types.Content(role="model", parts=[types.Part(text=" ".join(notes))]),
    )


def events_for_memory(
    events: list[Event], invocation_id: str, root: str
) -> list[Event]:
    """Learner messages plus progress notes as one event (fallback path)."""
    selected = learner_events(events, invocation_id)
    if notes := progress_notes(events, invocation_id, root):
        selected.append(note_event(notes, invocation_id, root))
    return selected


async def _ingest(
    ctx: CallbackContext, learner: list[Event], notes: list[str], root: str, inv: str
) -> None:
    try:
        if notes:
            # Progress is a fact the agent observed, not something the learner
            # said, so topic extraction tends to skip it. Write it as a direct
            # memory; consolidation merges it with earlier progress for the
            # same course instead of piling up duplicates.
            try:
                await ctx.add_memory(
                    memories=[
                        MemoryEntry(
                            author=root,
                            content=types.Content(
                                role="model", parts=[types.Part(text=n)]
                            ),
                        )
                        for n in notes
                    ],
                    custom_metadata={"enable_consolidation": True},
                )
                logger.info("progress memory written", extra={"notes": len(notes)})
            except NotImplementedError:  # e.g. InMemoryMemoryService
                learner = [*learner, note_event(notes, inv, root)]
        if learner:
            await ctx.add_events_to_memory(
                events=learner, custom_metadata=INGEST_METADATA
            )
            logger.info("memory ingest sent", extra={"events": len(learner)})
    except Exception as e:  # memory must never break a lesson
        logger.warning("memory ingest failed", extra={"error": repr(e)[:300]})


async def save_turn_to_memory(callback_context: CallbackContext) -> None:
    """Coach after_agent_callback: schedule the memory write and return."""
    inv = callback_context._invocation_context
    if inv.memory_service is None:
        return None
    root = callback_context.agent_name
    events = list(inv.session.events)
    learner = learner_events(events, inv.invocation_id)
    notes = progress_notes(events, inv.invocation_id, root)
    if not learner and not notes:
        return None
    task = asyncio.create_task(
        _ingest(callback_context, learner, notes, root, inv.invocation_id)
    )
    _background.add(task)
    task.add_done_callback(_background.discard)
    return None
