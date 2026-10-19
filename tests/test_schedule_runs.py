"""Schedules with tool packs: a conversation per run, a run that asks and waits, what happens when runs are missed or
overlap, and the daily-note schedule made at start."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from clara.agent.turn import ChatRequest
from clara.app import build_services, create_app, extend, open_database
from clara.knowledge.vault.git import init_repository
from clara.packs import daily_note
from clara.settings import load_settings
from clara.timekeeping import ScheduleError
from fakes import calls
from test_packs import SCHEMA, TEMPLATE, commit_all

PARIS = "Europe/Paris"
WAITING_PACKS = ["second_brain", "day_conversations", "ask_person", "discord_thread"]


class Clock:
    def __init__(self, text: str):
        self.now = datetime.fromisoformat(text).replace(tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def at(self, text: str) -> None:
        self.now = datetime.fromisoformat(text).replace(tzinfo=UTC)


@pytest.fixture
def clock() -> Clock:
    return Clock("2026-10-18T10:00")


@pytest.fixture
def vault_dir(tmp_path) -> Path:
    root = tmp_path / "vault"
    for folder in ("_clara", "_templates", "1-daily", "3-projects/Clara"):
        (root / folder).mkdir(parents=True)
    (root / "_clara" / "schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
    (root / "_templates" / "daily.md").write_text(TEMPLATE, encoding="utf-8")
    (root / "3-projects" / "Clara" / "Clara.md").write_text("# Clara\n", encoding="utf-8")
    init_repository(root)
    commit_all(root, GIT_COMMITTER_DATE="2026-10-01T09:00:00+00:00", GIT_AUTHOR_DATE="2026-10-01T09:00:00+00:00")
    return root


def build(environ, llm, clock, vault_dir=None):
    extra = {"CLARA_VAULT_PATH": str(vault_dir), "CLARA_VAULT_PUSH": "false"} if vault_dir else {}
    settings = load_settings({**environ, **extra}, use_file=False)
    db = open_database(settings)
    services = build_services(settings, db, provider_factory=llm.factory)
    extend(services)
    services.runtime.set("default_model", "local:fake")
    services.schedules._clock = clock
    return services, db


@pytest.fixture
def s(environ, vault_dir, llm, clock):
    services, db = build(environ, llm, clock, vault_dir)
    yield services
    services.traffic.close()
    db.close()


@pytest.fixture
def novault(environ, llm, clock):
    services, db = build(environ, llm, clock)
    yield services
    services.traffic.close()
    db.close()


def plan(s, **options):
    options.setdefault("packs", WAITING_PACKS)
    return s.schedules.create(options.pop("name", "Bilan"), options.pop("prompt", "Fais le bilan du jour."), "2026-10-18T23:59",
                              timezone=PARIS, repeat="daily", **options)


async def run_due(s, clock: Clock, at: str) -> None:
    clock.at(at)
    s.schedules.fire_due()
    await s.schedules.wait_running()


def runs(s, schedule_id: int) -> list[dict]:
    return list(reversed(s.schedules.runs(schedule_id)))


# ----------------------------------------------------------------------
# A schedule with packs, a model, a conversation per run
# ----------------------------------------------------------------------
def test_a_schedule_keeps_its_packs_and_policies_and_checks_them(s):
    schedule = plan(s, per_run=True, missed="latest", overlap="replace", model="local:fake")
    described = s.schedules.describe(schedule)
    assert described["packs"] == ["second_brain", "day_conversations", "ask_person", "discord_thread"]
    assert (described["per_run"], described["missed"], described["overlap"], described["model"]) == (True, "latest", "replace", "local:fake")
    changed = s.schedules.update(schedule.id, packs=["web"], overlap="queue", per_run=False, model="")
    assert changed.packs == ("web",) and changed.overlap == "queue" and not changed.per_run and changed.model == ""
    for bad in ({"packs": ["bogus"]}, {"packs": ["web", "ask_person", "nope"]}, {"missed": "never"}, {"overlap": "sometimes"}):
        with pytest.raises(ScheduleError):
            s.schedules.update(schedule.id, **bad)
    with pytest.raises(ScheduleError):
        plan(s, name="Autre", packs=["bogus"])
    assert plan(s, name="Simple", packs=None).packs == ()  # nothing asked for: no pack, not even web


async def test_every_run_has_its_own_conversation_with_the_packs_and_the_model(s, llm, clock):
    schedule = plan(s, per_run=True, model="local:fake")
    llm.replies = ["Premier.", "Second."]
    await run_due(s, clock, "2026-10-18T21:59:30")
    await run_due(s, clock, "2026-10-19T21:59:30")
    first, second = runs(s, schedule.id)
    assert first["status"] == second["status"] == "ok" and first["conversation_id"] != second["conversation_id"]
    conversation = s.conversations.get(first["conversation_id"])
    assert conversation.title == "Bilan — 2026-10-18" and conversation.kind == "chat" and conversation.model == "local:fake"
    assert conversation.vault_open and "ask_person" in conversation.packs and "second_brain" in conversation.describe()["packs"]
    assert "Fais le bilan du jour." in llm.seen[0]["messages"][-1]["content"]
    assert "ask_person" in llm.seen[0]["messages"][0]["content"]  # a schedule that may ask is told how
    names = {tool["function"]["name"] for tool in llm.seen[0]["tools"]}
    assert {"ask_person", "finish_run", "day_conversations", "vault_daily_block"} <= names
    assert "discord_thread" not in names  # the bot is not connected in this test
    assert "web_search" not in names  # its packs do not include the web


async def test_a_shared_conversation_is_kept_and_follows_the_schedule(s, llm, clock):
    schedule = plan(s, packs=["web", "day_activity"])
    llm.replies = ["Un.", "Deux."]
    await run_due(s, clock, "2026-10-18T21:59:30")
    await run_due(s, clock, "2026-10-19T21:59:30")
    first, second = runs(s, schedule.id)
    assert first["conversation_id"] == second["conversation_id"] == s.schedules.get(schedule.id).conversation_id
    assert s.conversations.get(first["conversation_id"]).kind == "schedule"
    s.schedules.update(schedule.id, packs=["web"])
    llm.replies = ["Trois."]
    await run_due(s, clock, "2026-10-20T21:59:30")
    assert "day_activity" not in {tool["function"]["name"] for tool in llm.seen[-1]["tools"]}


# ----------------------------------------------------------------------
# A run that asks, waits, and goes on with your answer
# ----------------------------------------------------------------------
async def ask_and_wait(s, llm, clock, **options):
    schedule = plan(s, per_run=True, **options)
    llm.replies = [calls(("ask_person", {"question": "Qu'as-tu fait d'autre aujourd'hui ?"})), "J'attends ta réponse."]
    await run_due(s, clock, "2026-10-18T21:59:30")
    return schedule, runs(s, schedule.id)[0]


async def test_a_run_that_asks_waits_for_the_answer_and_finishes_with_the_note_written(s, llm, clock, vault_dir):
    schedule, run = await ask_and_wait(s, llm, clock)
    assert run["status"] == "waiting" and run["finished_at"] is None and run["summary"].startswith("Qu'as-tu fait")
    asked = s.events.recent(1, ["ask"])[0]
    assert asked.text == "Qu'as-tu fait d'autre aujourd'hui ?" and asked.title == "Bilan — 2026-10-18"
    assert asked.payload == {"conversation": run["conversation_id"], "stage": "asked", "run": run["id"], "thread": True}
    assert asked.targets == ("web", "discord") and not s.events.recent(5, ["schedule"])  # no summary yet
    assert s.schedules.describe(schedule)["waiting_run"]["conversation"] == run["conversation_id"]
    with pytest.raises(ScheduleError):
        s.schedules.run_now(schedule.id)  # it waits for you
    # your answer, from any client, goes on in the run's conversation with the same tools
    recap = "**Fait hors de Clara**\n- Couru 5 km, voir [[Clara]]"
    llm.replies = [calls(("vault_daily_block", {"content": recap})), calls(("finish_run", {"summary": "Récap écrit dans la note du jour."})),
                   "C'est écrit."]
    await s.agent.answer(ChatRequest(message="J'ai couru 5 km.", client="discord", conversation=run["conversation_id"]))
    names = {tool["function"]["name"] for tool in llm.seen[-3]["tools"]}
    assert {"vault_daily_block", "finish_run", "ask_person"} <= names
    done = runs(s, schedule.id)[0]
    assert done["status"] == "ok" and done["finished_at"] and done["summary"] == "Récap écrit dans la note du jour."
    note = (vault_dir / "1-daily" / "2026" / "2026-10-18.md").read_text(encoding="utf-8")  # the run's day, not today's
    assert "- Couru 5 km, voir [[Clara]]" in note and "<!-- clara:recap -->" in note
    finished = s.events.recent(1, ["ask"])[0]
    assert finished.payload["stage"] == "finished" and finished.text == "Récap écrit dans la note du jour."
    assert not s.events.recent(5, ["schedule"])  # the ask event was its notification
    assert s.schedules.describe(schedule)["waiting_run"] is None
    s.schedules.run_now(schedule.id)  # free again
    await s.schedules.wait_running()


async def test_a_run_without_the_thread_pack_asks_without_a_thread(s, llm, clock):
    _, run = await ask_and_wait(s, llm, clock, packs=["ask_person"])
    assert s.events.recent(1, ["ask"])[0].payload["thread"] is False and run["status"] == "waiting"


async def test_a_local_only_schedule_asks_on_the_web_only(s, llm, clock):
    await ask_and_wait(s, llm, clock, local_only=True)
    assert s.events.recent(1, ["ask"])[0].targets == ("web",)


async def test_finishing_without_having_asked_is_an_ordinary_run(s, llm, clock):
    schedule = plan(s, per_run=True)
    llm.replies = [calls(("finish_run", {"summary": "Rien à demander."})), "ok"]
    await run_due(s, clock, "2026-10-18T21:59:30")
    assert runs(s, schedule.id)[0]["status"] == "ok"
    assert s.events.recent(1, ["schedule"])[0].text == "Rien à demander." and not s.events.recent(1, ["ask"])


async def test_a_waiting_run_is_reminded_once_the_next_morning(s, llm, clock):
    await ask_and_wait(s, llm, clock)
    for moment in ("2026-10-19T06:59:00", "2026-10-19T07:01:00"):  # 08:59, then 09:01 in Paris
        clock.at(moment)
        s.schedules.fire_due()
    asks = s.events.recent(5, ["ask"])
    assert [event.payload["stage"] for event in asks] == ["reminder", "asked"] and "Still waiting" in asks[0].text
    clock.at("2026-10-19T08:00:00")
    s.schedules.fire_due()
    assert len(s.events.recent(5, ["ask"])) == 2  # once


# ----------------------------------------------------------------------
# Two runs at once
# ----------------------------------------------------------------------
async def test_overlap_replace_closes_the_waiting_run_from_what_it_has_then_starts_the_next(s, llm, clock):
    schedule, first = await ask_and_wait(s, llm, clock, overlap="replace")
    llm.replies = [calls(("finish_run", {"summary": "Récap écrit sans réponse."})), "Terminé.", "Nouveau bilan."]
    clock.at("2026-10-19T21:59:30")
    s.schedules.fire_due()
    await s.schedules.wait_running()
    assert runs(s, schedule.id)[0]["status"] == "closed" and runs(s, schedule.id)[0]["summary"] == "Récap écrit sans réponse."
    assert any("[No answer received]" in m.content for m in s.conversations.messages_after(first["conversation_id"]))
    assert s.events.recent(1, ["ask"])[0].payload["stage"] == "closed"
    s.schedules.fire_due()  # the queued run starts now that the old one is over
    await s.schedules.wait_running()
    second = runs(s, schedule.id)[-1]
    assert second["status"] == "ok" and second["conversation_id"] != first["conversation_id"]


async def test_a_closing_that_does_not_finish_the_run_closes_it_all_the_same(s, llm, clock):
    schedule, _ = await ask_and_wait(s, llm, clock, overlap="replace")
    llm.replies = ["Je ne sais pas quoi faire."]
    clock.at("2026-10-19T21:59:30")
    s.schedules.fire_due()
    await s.schedules.wait_running()
    assert runs(s, schedule.id)[0]["status"] == "closed" and runs(s, schedule.id)[0]["summary"] == "Closed without an answer."


async def test_overlap_skip_records_the_new_run_as_skipped(s, llm, clock):
    schedule, _ = await ask_and_wait(s, llm, clock, overlap="skip")
    clock.at("2026-10-19T21:59:30")
    s.schedules.fire_due()
    await s.schedules.wait_running()
    assert [run["status"] for run in runs(s, schedule.id)] == ["waiting", "skipped"]


async def test_overlap_queue_waits_until_the_old_run_ends(s, llm, clock):
    schedule, first = await ask_and_wait(s, llm, clock, overlap="queue")
    clock.at("2026-10-19T21:59:30")
    s.schedules.fire_due()
    assert [run["status"] for run in runs(s, schedule.id)] == ["waiting", "queued"] and s.schedules.describe(schedule)["queued"] == 1
    s.schedules.fire_due()
    assert [run["status"] for run in runs(s, schedule.id)] == ["waiting", "queued"]  # still waits for your answer
    llm.replies = [calls(("finish_run", {"summary": "Fini."})), "ok", "Le suivant."]
    await s.agent.answer(ChatRequest(message="Rien d'autre.", client="web", conversation=first["conversation_id"]))
    s.schedules.fire_due()
    await s.schedules.wait_running()
    assert [run["status"] for run in runs(s, schedule.id)] == ["ok", "ok"]
    assert parse(runs(s, schedule.id)[1]["due_at"]) == datetime(2026, 10, 19, 21, 59, tzinfo=UTC)  # for its own due time


def parse(text: str) -> datetime:
    return datetime.fromisoformat(text)


async def test_a_run_still_going_is_not_doubled_either(s, llm, clock):
    schedule = plan(s, overlap="queue")
    llm.delay = 0.05
    clock.at("2026-10-18T21:59:30")
    s.schedules.fire_due()
    clock.at("2026-10-19T21:59:30")  # the first has not ended (the fake model is slow)
    s.schedules.fire_due()
    assert [run["status"] for run in runs(s, schedule.id)] == ["running", "queued"]
    await s.schedules.wait_running()
    s.schedules.fire_due()
    await s.schedules.wait_running()
    assert [run["status"] for run in runs(s, schedule.id)] == ["ok", "ok"]


# ----------------------------------------------------------------------
# Runs missed while the server was off
# ----------------------------------------------------------------------
async def off_for_days(s, llm, clock, missed: str):
    schedule = plan(s, packs=["web"], missed=missed, name=f"Matin {missed}")
    s.db.execute("UPDATE schedules SET local_at = '2026-10-18T08:00', next_at = '2026-10-18T06:00:00+00:00' WHERE id = ?", (schedule.id,))
    llm.replies = ["ok"] * 5
    await run_due(s, clock, "2026-10-21T10:00:00")  # 12:00 on the 21st: the 18th, 19th, 20th and 21st at 08:00 were missed
    for _ in range(4):
        s.schedules.fire_due()
        await s.schedules.wait_running()
    return [(run["status"], run["due_at"][8:10]) for run in runs(s, schedule.id)]


async def test_missed_skip_only_records_them(s, llm, clock):
    assert await off_for_days(s, llm, clock, "skip") == [("missed", "18"), ("missed", "19"), ("missed", "20"), ("missed", "21")]


async def test_missed_latest_runs_the_last_one_and_records_the_others(s, llm, clock):
    assert await off_for_days(s, llm, clock, "latest") == [("missed", "18"), ("missed", "19"), ("missed", "20"), ("ok", "21")]


async def test_missed_all_runs_them_one_after_the_other(s, llm, clock):
    assert await off_for_days(s, llm, clock, "all") == [("ok", "18"), ("ok", "19"), ("ok", "20"), ("ok", "21")]


async def test_a_catch_up_is_not_a_late_alert_but_a_fresh_run_is(s, llm, clock):
    told = []
    s.schedules.late = lambda what, seconds: told.append(what)
    schedule = plan(s, packs=["web"], missed="latest")
    s.db.execute("UPDATE schedules SET local_at = '2026-10-18T08:00', next_at = '2026-10-18T06:00:00+00:00' WHERE id = ?", (schedule.id,))
    llm.replies = ["ok", "ok"]
    await run_due(s, clock, "2026-10-19T10:00:00")
    assert not told
    clock.at("2026-10-20T06:20:00")  # 08:20: twenty minutes late
    s.schedules.fire_due()
    await s.schedules.wait_running()
    assert told == ["schedule “Bilan”"]


# ----------------------------------------------------------------------
# The daily-note schedule
# ----------------------------------------------------------------------
def test_the_daily_note_schedule_is_made_once_with_its_packs(s, clock):
    schedule_id = daily_note.seed(s)
    schedule = s.schedules.get(schedule_id)
    assert schedule.name == "Bilan du jour" and schedule.enabled and schedule.repeat == "daily"
    assert schedule.local_at.strftime("%H:%M") == "23:59" and schedule.timezone == PARIS
    assert schedule.next_at == datetime(2026, 10, 18, 21, 59, tzinfo=UTC)  # tonight, 23:59 in Paris
    assert set(schedule.packs) == set(daily_note.PACKS) and schedule.per_run and schedule.missed == "latest"
    assert schedule.overlap == "replace" and "vault_daily_block" in schedule.prompt and "ask_person" in schedule.prompt
    assert daily_note.seed(s) is None  # once
    s.schedules.delete(schedule_id)
    assert daily_note.seed(s) is None  # and not again after you deleted it


def test_without_a_vault_no_daily_note_schedule_is_made(novault):
    assert daily_note.seed(novault) is None and novault.schedules.listing() == []
    assert not novault.db.one("SELECT 1 FROM settings WHERE key = ?", (daily_note.SEED_KEY,))


def test_the_days_of_the_earlier_recap_become_runs_of_the_schedule(s):
    asked = s.conversations.create("server", title="Bilan du 2026-10-16").id
    written = s.conversations.create("server", title="Bilan du 2026-10-15").id
    for day, status, conversation, path in (("2026-10-16", "asked", asked, ""), ("2026-10-15", "written", written, "1-daily/2026/2026-10-15.md"),
                                            ("2026-10-14", "asking", None, "")):
        s.db.execute("INSERT INTO recaps_legacy (day, status, conversation_id, timezone, asked_at, finished_at, note_path, created_at)"
                     " VALUES (?, ?, ?, ?, '2026-10-16T21:59:30+00:00', NULL, ?, '2026-10-16T21:59:00+00:00')",
                     (day, status, conversation, PARIS, path))
    schedule_id = daily_note.seed(s)
    moved = {run["due_at"][:10]: run for run in s.schedules.runs(schedule_id)}
    assert set(moved) == {"2026-10-16", "2026-10-15"}  # a day that was only being asked is not a run
    assert moved["2026-10-16"]["status"] == "waiting" and moved["2026-10-16"]["conversation_id"] == asked
    assert moved["2026-10-15"]["status"] == "ok" and moved["2026-10-15"]["summary"] == "1-daily/2026/2026-10-15.md"
    assert s.conversations.get(asked).describe()["packs"].count("ask_person") == 1  # its answer still finds its tools
    assert not s.db.one("SELECT 1 FROM sqlite_master WHERE name = 'recaps_legacy'")


async def test_the_daily_note_runs_end_to_end_at_23_59(s, llm, clock):
    daily_note.seed(s)
    llm.replies = [calls(("day_conversations", {}), ("day_activity", {}), ("files_created", {})),
                   calls(("ask_person", {"question": "Voici ta journée : …"})), "J'attends."]
    await run_due(s, clock, "2026-10-18T21:59:30")
    run = s.schedules.runs(s.schedules.listing()[0].id)[0]
    assert run["status"] == "waiting"
    results = [m["content"] for m in llm.seen[1]["messages"] if m["role"] == "tool"]
    assert any("Conversations du dimanche 18 octobre 2026" in text for text in results)  # no date given: the run's day
    assert any("Activité du dimanche 18 octobre 2026" in text for text in results)
    assert any("Fichiers du dimanche 18 octobre 2026" in text for text in results)
    assert s.conversations.get(run["conversation_id"]).title == "Bilan du jour — 2026-10-18"


# ----------------------------------------------------------------------
# The API
# ----------------------------------------------------------------------
@pytest.fixture
def token(s) -> str:
    return s.tokens.create("tester", "cli")[1]


@pytest.fixture
async def client(s, token):
    import httpx

    transport = httpx.ASGITransport(app=create_app(s))
    async with httpx.AsyncClient(transport=transport, base_url="http://clara.test",
                                 headers={"Authorization": f"Bearer {token}"}) as http:
        yield http


async def test_the_api_creates_a_schedule_with_packs_and_policies(client, s):
    body = {"name": "Bilan", "prompt": "Fais le bilan.", "at": "2030-01-01T23:59", "timezone": PARIS, "repeat": "daily",
            "packs": ["day_conversations", "ask_person"], "per_run": True, "missed": "latest", "overlap": "replace",
            "model": "local:fake"}
    made = await client.post("/v1/schedules", json=body)
    assert made.status_code == 201
    shown = made.json()
    assert (shown["packs"], shown["per_run"], shown["missed"], shown["overlap"], shown["model"]) == (
        ["day_conversations", "ask_person"], True, "latest", "replace", "local:fake")
    changed = await client.patch(f"/v1/schedules/{shown['id']}", json={"packs": [], "model": "", "overlap": "queue"})
    assert changed.json()["packs"] == [] and changed.json()["model"] == "" and changed.json()["overlap"] == "queue"
    assert (await client.post("/v1/schedules", json={**body, "packs": ["bogus"]})).status_code == 422
    assert (await client.post("/v1/schedules", json={**body, "model": "not a model"})).status_code == 422
    assert (await client.post("/v1/schedules", json={**body, "overlap": "later"})).status_code == 422
    assert (await client.post(f"/v1/schedules/{shown['id']}/close")).status_code == 409  # nothing waits
    assert (await client.post("/v1/schedules/999/close")).status_code == 404


async def test_the_api_runs_a_schedule_now(client, s, llm, clock):
    schedule = plan(s, packs=["web"])
    llm.replies = ["Fait."]
    assert (await client.post(f"/v1/schedules/{schedule.id}/run")).status_code == 200
    await s.schedules.wait_running()
    assert (await client.get(f"/v1/schedules/{schedule.id}")).json()["history"][0]["status"] == "ok"


async def test_the_api_closes_the_waiting_run(client, s, llm, clock):
    schedule, run = await ask_and_wait(s, llm, clock)
    llm.replies = [calls(("finish_run", {"summary": "Fermé."})), "ok"]
    assert (await client.post(f"/v1/schedules/{schedule.id}/close")).status_code == 200
    await s.schedules.wait_running()
    detail = (await client.get(f"/v1/schedules/{schedule.id}")).json()
    assert detail["history"][0]["status"] == "closed" and detail["history"][0]["conversation_id"] == run["conversation_id"]
    assert detail["waiting_run"] is None


def test_the_next_start_is_not_a_spin_while_a_queued_run_waits(s, llm, clock):
    schedule = plan(s, overlap="queue")
    s.db.execute("INSERT INTO schedule_runs (schedule_id, due_at, started_at, status) VALUES (?, '2026-10-18T21:59:00+00:00', ?, 'waiting')",
                 (schedule.id, "2026-10-18T22:00:00+00:00"))
    s.db.execute("INSERT INTO schedule_runs (schedule_id, due_at, status) VALUES (?, '2026-10-19T21:59:00+00:00', 'queued')", (schedule.id,))
    s.db.execute("UPDATE schedule_runs SET waiting_since = '2026-10-18T22:00:00+00:00', reminded_at = '2026-10-19T07:00:00+00:00' WHERE status = 'waiting'")
    soon = s.schedules.next_due()
    assert soon is not None and soon >= clock.now + timedelta(hours=1)  # the next due moment, not "now"



# ----------------------------------------------------------------------
# Upgrading a database that has runs and the earlier recap
# ----------------------------------------------------------------------
def test_a_version_3_database_upgrades_and_keeps_its_runs_and_recap_days(tmp_path):
    import sqlite3

    from clara.db import migrate
    from clara.db.migrate import migrations

    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    for migration in migrations()[:3]:
        connection.executescript(f"BEGIN IMMEDIATE;\n{migration.sql}\nPRAGMA user_version = {migration.version};\nCOMMIT;")
    connection.execute("INSERT INTO conversations (id, client, created_at, updated_at) VALUES ('c1', 'web', 'x', 'x')")
    connection.execute("INSERT INTO schedules (id, name, prompt, local_at, timezone, created_at, updated_at)"
                       " VALUES (1, 'News', 'Give the news', '2026-10-18T08:00', 'Europe/Paris', 'x', 'x')")
    connection.execute("INSERT INTO schedule_runs (schedule_id, due_at, status, summary, late_seconds)"
                       " VALUES (1, '2026-10-18T06:00:00+00:00', 'ok', 'Three items.', 4)")
    connection.execute("INSERT INTO schedule_runs (schedule_id, due_at, status, summary) VALUES (1, '2026-10-19T06:00:00+00:00', 'missed', 'off')")
    connection.execute("INSERT INTO recaps (day, status, conversation_id, timezone, created_at)"
                       " VALUES ('2026-10-17', 'asked', 'c1', 'Europe/Paris', 'x')")
    connection.commit()
    connection.close()
    before, after, backup = migrate(path, tmp_path / "backups")
    assert (before, after) == (3, len(migrations())) and backup is not None
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    runs = connection.execute("SELECT * FROM schedule_runs ORDER BY id").fetchall()
    assert [(row["status"], row["summary"], row["late_seconds"], row["conversation_id"]) for row in runs] == [
        ("ok", "Three items.", 4, None), ("missed", "off", 0, None)]
    connection.execute("INSERT INTO schedule_runs (schedule_id, due_at, status) VALUES (1, 'x', 'waiting')")  # the new statuses exist
    connection.execute("INSERT INTO schedule_runs (schedule_id, due_at, status) VALUES (1, 'y', 'queued')")
    assert connection.execute("SELECT packs, per_run, missed, overlap, model FROM schedules").fetchone()[:] == ('["web"]', 0, "skip", "skip", "")
    assert connection.execute("SELECT packs FROM conversations").fetchone()[0] == '["web"]'
    assert connection.execute("SELECT in_history FROM schedules").fetchone()[0] == 0  # hidden from the history until switched on
    assert connection.execute("SELECT day, status FROM recaps_legacy").fetchone()[:] == ("2026-10-17", "asked")
    assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'recaps'").fetchone() is None
    connection.close()
