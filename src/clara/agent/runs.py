"""Turns that go on without their client.

A turn is normally tied to the request that asked it: close the stream and the answer stops. A client may ask for a
`Run` instead (`keep_running`): the turn is driven by a task of its own, and the request only *follows* it. The page
closed, the phone asleep, another conversation opened: the answer goes on, is stored when it ends, and whoever comes
back follows the same run from its first event.

A run keeps what it said for as long as it goes (consecutive `token` and `thinking` events are kept as one), so a
follower that joins late is given everything so far, then what comes. Once it ends it is forgotten: the answer is in
the conversation.

Only an explicit stop (`Runs.stop`) or the server stopping (`Runs.cancel_all`) ends a run early; what had been said is
then stored as interrupted, as for any turn cut short.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..tools.base import TurnState
    from .turn import ChatRequest, TurnPlan

MERGED = ("token", "thinking")  # events whose consecutive runs are kept as one


class AlreadyRunning(Exception):
    """Clara is already answering in that conversation."""


class Run:
    def __init__(self, state: TurnState, question: str, attachments: list[str]):
        self.state = state
        self.turn_id = state.turn_id
        self.conversation = state.conversation_id or None
        self.question = question
        self.attachments = attachments
        self.task: asyncio.Task | None = None
        self.ended = False
        self._log: list[dict] = []
        self._queues: set[asyncio.Queue[dict | None]] = set()

    def publish(self, event: dict) -> None:
        kind = event["type"]
        if kind in MERGED and self._log and self._log[-1]["type"] == kind:
            self._log[-1]["parts"].append(event["text"])
        elif kind in MERGED:
            self._log.append({"type": kind, "parts": [event["text"]]})
        else:
            self._log.append(event)
        for queue in self._queues:
            queue.put_nowait(event)

    def end(self) -> None:
        if self.ended:
            return
        self.ended = True
        for queue in self._queues:
            queue.put_nowait(None)

    @property
    def followers(self) -> int:
        return len(self._queues)

    def events(self) -> list[dict]:
        """What the run has said so far."""
        return [{"type": e["type"], "text": "".join(e["parts"])} if "parts" in e else e for e in self._log]

    async def follow(self) -> AsyncIterator[dict]:
        """Everything the run has said, then what it says, until it ends. Leaving does not stop it."""
        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        so_far = self.events()
        if not self.ended:  # no await between the two: nothing is missed or said twice
            self._queues.add(queue)
        try:
            for event in so_far:
                yield event
            while not self.ended or not queue.empty():
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            self._queues.discard(queue)


class Runs:
    def __init__(self, open_turn: Callable[[ChatRequest, TurnPlan], AsyncIterator[dict]]):
        self._open = open_turn
        self._runs: dict[str, Run] = {}

    @property
    def active(self) -> bool:
        return bool(self._runs)

    def of(self, conversation_id: str) -> Run | None:
        return next((run for run in self._runs.values() if run.conversation == conversation_id), None)

    def start(self, request: ChatRequest, plan: TurnPlan, fail: Callable[[Exception], dict]) -> Run:
        """Drive the turn in a task of its own. `fail` turns the error that ends it into its last event."""
        state = plan.state
        if state.conversation_id and self.of(state.conversation_id) is not None:
            raise AlreadyRunning(state.conversation_id)
        run = Run(state, request.message, [file.name for file in request.files])
        self._runs[run.turn_id] = run
        run.task = asyncio.ensure_future(self._drive(run, self._open(request, plan), fail))
        return run

    async def _drive(self, run: Run, events: AsyncIterator[dict], fail: Callable[[Exception], dict]) -> None:
        try:
            async with contextlib.aclosing(events) as stream:
                async for event in stream:
                    run.publish(event)
        except Exception as error:  # once going, a failure is the last event
            run.publish(fail(error))
        finally:
            run.end()
            self._runs.pop(run.turn_id, None)

    async def stop(self, run: Run, reason: str) -> None:
        """Cut the run short and wait until what it had done is stored."""
        run.state.stop_reason = reason
        if run.task is not None and not run.task.done():
            run.task.cancel()
            await asyncio.wait({run.task})
        run.end()  # a task cancelled before it began never reached its own end
        self._runs.pop(run.turn_id, None)

    async def cancel_all(self, reason: str) -> None:
        await asyncio.gather(*(self.stop(run, reason) for run in list(self._runs.values())))
