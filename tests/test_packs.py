"""Tool packs: the vault's daily blocks, a day read back (conversations, activity, files), and what each conversation is
offered."""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from clara.agent.turn import ChatRequest
from clara.app import build_services, create_app, extend, open_database
from clara.knowledge.vault import VaultError
from clara.knowledge.vault.git import init_repository
from clara.packs import HistoryReader, PackError, day_bounds, french_date
from clara.packs.files import FilesReader
from clara.settings import load_settings
from clara.timeutil import iso
from fakes import calls

SCHEMA = {"archive": "8-archive", "daily_format": "YYYY/YYYY-MM-DD",
          "types": {"daily": {"folder": "1-daily"}, "project": {"folder": "3-projects"}}}
TEMPLATE = "---\ntype: daily\ncreated: {{date}}\nupdated: {{date}}\nauthor: erwan\n---\n\n# {{date:YYYY-MM-DD dddd}}\n\n## Log\n\n## Tasks\n\n## Notes\n"
DAY = date(2026, 10, 18)
PARIS = "Europe/Paris"
DATED = {"GIT_COMMITTER_DATE": "2026-10-18T10:30:00+02:00", "GIT_AUTHOR_DATE": "2026-10-18T10:30:00+02:00"}


def commit_all(root: Path, message: str = "setup", **env) -> None:
    git = ["git", "-c", "user.name=Erwan", "-c", "user.email=e@example.org"]
    subprocess.run([*git, "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run([*git, "commit", "-q", "-m", message], cwd=root, check=True, capture_output=True,
                   env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root), **env})


@pytest.fixture
def vault_dir(tmp_path) -> Path:
    root = tmp_path / "vault"
    for folder in ("_clara", "_templates", "1-daily", "3-projects/Clara", "5-knowledge"):
        (root / folder).mkdir(parents=True)
    (root / "_clara" / "schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
    (root / "_templates" / "daily.md").write_text(TEMPLATE, encoding="utf-8")
    (root / "1-daily" / "Daily Index.md").write_text("# Daily Index\n", encoding="utf-8")
    (root / "3-projects" / "Clara" / "Clara.md").write_text("# Clara\nThe assistant.\n", encoding="utf-8")
    (root / "5-knowledge" / "Politique.md").write_text("# Politique\n", encoding="utf-8")
    init_repository(root)
    commit_all(root, GIT_COMMITTER_DATE="2026-10-01T09:00:00+00:00", GIT_AUTHOR_DATE="2026-10-01T09:00:00+00:00")
    return root


@pytest.fixture
def s(environ, vault_dir, llm):
    settings = load_settings({**environ, "CLARA_VAULT_PATH": str(vault_dir), "CLARA_VAULT_PUSH": "false"}, use_file=False)
    db = open_database(settings)
    services = build_services(settings, db, provider_factory=llm.factory)
    extend(services)
    services.runtime.set("default_model", "local:fake")
    yield services
    services.traffic.close()
    db.close()


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


def say(s, conversation: str, role: str, content: str, at: str, client: str = "web", tool: str | None = None) -> None:
    s.db.execute("INSERT INTO messages (conversation_id, role, content, client, tool_name, created_at)"
                 " VALUES (?, ?, ?, ?, ?, ?)", (conversation, role, content, client, tool, at))


def chat(s, title: str, **options) -> str:
    return s.conversations.create("web", title=title, **options).id


async def history(s, **options):
    return await HistoryReader(s).conversations(DAY, PARIS, budget=options.pop("budget", 60_000), **options)


# ----------------------------------------------------------------------
# The vault's daily note
# ----------------------------------------------------------------------
def test_the_daily_note_follows_the_layout_of_the_clara_vault(s):
    assert s.vault.daily_path(DAY) == "1-daily/2026/2026-10-18.md"


def test_the_older_schema_layout_still_works(s, vault_dir):
    (vault_dir / "_clara" / "schema.json").write_text(json.dumps({"daily": {"folder": "Journal", "format": "%d-%m-%Y"}}))
    assert s.vault.daily_path(DAY) == "Journal/18-10-2026.md"


async def test_a_block_fills_one_part_of_the_note_and_rewrites_only_it(s, vault_dir):
    path, dropped = await s.vault.write_block(DAY, "**Discussions**\n- Parlé de [[Clara]] et de [[Inconnue]], [[clara|le projet]]")
    assert path == "1-daily/2026/2026-10-18.md" and dropped == ["Inconnue"]
    note = (vault_dir / path).read_text(encoding="utf-8")
    assert note.startswith("---\ntype: daily") and "# 2026-10-18 Sunday" in note  # the template, dates filled
    assert "## Récap (Clara)\n<!-- clara:recap -->\n**Discussions**" in note
    assert "[[Clara]]" in note and "[[Clara|le projet]]" in note and "[[Inconnue]]" not in note and "Inconnue" in note
    (vault_dir / path).write_text(note.replace("## Log\n", "## Log\n- 09:10 un mot à moi\n"), encoding="utf-8")
    await s.vault.write_block(DAY, "**Discussions**\n- Version corrigée")
    await s.vault.write_block(DAY, "- Une autre chose", block="sport", heading="Sport")
    again = (vault_dir / path).read_text(encoding="utf-8")
    assert again.count("<!-- clara:recap -->") == 1 and "Version corrigée" in again and "Parlé de" not in again
    assert "- 09:10 un mot à moi" in again and "## Sport\n<!-- clara:sport -->\n- Une autre chose" in again
    assert s.vault.git.git("log", "-1", "--format=%s").strip().startswith("Clara: daily note block sport")


async def test_a_bad_block_is_refused(s):
    for kwargs in ({"body": "  <!-- clara:recap -->  "}, {"body": "x", "block": "Bad Name"}):
        with pytest.raises(VaultError):
            await s.vault.write_block(DAY, **kwargs)


async def test_a_line_added_to_todays_note_goes_under_log(s, vault_dir):
    await s.vault.daily("append", "appelé le garage")
    note = (vault_dir / s.vault.daily_path(datetime.now(ZoneInfo(PARIS)))).read_text(encoding="utf-8")
    assert note.index("## Log") < note.index("appelé le garage") < note.index("## Tasks")


def test_the_notes_changed_on_a_day_come_from_git(s, vault_dir):
    (vault_dir / "3-projects" / "Clara" / "Roadmap.md").write_text("# Roadmap\n", encoding="utf-8")
    (vault_dir / "3-projects" / "Clara" / "Clara.md").write_text("# Clara\nMore.\n", encoding="utf-8")
    commit_all(vault_dir, "vault backup", **DATED)
    rows = {row["path"]: row for row in s.vault.changes_on(DAY)}
    assert rows["3-projects/Clara/Roadmap.md"]["status"] == "created" and not rows["3-projects/Clara/Roadmap.md"]["mine"]
    assert rows["3-projects/Clara/Clara.md"]["status"] == "changed" and rows["3-projects/Clara/Clara.md"]["at"].startswith("2026-10-18")
    assert s.vault.changes_on(date(2026, 10, 19)) == []
    since, until = s.vault.day_span(DAY)
    assert s.vault.git.numstat(since, until)["3-projects/Clara/Clara.md"] == (1, 1)
    assert "+More." in s.vault.git.diff("3-projects/Clara/Clara.md", since, until)


# ----------------------------------------------------------------------
# The notes a conversation wrote (the side panel)
# ----------------------------------------------------------------------
async def test_the_notes_a_conversation_wrote_are_found_from_the_line_on_its_commits(s, vault_dir):
    mine, other = "c" + "1" * 16, "c" + "2" * 16
    await s.vault.write("Ideas/Kayak.md", "# Kayak\n", "create", conversation=mine)
    await s.vault.write("Ideas/Draft.md", "# Draft\n", "create", conversation=mine)
    await s.vault.write("Ideas/Elsewhere.md", "# Elsewhere\n", "create", conversation=other)
    await s.vault.edit("Clara", "The assistant.", "The assistant, rebuilt.", conversation=mine)  # a note that was there
    await s.vault.edit("Kayak", "", "", append="- paddles", conversation=mine)  # one she made, then changed
    await s.vault.daily("append", "appelé le garage", conversation=mine)
    await s.vault.move("Draft", "3-projects/Clara/Plan.md", conversation=mine)
    await s.vault.write("Ideas/Gone.md", "# Gone\n", "create", conversation=mine)
    await s.vault.write("Gone", "", "delete", conversation=mine)
    await s.vault.write("Politique", "# Politique\nChanged.\n", "replace")  # no conversation: nobody's
    assert s.vault.git.git("log", "-1", "--format=%B", "--", "Ideas/Elsewhere.md").strip() == (
        f"Clara: create Ideas/Elsewhere.md\n\nConversation: {other}")
    assert s.vault.git.git("log", "-1", "--format=%B", "--", "5-knowledge/Politique.md").strip() == "Clara: rewrite 5-knowledge/Politique.md"

    notes = {note["path"]: note for note in await s.vault.conversation_notes(mine)}
    today = s.vault.daily_path(datetime.now(ZoneInfo(PARIS)))
    assert set(notes) == {"Ideas/Kayak.md", "3-projects/Clara/Plan.md", "3-projects/Clara/Clara.md", today}
    assert notes["Ideas/Kayak.md"]["action"] == "created"  # changed afterwards, still a note she made
    assert notes["3-projects/Clara/Plan.md"]["action"] == "created"  # moved, still a note she made, at its new path
    assert notes["3-projects/Clara/Clara.md"]["action"] == "edited"
    assert notes[today]["action"] == "created"  # the template made it
    note = notes["Ideas/Kayak.md"]
    assert note["size"] == len("# Kayak\n- paddles") and note["created"] <= note["updated"]
    assert [item["path"] for item in await s.vault.conversation_notes(other)] == ["Ideas/Elsewhere.md"]
    assert await s.vault.conversation_notes("c" + "3" * 16) == [] and await s.vault.conversation_notes("not an id") == []
    assert len(await s.vault.conversation_notes(mine, limit=1)) == 1


async def test_the_panel_lists_the_vault_notes_clara_wrote_in_that_conversation(client, s, llm):
    cid = chat(s, "Notes", packs=["web", "second_brain"])
    llm.replies = [calls(("vault_write", {"note": "Ideas/Kayak.md", "content": "# Kayak\n"}),
                         ("vault_edit", {"note": "Clara", "old_text": "The assistant.", "new_text": "The agent."})),
                   "Written."]
    await s.agent.answer(ChatRequest(message="note it", client="web", conversation=cid))
    notes = (await client.get(f"/v1/conversations/{cid}/panel")).json()["notes"]
    assert {item["path"]: item["action"] for item in notes} == {"Ideas/Kayak.md": "created", "3-projects/Clara/Clara.md": "edited"}
    assert all(item["created"] and item["size"] > 0 for item in notes)
    assert (await client.get(f"/v1/conversations/{chat(s, 'Other')}/panel")).json()["notes"] == []


# ----------------------------------------------------------------------
# The conversations of a day
# ----------------------------------------------------------------------
def test_a_local_day_is_cut_in_its_own_zone():
    start, end = day_bounds(DAY, PARIS)
    assert iso(start) == "2026-10-17T22:00:00+00:00" and iso(end) == "2026-10-18T22:00:00+00:00"
    assert french_date(DAY) == "dimanche 18 octobre 2026" and french_date(date(2026, 11, 1)) == "dimanche 1er novembre 2026"


async def test_a_day_holds_its_conversations_with_what_came_before(s):
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
    found = await history(s)
    text = found.text
    assert "Conversations du dimanche 18 octobre 2026" in text
    assert "«Provider OpenCode» · discord" in text and "Commencée le 17/10" in text
    assert "Derniers échanges avant ce jour: Toi: On avait parlé du modèle gratuit / Clara: Oui" in text
    assert "- 10:10 Toi: Ajoute-le au sélecteur" in text and "- 10:11 Clara: C'est fait" in text  # 08:10Z is 10:10 in Paris
    assert "Outils utilisés: ws_edit×1" in text and "Notes du vault liées: [[Politique]]" in text
    assert "tard le soir" in text and "le lendemain" not in text
    assert "ne pas voir" not in text and set(found.ids) == {early, barely_in}


async def test_a_local_only_viewer_may_read_local_only_conversations_but_nobody_incognito_ones(s):
    private = chat(s, "Confidences", local_only=True)
    say(s, private, "user", "mes confidences", "2026-10-18T09:00:00+00:00")
    secret = chat(s, "Secret", incognito=True)
    say(s, secret, "user", "rien du tout", "2026-10-18T09:00:00+00:00")
    assert "mes confidences" not in (await history(s)).text
    seen = (await history(s, viewer_local_only=True)).text
    assert "mes confidences" in seen and "rien du tout" not in seen
    assert "mes confidences" in HistoryReader(s).read_conversation(private, None, PARIS, after=0, max_chars=5000, viewer_local_only=True)
    for viewer in (False, True):
        with pytest.raises(ValueError):
            HistoryReader(s).read_conversation(secret, None, PARIS, after=0, max_chars=5000, viewer_local_only=viewer)
    with pytest.raises(ValueError):
        HistoryReader(s).read_conversation(private, None, PARIS, after=0, max_chars=5000, viewer_local_only=False)


async def test_the_notes_a_conversation_read_or_wrote_are_offered_as_links_even_with_spaces(s, vault_dir):
    (vault_dir / "5-knowledge" / "Plan de la semaine.md").write_text("# Plan de la semaine\n", encoding="utf-8")
    conversation = chat(s, "Organisation")
    say(s, conversation, "user", "regarde mon plan", "2026-10-18T07:00:00+00:00")
    s.db.execute("INSERT INTO audit_log (at, conversation_id, tool, target, level, situation, args_digest, effect, outcome)"
                 " VALUES ('2026-10-18T07:01:00+00:00', ?, 'vault_read', 'vault:Plan de la semaine', 'read', 'interactive',"
                 " 'x', 'allow', 'ran')", (conversation,))
    found = await history(s)
    assert "Notes du vault liées: [[Plan de la semaine]]" in found.text and "[[Plan de la semaine]]" in found.notes


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

    found = await history(s, budget=9_000, summarise=summarise)
    assert found.summarised == len(asked) == 3 and "condensé automatiquement" in found.text and len(found.text) <= 9_500

    async def broken(title: str, excerpts: str) -> str:
        raise RuntimeError("model down")

    cut = await history(s, budget=9_000, summarise=broken)
    assert cut.summarised == 0 and len(cut.text) <= 9_500 and "question 0" in cut.text


def test_one_conversation_is_read_in_pages(s):
    conversation = chat(s, "Longue")
    for turn in range(10):
        say(s, conversation, "user", f"question {turn} " + "x" * 100, f"2026-10-18T08:{turn:02d}:00+00:00")
    reader = HistoryReader(s)
    first = reader.read_conversation(conversation, DAY, PARIS, after=0, max_chars=450, viewer_local_only=False)
    assert "question 0" in first and "question 9" not in first and "rappelle read_conversation avec after=" in first
    last_id = int(first.rsplit("after=", 1)[1].split("]")[0])
    second = reader.read_conversation(conversation, DAY, PARIS, after=last_id, max_chars=450, viewer_local_only=False)
    assert "question 0" not in second and "question" in second


async def test_the_activity_of_a_day_leaves_out_what_a_private_conversation_made(s):
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
    private = chat(s, "Confidences", local_only=True)
    say(s, private, "user", "note ceci", "2026-10-18T07:00:00+00:00")
    hidden = await s.tasks.create("Voir le médecin pour mon secret", conversation_id=private)
    s.db.execute("UPDATE tasks SET created_at = '2026-10-18T07:00:00+00:00' WHERE id = ?", (hidden.id,))
    s.db.execute("INSERT INTO audit_log (at, conversation_id, tool, target, level, situation, args_digest, effect, outcome)"
                 " VALUES ('2026-10-18T07:05:00+00:00', ?, 'ws_write', 'ws:notes/secret.md', 'write', 'interactive', 'x',"
                 " 'allow', 'ran')", (private,))
    text = HistoryReader(s).activity(DAY, PARIS)
    assert f"[{done.id}] Appeler le garage (terminée à 15:00)" in text
    assert f"[{open_one.id}] Renouveler l'assurance (liée à une conversation du jour)" in text
    assert "08:00 Prendre le médicament" in text and "09:05 ws_write ws:notes/liste.md" in text
    assert "secret" not in text
    assert "secret" in HistoryReader(s).activity(DAY, PARIS, viewer_local_only=True)  # a local-only viewer sees it


# ----------------------------------------------------------------------
# The files of a day
# ----------------------------------------------------------------------
@pytest.fixture
def dated_workspace(s, monkeypatch):
    original = s.workspace._env
    monkeypatch.setattr(s.workspace, "_env", lambda: {**original(), **DATED})
    return s.workspace


async def test_the_files_of_a_day_are_listed_short_from_four_sources(s, vault_dir, dated_workspace):
    await dated_workspace.write("notes/liste.md", "# Liste de courses\nlait\n", overwrite=False, message="ws_write: liste")
    await dated_workspace.write("notes/vieux.md", "ancien\n", overwrite=False, message="ws_write: vieux")
    (vault_dir / "3-projects" / "Clara" / "Roadmap.md").write_text("---\nstatus: active\n---\n# Roadmap du projet\n", encoding="utf-8")
    commit_all(vault_dir, "vault backup", **DATED)
    conversation = chat(s, "Papiers")
    s.db.execute("INSERT INTO conversation_files (conversation_id, name, kind, mime, digest, size, data, client, created_at)"
                 " VALUES (?, 'facture.txt', 'text', '', 'd1', 12, ?, 'web', '2026-10-18T08:00:00+00:00')", (conversation, b"Facture EDF"))
    s.db.execute("INSERT INTO created_files (kind, locator, name, conversation_id, created_at)"
                 " VALUES ('drive', 'abc123', 'Compte rendu', ?, '2026-10-18T09:00:00+00:00')", (conversation,))
    text = await FilesReader(s).catalogue(DAY, PARIS)
    assert "ws:notes/liste.md · créé 10:30 · par Clara · +2 −0" in text and "«Liste de courses»" in text
    assert "vault:3-projects/Clara/Roadmap.md · créé 10:30 · par toi" in text and "«Roadmap du projet»" in text
    assert "drive:abc123" in text and "Compte rendu" in text and "attached:" in text and "facture.txt" in text
    assert "lait" not in text  # no content, only a few words of what it is
    only = await FilesReader(s).catalogue(DAY, PARIS, source="vault")
    assert "Roadmap" in only and "ws:" not in only
    assert "Aucun fichier" in await FilesReader(s).catalogue(date(2026, 10, 19), PARIS)
    with pytest.raises(ValueError):
        await FilesReader(s).catalogue(DAY, PARIS, source="mail")


async def test_a_long_list_of_files_is_cut_and_goes_on(s, dated_workspace):
    for number in range(65):
        await dated_workspace.write(f"many/f{number:02d}.txt", f"fichier {number}\n", overwrite=False, message=f"ws_write: {number}")
    first = await FilesReader(s).catalogue(DAY, PARIS)
    assert first.count("ws:many/") == 60 and "5 autres" in first and "offset=60" in first
    assert first.count("\n- ") == 60
    assert "ws:many/" in await FilesReader(s).catalogue(DAY, PARIS, offset=60)


async def test_what_you_sent_in_a_private_conversation_is_not_listed_to_others(s):
    private = chat(s, "Confidences", local_only=True)
    s.db.execute("INSERT INTO conversation_files (conversation_id, name, kind, mime, digest, size, data, client, created_at)"
                 " VALUES (?, 'secret.txt', 'text', '', 'd2', 5, ?, 'web', '2026-10-18T08:00:00+00:00')", (private, b"abcde"))
    assert "secret.txt" not in await FilesReader(s).catalogue(DAY, PARIS)
    assert "secret.txt" in await FilesReader(s).catalogue(DAY, PARIS, viewer_local_only=True)
    number = s.db.value("SELECT id FROM conversation_files")
    with pytest.raises(ValueError):
        await FilesReader(s).read(f"attached:{number}", mode="full", day=DAY, tz_name=PARIS, from_line=1, max_chars=5000,
                                  viewer_local_only=False)


async def test_a_file_is_read_whole_or_as_the_days_diff(s, vault_dir, dated_workspace):
    await dated_workspace.write("notes/liste.md", "ligne 1\nligne 2\n", overwrite=False, message="ws_write: liste")
    (vault_dir / "3-projects" / "Clara" / "Clara.md").write_text("# Clara\nThe assistant.\nUne ligne ajoutée.\n", encoding="utf-8")
    commit_all(vault_dir, "vault backup", **DATED)
    reader = FilesReader(s)

    async def read(ref, mode="full"):
        return (await reader.read(ref, mode=mode, day=DAY, tz_name=PARIS, from_line=1, max_chars=5000, viewer_local_only=False))[0]

    assert "ligne 2" in await read("ws:notes/liste.md")
    diff = await read("ws:notes/liste.md", "diff")
    assert "+ligne 1" in diff and "diff of 2026-10-18" in diff
    note = await read("vault:3-projects/Clara/Clara.md")
    assert "Une ligne ajoutée." in note and "The assistant." in note
    changed = await read("vault:3-projects/Clara/Clara.md", "diff")
    assert "+Une ligne ajoutée." in changed and "+The assistant." not in changed
    assert "did not change" in (await reader.read("ws:notes/liste.md", mode="diff", day=date(2026, 10, 20), tz_name=PARIS,
                                                   from_line=1, max_chars=5000, viewer_local_only=False))[0]
    sent = chat(s, "Papiers")
    s.db.execute("INSERT INTO conversation_files (conversation_id, name, kind, mime, digest, size, data, client, created_at)"
                 " VALUES (?, 'facture.txt', 'text', '', 'd1', 11, ?, 'web', '2026-10-18T08:00:00+00:00')", (sent, b"Facture EDF"))
    assert "Facture EDF" in await read(f"attached:{s.db.value('SELECT id FROM conversation_files')}")
    with pytest.raises(ValueError):
        await read("ftp:thing")
    with pytest.raises(ValueError):
        await read("ws:notes/liste.md", "patch")


# ----------------------------------------------------------------------
# What a conversation is offered
# ----------------------------------------------------------------------
def offered(llm) -> set[str]:
    return {tool["function"]["name"] for tool in llm.seen[-1]["tools"]}


async def test_special_tools_exist_only_where_their_pack_is_on(s, llm):
    plain = chat(s, "Simple")
    llm.replies = ["ok"]
    await s.agent.answer(ChatRequest(message="salut", client="web", conversation=plain))
    assert not offered(llm) & {"day_conversations", "files_created", "day_activity", "read_conversation", "ask_person",
                               "finish_run", "discord_thread", "daily_notes"}
    assert "web_search" in offered(llm)  # web is on by default
    s.conversations.update(plain, packs=["day_conversations", "files_created", "ask_person"])
    llm.replies = ["ok"]
    await s.agent.answer(ChatRequest(message="salut", client="web", conversation=plain))
    tools = offered(llm)
    assert {"day_conversations", "read_conversation", "files_created", "read_created_file"} <= tools
    assert "web_search" not in tools and "day_activity" not in tools
    assert "ask_person" not in tools and "finish_run" not in tools  # for schedules: this is no run


async def test_the_second_brain_switch_is_the_vault_being_open(s, llm):
    cid = chat(s, "Notes")
    assert s.conversations.get(cid).describe()["packs"] == ["web"]
    s.conversations.update(cid, packs=["web", "second_brain"])
    current = s.conversations.get(cid)
    assert current.vault_open and current.describe()["packs"] == ["web", "second_brain"]
    s.conversations.update(cid, packs=["web"])
    assert not s.conversations.get(cid).vault_open


async def test_a_day_is_read_by_the_model_through_its_tools(s, llm):
    other = chat(s, "Provider OpenCode")
    say(s, other, "user", "Ajoute-le au sélecteur", "2026-10-18T08:10:00+00:00")
    cid = chat(s, "Question", packs=["web", "day_conversations", "day_activity"])
    llm.replies = [calls(("day_conversations", {"date": "2026-10-18"}), ("day_activity", {"date": "2026-10-18"})),
                   calls(("read_conversation", {"conversation": other, "date": "2026-10-18"})), "Tu as ajouté le sélecteur."]
    assert await s.agent.answer(ChatRequest(message="Qu'ai-je fait le 18 ?", client="web", conversation=cid)) == "Tu as ajouté le sélecteur."
    results = [m["content"] for m in llm.seen[-1]["messages"] if m["role"] == "tool"]
    assert any("Ajoute-le au sélecteur" in text and "Provider OpenCode" in text for text in results)
    assert any("Activité du dimanche 18 octobre 2026" in text for text in results)
    assert llm.seen[-1]["messages"][-1]["role"] == "tool" and "[#" in llm.seen[-1]["messages"][-1]["content"]


async def test_a_local_only_conversation_reads_local_only_ones_through_the_tool(s, llm):
    private = chat(s, "Confidences", local_only=True)
    say(s, private, "user", "mes confidences", "2026-10-18T09:00:00+00:00")
    for local_only, expected in ((False, False), (True, True)):
        cid = chat(s, "Question", packs=["web", "day_conversations"], local_only=local_only)
        llm.replies = [calls(("day_conversations", {"date": "2026-10-18"})), "ok"]
        await s.agent.answer(ChatRequest(message="le 18 ?", client="web", conversation=cid))
        result = next(m["content"] for m in llm.seen[-1]["messages"] if m["role"] == "tool")
        assert ("mes confidences" in result) is expected, local_only


async def test_the_tool_without_a_date_means_the_day_of_the_run(s, llm):
    from clara.tools.base import ToolContext, TurnState
    from clara.tools.days import resolve_day

    schedule = s.schedules.create("Bilan", "x", "2030-01-01T23:59", timezone=PARIS, repeat="daily", packs=["day_conversations"])
    conversation = chat(s, "Run")
    run = s.db.insert("INSERT INTO schedule_runs (schedule_id, due_at, status, conversation_id) VALUES (?, '2026-10-18T21:59:00+00:00',"
                      " 'running', ?)", (schedule.id, conversation))
    context = ToolContext(s, TurnState(turn_id="t", conversation_id=conversation, client="server", timezone=PARIS))
    assert run and resolve_day(context) == DAY  # 23:59 in Paris is still the 18th
    assert resolve_day(context, "2026-10-12") == date(2026, 10, 12)
    assert resolve_day(ToolContext(s, TurnState(turn_id="t", conversation_id="", client="web", timezone=PARIS))) == datetime.now(ZoneInfo(PARIS)).date()
    from clara.tools.base import ToolError

    with pytest.raises(ToolError):
        resolve_day(context, "someday")


async def test_the_daily_notes_of_a_range_are_read(s, vault_dir):
    from clara.tools.base import ToolContext, TurnState

    for day in (16, 17):
        path = vault_dir / "1-daily" / "2026" / f"2026-10-{day}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# 2026-10-{day}\n- Note du {day}\n", encoding="utf-8")
    from clara.packs.tools import _daily_notes

    context = ToolContext(s, TurnState(turn_id="t", conversation_id="", client="web", timezone=PARIS))
    text = await _daily_notes(context, "2026-10-15", "2026-10-18")
    assert "Note du 16" in text and "Note du 17" in text and text.index("Note du 16") < text.index("Note du 17")


# ----------------------------------------------------------------------
# The registry and the API
# ----------------------------------------------------------------------
def test_the_registry_checks_the_packs_and_where_they_belong(s):
    assert s.packs.check(["files_created", "web"], "chat") == ["web", "files_created"]
    with pytest.raises(PackError):
        s.packs.check(["nothing"], "chat")
    with pytest.raises(PackError):
        s.packs.check(["ask_person"], "chat")  # for schedules only
    assert s.packs.check(["ask_person", "discord_thread"], "schedule") == ["ask_person", "discord_thread"]
    chat_ids = {pack["id"] for pack in s.packs.listing("chat")}
    assert "files_created" in chat_ids and "ask_person" not in chat_ids
    assert {pack["id"] for pack in s.packs.listing("schedule")} >= {"ask_person", "discord_thread", "web"}


def test_a_pack_that_cannot_be_used_says_why(s):
    discord = next(pack for pack in s.packs.listing("schedule") if pack["id"] == "discord_thread")
    assert not discord["available"] and "not connected" in discord["reason"]
    s.clients["discord"] = __import__("time").monotonic()
    assert next(pack for pack in s.packs.listing("schedule") if pack["id"] == "discord_thread")["available"]


async def test_the_api_lists_the_packs_and_saves_them_with_the_conversation(client, s):
    listing = (await client.get("/v1/packs", params={"where": "chat"})).json()["packs"]
    assert [pack["id"] for pack in listing][:2] == ["web", "second_brain"] and all(pack["where"] != "schedule" for pack in listing)
    made = (await client.post("/v1/conversations", json={"packs": ["day_conversations"]})).json()
    assert made["packs"] == ["day_conversations"]
    changed = (await client.patch(f"/v1/conversations/{made['id']}", json={"packs": ["web", "second_brain", "files_created"]})).json()
    assert changed["packs"] == ["web", "files_created", "second_brain"] or changed["packs"] == ["web", "second_brain", "files_created"]
    assert s.conversations.get(made["id"]).vault_open
    assert (await client.patch(f"/v1/conversations/{made['id']}", json={"packs": ["ask_person"]})).status_code == 422
    assert (await client.post("/v1/conversations", json={"packs": ["bogus"]})).status_code == 422
    assert (await client.get(f"/v1/conversations/{made['id']}")).json()["packs"]


# ----------------------------------------------------------------------
# The QCM pack, and the tools a project starts its conversations with
# ----------------------------------------------------------------------
async def test_the_qcm_is_a_pack_off_until_it_is_switched_on(client, s, llm):
    async def offered(conversation: str, **request) -> set[str]:
        llm.seen.clear()
        llm.replies = ["ok"]
        await s.agent.answer(ChatRequest(message="hello", conversation=conversation, client="web", **request))
        return {tool["function"]["name"] for tool in llm.seen[0]["tools"]}

    assert "qcm" not in await offered(chat(s, "plain"))
    assert "qcm" in await offered(chat(s, "quiz", packs=["web", "qcm"]))
    listing = {pack["id"]: pack for pack in (await client.get("/v1/packs", params={"where": "chat"})).json()["packs"]}
    assert listing["qcm"]["tools"] == ["qcm"] and not listing["qcm"]["default"] and listing["qcm"]["available"]
    assert "qcm" not in {pack["id"] for pack in s.packs.listing("schedule")}  # a run has nobody to answer it


async def test_a_project_starts_its_conversations_with_its_tools(client, s, llm):
    made = (await client.post("/v1/projects", json={"name": "Maths"})).json()
    assert made["packs"] == ["web"]  # nothing chosen: as a conversation outside any project
    project = (await client.post("/v1/projects", json={"name": "Lessons", "packs": ["qcm", "web", "second_brain"]})).json()
    assert project["packs"] == ["web", "second_brain", "qcm"]  # the registry's order, as a conversation has them
    assert (await client.get(f"/v1/projects/{project['id']}")).json()["packs"] == project["packs"]
    assert next(item for item in (await client.get("/v1/projects")).json()["projects"] if item["id"] == project["id"])["packs"] == project["packs"]

    # a new conversation of the project, from any client, starts with them; one given its own tools keeps those
    started = (await client.post("/v1/conversations", json={"project": project["id"]})).json()
    assert sorted(started["packs"]) == sorted(project["packs"]) and s.conversations.get(started["id"]).vault_open
    assert (await client.post("/v1/conversations", json={"project": project["id"], "packs": ["day_activity"]})).json()["packs"] == ["day_activity"]
    assert (await client.post("/v1/conversations", json={})).json()["packs"] == ["web"]
    assert (await client.post("/v1/conversations", json={"project": made["id"]})).json()["packs"] == ["web"]

    # the same when the chat itself starts the conversation, and then Clara has the QCM
    llm.seen.clear()
    llm.replies = ["ok"]
    events = [event async for event in s.agent.turn(ChatRequest(message="teach me", client="web", project=project["id"]))]
    assert "qcm" in {tool["function"]["name"] for tool in llm.seen[0]["tools"]}
    assert "qcm" in s.conversations.get(events[-1]["conversation"]).packs

    # changing the project's tools changes the next conversations only
    changed = (await client.patch(f"/v1/projects/{project['id']}", json={"packs": []})).json()
    assert changed["packs"] == [] and changed["name"] == "Lessons"
    assert "qcm" in s.conversations.get(started["id"]).packs
    assert (await client.post("/v1/conversations", json={"project": project["id"]})).json()["packs"] == []


async def test_a_project_refuses_tools_that_do_not_exist_or_are_not_for_a_chat(client):
    assert (await client.post("/v1/projects", json={"name": "A", "packs": ["bogus"]})).status_code == 422
    assert (await client.post("/v1/projects", json={"name": "A", "packs": ["ask_person"]})).status_code == 422  # schedules only
    project = (await client.post("/v1/projects", json={"name": "B"})).json()
    assert (await client.patch(f"/v1/projects/{project['id']}", json={"packs": ["discord_thread"]})).status_code == 422
    assert (await client.get(f"/v1/projects/{project['id']}")).json()["packs"] == ["web"]
