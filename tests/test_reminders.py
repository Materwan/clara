from __future__ import annotations

from datetime import UTC, datetime

import pytest

from clara.timekeeping import AnnounceFailed, ReminderError, Reminders


class Clock:
    def __init__(self, text: str):
        self.now = datetime.fromisoformat(text).replace(tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock():
    return Clock("2026-10-23T12:00")


@pytest.fixture
def reminders(services, clock):
    service = Reminders(services.db, services.events, "Europe/Paris", clock=clock)
    return service


async def test_a_daily_reminder_follows_the_clock_change(reminders, clock, services):
    reminder = reminders.create("Take the pills", "2026-10-24T09:00", repeat="daily")
    assert reminder.timezone == "Europe/Paris"
    assert reminder.due_at.isoformat() == "2026-10-24T07:00:00+00:00"  # CEST
    clock.now = datetime(2026, 10, 24, 7, 0, 30, tzinfo=UTC)
    assert await reminders.fire_due() == 1
    following = reminders.get(reminder.id)
    assert following.due_at.isoformat() == "2026-10-25T08:00:00+00:00"  # 09:00 CET, not 08:00
    clock.now = datetime(2026, 10, 25, 8, 0, 5, tzinfo=UTC)
    assert await reminders.fire_due() == 1
    assert reminders.get(reminder.id).due_at.isoformat() == "2026-10-26T08:00:00+00:00"
    fired = services.events.recent(10, ["reminder"])
    assert len(fired) == 2 and fired[0].text.startswith("Take the pills")


async def test_offsets_and_the_past_are_refused(reminders):
    with pytest.raises(ReminderError):
        reminders.create("x", "2026-10-24T09:00", timezone="+02:00")
    with pytest.raises(ReminderError):
        reminders.create("x", "2026-10-01T09:00")
    with pytest.raises(ReminderError):
        reminders.create("x", "2026-10-24T09:00", repeat="hourly")


async def test_a_missed_repeating_reminder_fires_once(reminders, clock):
    reminder = reminders.create("Stand up", "2026-10-23T15:00", repeat="daily")
    clock.now = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)  # the server was off for days
    assert await reminders.fire_due() == 1
    assert reminders.get(reminder.id).due_at.isoformat() == "2026-10-27T14:00:00+00:00"
    assert await reminders.fire_due() == 0


async def test_clara_writes_it_and_falls_back_to_the_text(reminders, clock, services):
    late = []
    reminders.late = lambda what, seconds: late.append(what)

    async def composer(reminder):
        raise AnnounceFailed("the model failed: Boom")

    reminders.composer = composer
    reminders.create("Call mum", "2026-10-23T15:00")
    reminders.create("Water plants", "2026-10-23T15:30")
    clock.now = datetime(2026, 10, 23, 13, 0, 2, tzinfo=UTC)
    assert await reminders.fire_due() == 1
    event = services.events.recent(1, ["reminder"])[0]
    assert event.text.startswith("Call mum") and "could not write" in event.text
    assert event.payload["message"] == ""

    async def writes(reminder):
        return "Time to call your mum!"

    reminders.composer = writes
    clock.now = datetime(2026, 10, 23, 13, 40, tzinfo=UTC)  # ten minutes late
    await reminders.fire_due()
    assert services.events.recent(1, ["reminder"])[0].text == "Time to call your mum!"
    assert late and "Water plants" in late[0]


async def test_reminders_announced_by_the_agent(services, llm, clock):
    from clara.timekeeping.clara_jobs import ClaraJobs

    llm.replies = ["Hey, your train leaves soon!"]
    jobs = ClaraJobs(services.agent, 5)
    reminder = services.reminders.create("Train at 18:00", "2027-01-01T09:00")
    assert await jobs.announce(reminder) == "Hey, your train leaves soon!"
    sent = llm.seen[-1]["messages"]
    assert sent[0]["role"] == "system" and sent[-1]["content"].endswith("[Reminder due] Train at 18:00")
    assert llm.seen[-1]["tools"] is None
