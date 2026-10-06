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

"""Per-session, per-turn bookkeeping shared by the plugins.

Plugins are process-wide singletons, so everything here is keyed by session ID.
A turn starts at ``before_run_callback`` and ends at ``after_run_callback``.
Nothing here needs to survive a restart; durable state (the cached costly lab)
lives in session state instead.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnStats:
    started: float = field(default_factory=time.monotonic)
    intent: str | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    routes: list[dict[str, str]] = field(default_factory=list)
    tokens: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    retries: int = 0
    violations: list[str] = field(default_factory=list)
    redactions: list[str] = field(default_factory=list)
    injection: list[str] = field(default_factory=list)
    hitl: list[dict[str, Any]] = field(default_factory=list)


class Tracker:
    def __init__(self) -> None:
        self._current_args: dict[tuple[str, str], dict[str, Any]] = {}
        self._attempts: dict[tuple[str, str, int], int] = defaultdict(int)
        self._outcome: dict[tuple[str, str], dict[str, Any]] = {}
        self._served_from_cache: set[str] = set()
        self._turns: dict[str, TurnStats] = {}

    # --- turn lifecycle -----------------------------------------------------
    def start_turn(self, session_id: str) -> None:
        # setdefault: on_user_message_callback may already have recorded stats.
        self._turns.setdefault(session_id, TurnStats())
        for key in [k for k in self._attempts if k[0] == session_id]:
            del self._attempts[key]

    def turn(self, session_id: str) -> TurnStats:
        return self._turns.setdefault(session_id, TurnStats())

    def end_turn(self, session_id: str) -> TurnStats:
        for key in [k for k in self._current_args if k[0] == session_id]:
            del self._current_args[key]
        for key in [k for k in self._outcome if k[0] == session_id]:
            del self._outcome[key]
        self._served_from_cache.discard(session_id)
        return self._turns.pop(session_id, TurnStats())

    # --- specialist calls ---------------------------------------------------
    def set_args(self, session_id: str, agent: str, args: dict[str, Any]) -> None:
        self._current_args[(session_id, agent)] = dict(args or {})

    def args(self, session_id: str, agent: str) -> dict[str, Any]:
        return self._current_args.get((session_id, agent), {})

    def attempt(self, session_id: str, agent: str, subtopic: int) -> int:
        """Failed attempts so far this turn for (agent, subtopic)."""
        return self._attempts[(session_id, agent, subtopic)]

    def record_failure(self, session_id: str, agent: str, subtopic: int) -> int:
        self._attempts[(session_id, agent, subtopic)] += 1
        self.turn(session_id).retries += 1
        return self._attempts[(session_id, agent, subtopic)]

    def set_outcome(self, session_id: str, agent: str, outcome: dict[str, Any]) -> None:
        self._outcome[(session_id, agent)] = outcome

    def pop_outcome(self, session_id: str, agent: str) -> dict[str, Any] | None:
        return self._outcome.pop((session_id, agent), None)

    def mark_served_from_cache(self, session_id: str) -> None:
        self._served_from_cache.add(session_id)

    def take_served_from_cache(self, session_id: str) -> bool:
        if session_id in self._served_from_cache:
            self._served_from_cache.discard(session_id)
            return True
        return False
