"""The daily recap: the vault's daily note, the digest of a day, and the check-in that waits for your answer."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from clara.agent.turn import ChatRequest
from clara.app import build_services, extend, open_database
from clara.knowledge.vault import VaultError
from clara.knowledge.vault.git import init_repository
from clara.recap import DigestBuilder, RecapError, day_bounds, french_date
from clara.recap import service as recap_service
from clara.settings import load_settings
from clara.timeutil import iso
from fakes import calls

SCHEMA = {"archive": "8-archive", "daily_format": "YYYY/YYYY-MM-DD",
          "types": {"daily": {"folder": "1-daily"}, "project": {"folder": "3-projects"}}}
TEMPLATE = "---\ntype: daily\ncreated: {{date}}\nupdated: {{date}}\nauthor: erwan\n---\n\n# {{date:YYYY-MM-DD dddd}}\n\n## Log\n\n## Tasks\n\n## Notes\n"
DAY = date(2026, 10, 18)


class Clock:
    def __init__(self, text: str):
        self.now = datetime.fromisoformat(text).replace(tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def commit_all(root: Path, message: str = "setup", **env) -> None:
    git = ["git", "-c", "user.name=Erwan", "-c", "user.email=e@example.org"]
    subprocess.run([*git, "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run([*git, "commit", "-q", "-m", message], cwd=root, check=True, capture_output=True,
                   env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root), **env})


@pytest.fixture
def vault_dir(tmp_path) -> Path:
    root = tmp_path / "vault"
    (root / "_clara").mkdir(parents=True)
    (root / "_templates").mkdir()
    (root / "1-daily").mkdir()
    (root / "3-projects" / "Clara").mkdir(parents=True)
    (root / "_clara" / "schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
    (root / "_templates" / "daily.md").write_text(TEMPLATE, encoding="utf-8")
    (root / "1-daily" / "Daily Index.md").write_text("# Daily Index\n", encoding="utf-8")
    (root / "3-projects" / "Clara" / "Clara.md").write_text("# Clara\nThe assistant.\n", encoding="utf-8")
    (root / "5-knowledge").mkdir()
    (root / "5-knowledge" / "Politique.md").write_text("# Politique\n", encoding="utf-8")
    init_repository(root)
    commit_all(root, GIT_COMMITTER_DATE="2026-10-01T09:00:00+00:00", GIT_AUTHOR_DATE="2026-10-01T09:00:00+00:00")
    return root


@pytest.fixture
def clock() -> Clock:
    return Clock("2026-10-18T10:00")


@pytest.fixture
def s(environ, vault_dir, llm, clock):
    settings = load_settings({**environ, "CLARA_VAULT_PATH": str(vault_dir), "CLARA_VAULT_PUSH": "false",
                              "CLARA_RECAP": "true"}, use_file=False)
    db = open_database(settings)
    services = build_services(settings, db, provider_factory=llm.factory)
    extend(services)
    services.runtime.set("default_model", "local:fake")
    services.recaps._clock = clock
    services.recaps.skip(date(2026, 10, 17))  # the day before the tests' first day is not asked
    yield services
    services.traffic.close()
    db.close()


def say(s, conversation: str, role: str, content: str, at: str, client: str = "web", tool: str | None = None) -> None:
    s.db.execute("INSERT INTO messages (conversation_id, role, content, client, tool_name, created_at)"
                 " VALUES (?, ?, ?, ?, ?, ?)", (conversation, role, content, client, tool, at))


def chat(s, title: str, **options) -> str:
    return s.conversations.create("web", title=title, **options).id


# ----------------------------------------------------------------------
# The vault's daily note
# ----------------------------------------------------------------------
def test_the_daily_note_follows_the_layout_of_the_clara_vault(s):
    assert s.vault.daily_path(DAY) == "1-daily/2026/2026-10-18.md"


def test_the_older_schema_layout_still_works(s, vault_dir):
    (vault_dir / "_clara" / "schema.json").write_text(json.dumps({"daily": {"folder": "Journal", "format": "%d-%m-%Y"}}))
    assert s.vault.daily_path(DAY) == "Journal/18-10-2026.md"


async def test_the_recap_fills_one_block_of_the_note_and_rewrites_only_it(s, vault_dir):
    path, dropped = await s.vault.write_recap(DAY, "**Discussions**\n- Parlé de [[Clara]] et de [[Inconnue]], [[clara|le projet]]")
    assert path == "1-daily/2026/2026-10-18.md" and dropped == ["Inconnue"]
    note = (vault_dir / path).read_text(encoding="utf-8")
    assert note.startswith("---\ntype: daily") and "# 2026-10-18 Sunday" in note  # the template, dates filled
    assert "## Récap (Clara)\n<!-- clara:recap -->\n**Discussions**" in note
    assert "[[Clara]]" in note and "[[Clara|le projet]]" in note and "[[Inconnue]]" not in note and "Inconnue" in note
    mine = note.replace("## Log\n", "## Log\n- 09:10 un mot à moi\n")  # the person writes in the note meanwhile
    (vault_dir / path).write_text(mine, encoding="utf-8")
    await s.vault.write_recap(DAY, "**Discussions**\n- Version corrigée")
    again = (vault_dir / path).read_text(encoding="utf-8")
    assert again.count("<!-- clara:recap -->") == 1 and "Version corrigée" in again and "Parlé de" not in again
    assert "- 09:10 un mot à moi" in again  # untouched
    assert s.vault.git.git("log", "-1", "--format=%s").strip() == "Clara: daily recap 1-daily/2026/2026-10-18.md"


async def test_an_empty_recap_is_refused(s):
    with pytest.raises(VaultError):
        await s.vault.write_recap(DAY, "  <!-- clara:recap -->  ")


async def test_a_line_added_to_todays_note_goes_under_log(s, vault_dir):
    await s.vault.daily("append", "appelé le garage")
    today = s.vault.daily_path(datetime.now(ZoneInfo("Europe/Paris")))
    note = (vault_dir / today).read_text(encoding="utf-8")
    log_at, tasks_at = note.index("## Log"), note.index("## Tasks")
    assert log_at < note.index("appelé le garage") < tasks_at


def test_the_notes_changed_on_a_day_come_from_git(s, vault_dir):
    (vault_dir / "3-projects" / "Clara" / "Roadmap.md").write_text("# Roadmap\n", encoding="utf-8")
    (vault_dir / "3-projects" / "Clara" / "Clara.md").write_text("# Clara\nMore.\n", encoding="utf-8")
    commit_all(vault_dir, "vault backup", GIT_COMMITTER_DATE="2026-10-18T10:30:00+02:00",
               GIT_AUTHOR_DATE="2026-10-18T10:30:00+02:00")
    rows = {row["path"]: row for row in s.vault.changes_on(DAY)}
    assert rows["3-projects/Clara/Roadmap.md"]["status"] == "created" and not rows["3-projects/Clara/Roadmap.md"]["mine"]
    assert rows["3-projects/Clara/Clara.md"]["status"] == "changed"
    assert s.vault.changes_on(date(2026, 10, 19)) == []


# ----------------------------------------------------------------------
# The digest
# ----------------------------------------------------------------------
def test_a_local_day_is_cut_in_its_own_zone():
    start, end = day_bounds(DAY, "Europe/Paris")
    assert iso(start) == "2026-10-17T22:00:00+00:00" and iso(end) == "2026-10-18T22:00:00+00:00"
    assert french_date(DAY) == "dimanche 18 octobre 2026" and french_date(date(2026, 11, 1)) == "dimanche 1er novembre 2026"


async def test_the_digest_holds_the_days_conversations_with_what_came_before(s):
    early = chat(s, "Provider OpenCode")
    say(s, early, "user", "On avait parlé du modèle gratuit", "2026-10-17T09:00:00+00:00")
    say(s, early, "assistant", "Oui, il est dans le catalogue.", "2026-10-17T09:00:30+00:00")
    say(s, early, "user", "Ajoute-le au sélecteur", "2026-10-18T08:10:00+00:00", client="discord")
    say(s, early, "assistant", "C'est fait, c'est lié à la politique du modèle.", "2026-10-18T08:11:00+00:00")
    say(s, early, "tool", "ok", "2026-10-18T08:10:40+00:00", tool="ws_edit")
    barely_in = chat(s, "Dernière minute")
    say(s, barely_in, "user", "tard le soir", "2026-10-18T21:59:59+00:00")
    just_out = chat(s, "Minuit passé")
    say(s, just_out, "user", "le lendemain", "2026-10-18T22:00:00+00:00")
    secret = chat(s, "Secret", incognito=True)
    say(s, secret, "user", "ne pas voir", "2026-10-18T09:00:00+00:00")
    private = chat(s, "Local", local_only=True)
    say(s, private, "user", "ne pas voir non plus", "2026-10-18T09:00:00+00:00")
    digest = await DigestBuilder(s).build(DAY, "Europe/Paris", budget=60_000)
    text = digest.text
    assert "Digest de la journée du dimanche 18 octobre 2026" in text
    assert "«Provider OpenCode» · discord" in text and "Commencée le 17/10" in text
    assert "Derniers échanges avant ce jour: Toi: On avait parlé du modèle gratuit / Clara: Oui" in text
    assert "- 10:10 Toi: Ajoute-le au sélecteur" in text and "- 10:11 Clara: C'est fait" in text  # 08:10Z is 10:10 in Paris
    assert "Outils utilisés: ws_edit×1" in text and "Notes du vault liées: [[Politique]]" in text  # named in the chat
    assert "tard le soir" in text and "le lendemain" not in text
    assert "ne pas voir" not in text and set(digest.conversations) == {early, barely_in}


async def test_the_check_in_itself_is_not_part_of_the_days_digest(s):
    checkin = chat(s, "Bilan du 2026-10-18")
    say(s, checkin, "user", "j'ai couru", "2026-10-18T20:00:00+00:00")
    s.db.execute("INSERT INTO recaps (day, status, conversation_id, timezone, created_at) VALUES (?, 'asked', ?, ?, ?)",
                 ("2026-10-18", checkin, "Europe/Paris", "2026-10-18T21:59:00+00:00"))
    digest = await DigestBuilder(s).build(DAY, "Europe/Paris", budget=60_000)
    assert digest.conversations == [] and "Aucune conversation ni activité" in digest.text


async def test_the_digest_lists_tasks_reminders_vault_changes_and_what_clara_did(s, vault_dir):
    conversation = chat(s, "Ménage")
    say(s, conversation, "user", "ajoute une tâche", "2026-10-18T07:00:00+00:00")
    done = await s.tasks.create("Appeler le garage")
    s.db.execute("UPDATE tasks SET status = 'done', done_at = ? WHERE id = ?", ("2026-10-18T13:00:00+00:00", done.id))
    open_one = await s.tasks.create("Renouveler l'assurance", conversation_id=conversation)
    s.db.execute("UPDATE tasks SET created_at = '2026-10-10T08:00:00+00:00' WHERE id = ?", (open_one.id,))
    s.events.publish("reminder", "", "Prendre le médicament")
    s.db.execute("UPDATE events SET created_at = '2026-10-18T06:00:00+00:00'")
    s.db.execute("INSERT INTO audit_log (at, conversation_id, tool, target, level, situation, args_digest, effect, outcome)"
                 " VALUES ('2026-10-18T07:05:00+00:00', ?, 'ws_write', 'ws:notes/liste.md', 'write', 'interactive', 'x',"
                 " 'allow', 'ran')", (conversation,))
    (vault_dir / "3-projects" / "Clara" / "Roadmap.md").write_text("# Roadmap\n", encoding="utf-8")
    commit_all(vault_dir, "vault backup", GIT_COMMITTER_DATE="2026-10-18T10:30:00+02:00",
               GIT_AUTHOR_DATE="2026-10-18T10:30:00+02:00")
    digest = await DigestBuilder(s).build(DAY, "Europe/Paris", budget=60_000)
    text = digest.text
    assert f"[{done.id}] Appeler le garage (terminée à 15:00)" in text
    assert f"[{open_one.id}] Renouveler l'assurance (liée à une conversation du jour)" in text
    assert "08:00 Prendre le médicament" in text
    assert "créée [[Roadmap]] (par toi)" in text and "[[Roadmap]]" in digest.notes
    assert "07:05" not in text and "09:05 ws_write ws:notes/liste.md" in text


async def test_the_notes_a_conversation_read_or_wrote_are_offered_as_links_even_with_spaces(s, vault_dir):
    (vault_dir / "5-knowledge" / "Plan de la semaine.md").write_text("# Plan de la semaine\n", encoding="utf-8")
    conversation = chat(s, "Organisation")
    say(s, conversation, "user", "regarde mon plan", "2026-10-18T07:00:00+00:00")
    s.db.execute("INSERT INTO audit_log (at, conversation_id, tool, target, level, situation, args_digest, effect, outcome)"
                 " VALUES ('2026-10-18T07:01:00+00:00', ?, 'vault_read', 'vault:Plan de la semaine', 'read', 'interactive',"
                 " 'x', 'allow', 'ran')", (conversation,))
    digest = await DigestBuilder(s).build(DAY, "Europe/Paris", budget=60_000)
    assert "Notes du vault liées: [[Plan de la semaine]]" in digest.text and "[[Plan de la semaine]]" in digest.notes


async def test_what_a_private_conversation_made_stays_out_of_the_digest_too(s, vault_dir):
    private = chat(s, "Confidences", local_only=True)
    say(s, private, "user", "note ceci", "2026-10-18T07:00:00+00:00")
    hidden = await s.tasks.create("Voir le médecin pour mon secret", conversation_id=private)
    s.db.execute("UPDATE tasks SET created_at = '2026-10-18T07:00:00+00:00' WHERE id = ?", (hidden.id,))
    for tool, target in (("ws_write", "ws:notes/secret.md"), ("vault_write", "vault:5-knowledge/Journal intime.md")):
        s.db.execute("INSERT INTO audit_log (at, conversation_id, tool, target, level, situation, args_digest, effect,"
                     " outcome) VALUES ('2026-10-18T07:05:00+00:00', ?, ?, ?, 'write', 'interactive', 'x', 'allow', 'ran')",
                     (private, tool, target))
    (vault_dir / "5-knowledge" / "Journal intime.md").write_text("# Journal intime\n", encoding="utf-8")
    (vault_dir / "5-knowledge" / "Autre.md").write_text("# Autre\n", encoding="utf-8")
    commit_all(vault_dir, "vault backup", GIT_COMMITTER_DATE="2026-10-18T10:30:00+02:00",
               GIT_AUTHOR_DATE="2026-10-18T10:30:00+02:00")
    text = (await DigestBuilder(s).build(DAY, "Europe/Paris", budget=60_000)).text
    assert "secret" not in text and "Journal intime" not in text and "Confidences" not in text
    assert "[[Autre]]" in text  # a note of the same day that no private conversation made is still there


async def test_a_long_day_is_summarised_by_the_model_then_cut_when_that_fails(s):
    for number in range(3):
        conversation = chat(s, f"Long {number}")
        for turn in range(30):
            say(s, conversation, "user", f"question {turn} " + "x" * 200, f"2026-10-18T08:{turn:02d}:00+00:00")
            say(s, conversation, "assistant", f"réponse {turn} " + "y" * 200, f"2026-10-18T08:{turn:02d}:30+00:00")
    asked: list[str] = []

    async def summarise(title: str, excerpts: str) -> str:
        asked.append(title)
        return "Résumé court de la conversation."

    builder = DigestBuilder(s)
    digest = await builder.build(DAY, "Europe/Paris", budget=9_000, summarise=summarise)
    assert digest.summarised == len(asked) >= 1 and "condensé automatiquement" in digest.text
    assert digest.chars <= 9_500

    async def broken(title: str, excerpts: str) -> str:
        raise RuntimeError("model down")

    cut = await builder.build(DAY, "Europe/Paris", budget=9_000, summarise=broken)
    assert cut.summarised == 0 and cut.chars <= 9_500 and "question 0" in cut.text


# ----------------------------------------------------------------------
# The check-in
# ----------------------------------------------------------------------
QUESTION = "Voici ce que j'ai vu de ta journée… Qu'as-tu fait d'autre ?"


async def ask(s, llm, clock, now: str = "2026-10-18T21:59:10", reply: str = QUESTION) -> str:
    conversation = chat(s, "Provider OpenCode")
    say(s, conversation, "user", "Ajoute-le au sélecteur", "2026-10-18T08:10:00+00:00")
    say(s, conversation, "assistant", "C'est fait.", "2026-10-18T08:11:00+00:00")
    llm.replies = [reply]
    clock.now = datetime.fromisoformat(now).replace(tzinfo=UTC)
    assert s.recaps.fire_due() == 1
    await s.recaps.wait_running()
    return s.recaps.row("2026-10-18").conversation_id


async def test_the_day_is_asked_at_the_end_of_it_and_nothing_is_written(s, llm, clock, vault_dir):
    assert s.recaps.fire_due() == 0  # 12:00 in Paris: the day is not over
    cid = await ask(s, llm, clock)
    row = s.recaps.row("2026-10-18")
    assert row.status == "asked" and cid and row.attempts == 1
    event = s.events.recent(1, ["checkin"])[0]
    assert event.text == QUESTION and event.title == "Bilan du dimanche 18 octobre 2026"
    assert event.targets == ("web", "discord") and event.payload["conversation"] == cid and event.conversation_id == cid
    sent = llm.seen[-1]
    assert not sent["tools"] and "Digest de la journée du dimanche 18 octobre 2026" in sent["messages"][-1]["content"]
    assert "Ajoute-le au sélecteur" in sent["messages"][-1]["content"]
    assert "UN seul message" in sent["messages"][0]["content"]  # the opening instructions
    assert s.conversations.get(cid).title == "Bilan du 2026-10-18" and s.conversations.get(cid).vault_open
    assert not (vault_dir / "1-daily" / "2026").exists()  # she waits for the answer
    assert s.recaps.fire_due() == 0  # asked once


async def test_a_server_that_was_off_asks_at_its_next_start(s, llm, clock):
    await ask(s, llm, clock, now="2026-10-19T06:30:00")  # 08:30 the next morning
    assert s.recaps.row("2026-10-18").status == "asked"
    assert s.events.recent(1, ["checkin"])[0].payload["stage"] == "asked"


async def test_an_unanswered_check_in_is_reminded_once_the_next_morning(s, llm, clock):
    await ask(s, llm, clock)
    clock.now = datetime(2026, 10, 19, 6, 59, tzinfo=UTC)  # 08:59
    assert s.recaps.fire_due() == 0 and len(s.events.recent(5, ["checkin"])) == 1
    clock.now = datetime(2026, 10, 19, 7, 1, tzinfo=UTC)  # 09:01
    s.recaps.fire_due()
    reminder = s.events.recent(1, ["checkin"])[0]
    assert reminder.payload["stage"] == "reminder" and "attend toujours ta réponse" in reminder.text
    s.recaps.fire_due()
    assert len(s.events.recent(5, ["checkin"])) == 2  # once


async def test_a_check_in_asked_after_the_morning_reminder_time_is_not_reminded(s, llm, clock):
    await ask(s, llm, clock, now="2026-10-19T10:00:00")
    s.recaps.fire_due()
    assert len(s.events.recent(5, ["checkin"])) == 1


async def test_your_answer_leads_to_the_recap_and_the_confirmed_task(s, llm, clock, vault_dir):
    cid = await ask(s, llm, clock)
    task = await s.tasks.create("Appeler le garage")
    recap = "**Discussions**\n- Sélecteur de modèles, voir [[Clara]]\n\n**Fait hors de Clara**\n- Couru 5 km\n\n**Tâches terminées**\n- [x] Appeler le garage"
    llm.replies = [calls(("update_task", {"task_id": task.id, "status": "done"})),
                   calls(("write_daily_recap", {"content": recap})), "C'est écrit dans 1-daily/2026/2026-10-18.md."]
    answer = await s.agent.answer(ChatRequest(message="J'ai couru 5 km et appelé le garage.", client="discord",
                                              conversation=cid))
    assert "C'est écrit" in answer
    system = llm.seen[-3]["messages"][0]["content"]
    assert "Bilan du jour — dimanche 18 octobre 2026" in system and "1-daily/2026/2026-10-18.md" in system
    assert "write_daily_recap" in [tool["function"]["name"] for tool in llm.seen[-3]["tools"]]
    row = s.recaps.row("2026-10-18")
    assert row.status == "written" and row.note_path == "1-daily/2026/2026-10-18.md"
    note = (vault_dir / row.note_path).read_text(encoding="utf-8")
    assert "- Couru 5 km" in note and "[[Clara]]" in note and "<!-- /clara:recap -->" in note
    assert s.tasks.get(task.id).status == "done"
    written = s.events.recent(1, ["checkin"])[0]  # told to Discord, which removes the check-in's thread
    assert written.payload == {"day": "2026-10-18", "conversation": cid, "stage": "written", "path": row.note_path}
    assert written.targets == ("discord",) and "1-daily/2026/2026-10-18.md" in written.text
    later = s.conversations.get(cid)
    assert later is not None and s.recaps.context_block(type("State", (), {"conversation_id": cid})()).startswith("## Bilan du jour")


async def test_the_recap_tool_exists_only_in_a_check_in(s, llm):
    plain = chat(s, "Autre")
    llm.replies = ["ok"]
    await s.agent.answer(ChatRequest(message="salut", client="web", conversation=plain))
    assert "write_daily_recap" not in [tool["function"]["name"] for tool in llm.seen[-1]["tools"]]


async def test_the_next_days_check_in_closes_the_unanswered_one_from_its_digest(s, llm, clock, vault_dir):
    first = await ask(s, llm, clock)
    llm.replies = [calls(("write_daily_recap", {"content": "**À confirmer**\n- Sélecteur de modèles"})), "Récap écrit.",
                   "Voici ta journée du 19."]
    clock.now = datetime(2026, 10, 19, 21, 59, 30, tzinfo=UTC)  # 23:59 the next day
    assert s.recaps.fire_due() == 1
    await s.recaps.wait_running()
    old, new = s.recaps.row("2026-10-18"), s.recaps.row("2026-10-19")
    assert old.status == "closed" and old.note_path and new.status == "asked" and new.conversation_id != first
    assert any("Aucune réponse reçue" in m.content for m in s.conversations.messages_after(first))
    stages = [event.payload.get("stage") for event in s.events.recent(5, ["checkin"])]
    assert sorted(stages[:2]) == ["asked", "closed"] and "written" not in stages
    assert "À confirmer" in (vault_dir / old.note_path).read_text(encoding="utf-8")
    # a late answer still updates the closed recap
    llm.replies = [calls(("write_daily_recap", {"content": "**Fait hors de Clara**\n- Couru"})), "Mis à jour."]
    await s.agent.answer(ChatRequest(message="J'avais couru.", client="web", conversation=first))
    assert s.recaps.row("2026-10-18").status == "written"


async def test_a_failed_attempt_is_retried_then_raises_an_alert(s, llm, clock, monkeypatch):
    class Broken:
        def __init__(self, services):
            pass

        async def build(self, *args, **kwargs):
            raise RuntimeError("the database is locked")

    monkeypatch.setattr(recap_service, "DigestBuilder", Broken)
    clock.now = datetime(2026, 10, 18, 21, 59, 10, tzinfo=UTC)
    for attempt in (1, 2, 3):
        assert s.recaps.fire_due() == 1
        await s.recaps.wait_running()
        row = s.recaps.row("2026-10-18")
        assert row.status == "failed" and row.attempts == attempt and "database is locked" in row.error
        assert s.recaps.fire_due() == 0  # not before ten minutes
        clock.now += timedelta(seconds=700)
    assert "recap" in s.alerts.active and s.recaps.fire_due() == 0  # three attempts, then it stops
    assert not s.events.recent(5, ["checkin"])


async def test_an_ask_cut_by_a_crash_is_asked_again(s):
    s.db.execute("INSERT INTO recaps (day, status, timezone, attempts, created_at) VALUES ('2026-10-18', 'asking', ?, 1, ?)",
                 ("Europe/Paris", "2026-10-18T21:59:00+00:00"))
    assert s.recaps.recover() == 1 and s.recaps.row("2026-10-18") is None


async def test_the_console_can_ask_skip_and_close_a_day(s, llm, clock):
    clock.now = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    llm.replies = [QUESTION]
    assert "Asking for 2026-10-18" in s.recaps.ask_now(DAY)
    await s.recaps.wait_running()
    with pytest.raises(RecapError):
        s.recaps.ask_now(DAY)
    assert s.recaps.row("2026-10-18").status == "asked" and "2026-10-18" in s.recaps.describe()
    assert "will not be recapped" in s.recaps.skip(DAY) and s.recaps.row("2026-10-18").status == "skipped"
    with pytest.raises(RecapError):
        await s.recaps.close_now(DAY)
    assert "will not be recapped" in s.recaps.skip(date(2026, 10, 16))
    clock.now = datetime(2026, 10, 18, 21, 59, 10, tzinfo=UTC)
    assert s.recaps.fire_due() == 0  # a skipped day is not asked
    assert s.recaps.row("2026-10-18").status == "skipped" and len(s.events.recent(5, ["checkin"])) == 1


async def test_the_recap_is_off_by_itself_without_the_setting(environ, vault_dir, llm, clock):
    settings = load_settings({**environ, "CLARA_VAULT_PATH": str(vault_dir)}, use_file=False)
    db = open_database(settings)
    services = build_services(settings, db, provider_factory=llm.factory)
    extend(services)
    services.recaps._clock = clock
    clock.now = datetime(2026, 10, 18, 21, 59, 10, tzinfo=UTC)
    assert services.recaps.fire_due() == 0 and services.recaps.next_due() is None
    assert "off" in services.recaps.describe()
    services.traffic.close()
    db.close()


def test_without_a_vault_there_is_no_recap(services):
    assert services.recaps is None and "write_daily_recap" not in services.toolbox.names
