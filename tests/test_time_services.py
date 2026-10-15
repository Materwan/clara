"""Tasks, schedules, versions and compaction."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from clara.agent.turn import ChatRequest
from clara.timekeeping import Followup, ScheduleError, TaskError, Tasks
from clara.timekeeping.schedules import Schedules
from conftest import parse_sse


class Clock:
    def __init__(self, text: str):
        self.now = datetime.fromisoformat(text).replace(tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


async def test_tasks_subtasks_and_their_limits(services):
    clock = Clock("2026-10-20T08:00")
    tasks = Tasks(services.db, services.events, "Europe/Paris", clock=clock)
    trip = await tasks.create("Trip to Lyon", due="2026-10-30T18:00")
    assert len(trip.next) == 3  # a day before, an hour before, at the deadline
    with pytest.raises(TaskError):
        await tasks.create("Book the train", due="2026-11-02T09:00", parent_id=trip.id)
    train = await tasks.create("Book the train", due="2026-10-25T09:00", reminders=["2026-10-24T09:00"],
                               parent_id=trip.id)
    assert train.next[0].isoformat() == "2026-10-24T07:00:00+00:00"  # 09:00 CEST
    tasks.complete(train.id)
    assert tasks.get(trip.id).status == "done"  # its only sub task is done
    await tasks.reopen(train.id)
    assert tasks.get(trip.id).status == "open"
    assert tasks.delete(trip.id) and tasks.find(train.id) is None


async def test_a_task_reminder_follows_clara_and_stops_after_the_last(services):
    clock = Clock("2026-10-20T08:00")
    tasks = Tasks(services.db, services.events, "Europe/Paris", max_reminders=2, clock=clock)

    async def follower(task, now, family):
        return Followup("Don't forget the tax form!", [datetime(2026, 10, 21, 7, 0, tzinfo=UTC)])

    tasks.follower = follower
    task = await tasks.create("Tax form", reminders=["2026-10-20T10:30"])
    clock.now = datetime(2026, 10, 20, 8, 31, tzinfo=UTC)
    assert await tasks.fire_due() == 1
    event = services.events.recent(1, ["task"])[0]
    assert event.text == "Don't forget the tax form!" and event.title == "Task: Tax form"
    assert tasks.get(task.id).next[0].isoformat() == "2026-10-21T07:00:00+00:00"
    clock.now = datetime(2026, 10, 21, 7, 1, tzinfo=UTC)
    await tasks.fire_due()
    assert tasks.get(task.id).next == () and tasks.get(task.id).reminders_sent == 2


async def test_a_daily_schedule_runs_unattended_and_records_its_runs(services, llm):
    clock = Clock("2026-10-23T05:00")
    schedules = Schedules(services.db, services.events, "Europe/Paris", clock=clock)
    ran: list[int] = []

    async def runner(schedule, number):
        ran.append(number)
        return "All green.\n\nDetails follow."

    schedules.runner = runner
    schedule = schedules.create("Morning check", "Check the backups", "2026-10-24T08:00", repeat="daily")
    assert schedule.next_at.isoformat() == "2026-10-24T06:00:00+00:00"
    clock.now = datetime(2026, 10, 24, 6, 0, 5, tzinfo=UTC)
    assert schedules.fire_due() == 1
    await schedules.wait_running()
    assert ran == [1]
    current = schedules.get(schedule.id)
    assert current.next_at.isoformat() == "2026-10-25T07:00:00+00:00"  # 08:00 CET after the change
    run = schedules.runs(schedule.id)[0]
    assert run["status"] == "ok" and run["summary"] == "All green."
    clock.now = datetime(2026, 10, 25, 9, 30, tzinfo=UTC)  # more than an hour late
    schedules.fire_due()
    assert schedules.runs(schedule.id)[0]["status"] == "missed"
    with pytest.raises(ScheduleError):
        schedules.create("x", "y", "2026-10-24T08:00", timezone="+02:00")


async def test_a_question_asked_again_keeps_both_versions(client, services, llm):
    llm.replies = ["First answer."]
    first = parse_sse((await client.post("/v1/chat/stream", json={"message": "question"})).text)[-1]
    conversation, question = first["conversation"], first["message_id"]
    llm.replies = ["Second answer."]
    second = parse_sse((await client.post("/v1/chat/stream", json={
        "message": "question, better", "conversation": conversation, "fork_of": question})).text)[-1]
    assert second["versions"] == [question, second["message_id"]]
    shown = (await client.get(f"/v1/conversations/{conversation}/messages")).json()["messages"]
    assert [m["content"] for m in shown] == ["question, better", "Second answer."]
    await client.post(f"/v1/conversations/{conversation}/version", json={"message_id": question})
    shown = (await client.get(f"/v1/conversations/{conversation}/messages")).json()["messages"]
    assert [m["content"] for m in shown] == ["question", "First answer."]


async def test_a_long_conversation_is_summarised_without_losing_its_messages(services, llm):
    conversation = services.conversations.create("cli")
    for number in range(6):
        services.conversations.add_turn(conversation.id, f"question {number} " + "x" * 200, [])
    llm.replies = ["Summary: six questions about x."]
    before, after = await services.agent.compact(conversation.id)
    current = services.conversations.get(conversation.id)
    assert current.summary == "Summary: six questions about x."
    assert services.conversations.message_count(conversation.id) == 6  # nothing purged by default
    assert services.conversations.message_count(conversation.id, current.summary_upto) == 2  # two turns kept
    llm.replies = ["Answer."]
    await services.agent.answer(ChatRequest(message="go on", client="cli", conversation=conversation.id))
    system = llm.seen[-1]["messages"][0]["content"]
    assert "Summary: six questions about x." in system
