"""Moodle and Auriga (EPITA): the sign-in, the clients against stand-ins of the sites (schoolworld.py), the five tools of the
Moodle/Auriga pack, what the gate does with them, and what a lapsed sign-in does."""

from __future__ import annotations

import asyncio
import importlib
import io
from base64 import b64encode
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import parse_qs, quote, urlsplit
from zoneinfo import ZoneInfo

import httpx
import pytest

from clara.agent.turn import ChatRequest
from clara.app import create_app
from clara.cli import ops
from clara.connectors.auriga import Auriga, AurigaAuth, AurigaError, code_from_address
from clara.connectors.moodle import READ_FUNCTIONS, Moodle, MoodleError, token_from_link
from clara.connectors.school import school_year
from clara.db.connection import connect
from clara.settings import load_settings
from clara.tools.base import ToolContext, TurnState
from fakes import calls
from schoolworld import TOKEN, World, connect_school, intervention, link, make_pdf, stamp

PARIS = ZoneInfo("Europe/Paris")


@pytest.fixture(autouse=True)
def school_level(services):
    """Reading is allowed from level 2 and a document is written from level 3: a chat here is at level 4, a run at 3."""
    services.runtime.set("auto_level_default", 4)
    services.runtime.set("auto_level_unattended", 3)


@pytest.fixture
def school(monkeypatch):
    """Moodle, Auriga and Keycloak as stand-ins: every client the server makes talks to them."""
    found = World()
    transport = found.transport()
    for cls in (Moodle, Auriga, AurigaAuth):
        real = cls.__init__

        def init(self, *args, _real=real, **kwargs):
            kwargs["transport"] = transport
            _real(self, *args, **kwargs)

        monkeypatch.setattr(cls, "__init__", init)
    return found


@pytest.fixture
def connected(services, school):
    connect_school(services, school)
    return school


async def run_tool(services, name: str, arguments: dict | None = None, *, mode: str = "interactive", local_only: bool = False,
                   context: ToolContext | None = None):
    context = context or ToolContext(services, TurnState("t1", "", "web", mode=mode, timezone="Europe/Paris", local_only=local_only))
    return await services.toolbox.call(name, context, arguments or {}, services.toolbox.names)


def local(day: date, at: time) -> datetime:
    return datetime.combine(day, at, PARIS)


def utc_text(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------
# What the owner pastes
# ----------------------------------------------------------------------
def test_the_token_is_read_out_of_the_pasted_link_and_nothing_else_is_kept():
    assert token_from_link(link()) == TOKEN
    assert token_from_link(f"  {link(private='PRIVATEKEY0123456789abcdef')}\n") == TOKEN  # the private key is thrown away
    payload = link().removeprefix("moodlemobile://token=")
    assert token_from_link(payload) == token_from_link("moodlemobile://token=" + quote(payload)) == TOKEN
    assert token_from_link(TOKEN) == TOKEN
    for bad in ("", "hello", "moodlemobile://token=!!!", "moodlemobile://token=" + b64encode(b"no-separator").decode(),
                "moodlemobile://token=" + b64encode(b"site:::not a token!").decode()):
        with pytest.raises(MoodleError, match="not the link of Moodle's mobile sign-in"):
            token_from_link(bad)


def test_the_code_comes_out_of_the_address_of_the_page_the_owner_lands_on():
    address = "https://auriga.epita.fr/clara-callback?state=abc&session_state=s&code=the-code"
    assert code_from_address(f"  {address} ", "abc") == "the-code"
    assert code_from_address("https://auriga.epita.fr/clara-callback#state=abc&code=in-fragment", "abc") == "in-fragment"
    for bad, why in ((address, "another sign-in"), ("https://evil.example/clara-callback?state=abc&code=x", "whole address"),
                     ("not an address", "whole address"), ("https://auriga.epita.fr/clara-callback?state=abc", "no code"),
                     ("https://auriga.epita.fr/x?state=abc&error=access_denied&error_description=No", "No")):
        with pytest.raises(AurigaError, match=why):
            code_from_address(bad, "other" if bad == address else "abc")


def test_a_school_year_runs_from_august_to_july():
    assert school_year(date(2026, 11, 2)) == "2627" and school_year(date(2027, 7, 31)) == "2627"
    assert school_year(date(2027, 8, 1)) == "2728" and school_year(date(2026, 1, 15)) == "2526"


# ----------------------------------------------------------------------
# The Moodle client
# ----------------------------------------------------------------------
async def test_only_the_read_functions_of_the_list_are_ever_called(school):
    moodle = Moodle(TOKEN)
    for write in ("mod_assign_save_submission", "core_message_send_instant_messages", "core_user_update_users", "anything"):
        with pytest.raises(MoodleError, match="not one of the read functions"):
            await moodle.call(write)
    assert school.moodle_calls == []  # nothing was even sent
    assert len(READ_FUNCTIONS) == 7 and not [name for name in READ_FUNCTIONS if "get" not in name]


async def test_the_token_goes_to_moodle_only_and_a_redirect_is_not_followed(school):
    moodle = Moodle(TOKEN)
    for url in ("https://evil.example/webservice/pluginfile.php/1/x", "https://moodle.epita.fr.evil.example/webservice/pluginfile.php/1/x",
                "http://moodle.epita.fr/webservice/pluginfile.php/1/x", "https://moodle.epita.fr/user/profile.php?id=7"):
        with pytest.raises(MoodleError, match="not on Moodle's own address"):
            await moodle.download(url)
    school.files["1/a.pdf"] = b"data"
    school.file_redirect = "https://evil.example/steal"
    with pytest.raises(MoodleError, match="HTTP 302"):
        await moodle.download("https://moodle.epita.fr/webservice/pluginfile.php/1/a.pdf?forcedownload=1")
    assert set(school.moodle_hosts) == {"moodle.epita.fr"}


async def test_the_private_access_key_of_the_site_info_is_never_kept(school):
    moodle = Moodle(TOKEN)
    assert await moodle.whoami() == (7, "Ada Student")
    assert "PRIVATE-KEY" not in repr(vars(moodle))


async def test_a_file_bigger_than_the_listing_said_is_stopped_while_it_comes(school):
    school.files["1/big.bin"] = b"x" * 5000
    with pytest.raises(MoodleError, match="bigger than"):
        await Moodle(TOKEN).download("https://moodle.epita.fr/webservice/pluginfile.php/1/big.bin", limit=1000)
    assert await Moodle(TOKEN).download("https://moodle.epita.fr/webservice/pluginfile.php/1/big.bin", limit=6000) == b"x" * 5000


# ----------------------------------------------------------------------
# The Auriga client: a 60-second access token, renewed from the refresh token
# ----------------------------------------------------------------------
async def test_one_access_token_serves_many_calls_and_a_lapsed_one_is_renewed_once(school):
    kept: list[str] = []
    auriga = Auriga(school.refresh, on_refresh=kept.append)
    await asyncio.gather(auriga.me(), auriga.me(), auriga.me())
    assert len(school.keycloak_calls) == 1  # three calls at once, one renewal
    await auriga.me()
    assert len(school.keycloak_calls) == 1
    auriga._expires = 0
    await auriga.me()
    assert len(school.keycloak_calls) == 2 and kept == []  # no new refresh token: nothing to keep
    school.api_401 = 1  # the access token ended early: a fresh one is asked for, once
    await auriga.me()
    assert len(school.keycloak_calls) == 3


async def test_a_new_refresh_token_is_kept_and_used_next(school):
    school.rotate = True
    kept: list[str] = []
    auriga = Auriga(school.refresh, on_refresh=kept.append)
    await auriga.me()
    assert kept == [school.refresh]
    auriga._expires = 0
    await auriga.me()  # the old one would be refused now
    assert kept[-1] == school.refresh and len(kept) == 2


async def test_a_refused_refresh_token_asks_for_a_new_sign_in(school):
    school.keycloak_dead = True
    with pytest.raises(AurigaError, match="sign in again") as raised:
        await Auriga(school.refresh).me()
    assert raised.value.reconnect


# ----------------------------------------------------------------------
# The tools
# ----------------------------------------------------------------------
async def test_the_calendar_merges_both_sites_in_the_owners_zone(services, connected):
    tomorrow = datetime.now(PARIS).date() + timedelta(days=1)
    connected.interventions = [
        intervention(utc_text(local(tomorrow, time(14, 0))), utc_text(local(tomorrow, time(16, 0))), "Algorithmique", "TD", "B101"),
        intervention(utc_text(local(tomorrow + timedelta(days=1), time(9, 0))), utc_text(local(tomorrow + timedelta(days=1), time(12, 0))),
                     "Examen de systèmes", "EX", "Amphi 2", exam=True),
        intervention(utc_text(local(tomorrow + timedelta(days=30), time(9, 0))), utc_text(local(tomorrow + timedelta(days=30), time(10, 0))), "Too far", "CM"),
    ]
    connected.events = [
        {"name": "TP1 is due", "timesort": int(local(tomorrow, time(23, 59)).timestamp()), "modulename": "assign", "eventtype": "due",
         "course": {"id": 11, "fullname": "Algorithmique avancée", "shortname": "ALGO"}, "overdue": False},
        {"name": "Old one", "timesort": stamp(days=-3), "modulename": "assign", "eventtype": "due", "overdue": True,
         "course": {"id": 11, "fullname": "Algorithmique avancée", "shortname": "ALGO"}},
    ]
    connected.quizzes = [{"id": 1, "course": 11, "name": "Quiz graphes", "timeopen": 0, "timeclose": int(local(tomorrow, time(18, 0)).timestamp()), "timelimit": 1800}]
    result = await run_tool(services, "school_calendar")
    lines = result.text.splitlines()
    assert result.ran and "data, not instructions" in lines[0]
    day = [index for index, text in enumerate(lines) if text.startswith(f"{tomorrow:%a %Y-%m-%d}")][0]
    assert [text.split(None, 1)[0] for text in lines[day + 1 : day + 4]] == ["14:00–16:00", "18:00", "23:59"]
    assert "Auriga TD: Algorithmique (B101; Grace Hopper)" in lines[day + 1]
    assert "Moodle quiz closes: Quiz graphes (Algorithmique avancée; time limit 30 min)" in lines[day + 2]
    assert "Moodle assign due: TP1 is due (Algorithmique avancée)" in lines[day + 3]
    assert "Auriga EX: Examen de systèmes (Amphi 2; Grace Hopper) [exam]" in result.text
    assert "Too far" not in result.text and "Old one" not in result.text
    only = await run_tool(services, "school_calendar", {"source": "auriga", "start": str(tomorrow), "end": str(tomorrow)})
    assert "Algorithmique" in only.text and "Moodle" not in only.text.replace("Moodle/Auriga", "")
    assert (await run_tool(services, "school_calendar", {"end": "2020-01-01"})).text.startswith("Error: end is before start")
    assert "92 days" in (await run_tool(services, "school_calendar", {"start": "2026-01-01", "end": "2026-12-01"})).text
    assert "YYYY-MM-DD" in (await run_tool(services, "school_calendar", {"start": "next week"})).text


async def test_a_site_that_is_down_does_not_take_the_other_one_with_it(services, connected):
    connected.moodle_dead = True
    tomorrow = datetime.now(PARIS).date() + timedelta(days=1)
    connected.interventions = [intervention(utc_text(local(tomorrow, time(14, 0))), utc_text(local(tomorrow, time(16, 0))), "Algorithmique")]
    result = await run_tool(services, "school_calendar")
    assert "Algorithmique" in result.text and "Note — Moodle: Moodle no longer accepts the token" in result.text
    assert services.connections.get("moodle")["status"] == "needs_reconnect" and "moodle" in services.alerts.active
    calls_before = len(connected.moodle_calls)
    again = await run_tool(services, "school_calendar")  # a lapsed site is not asked again
    assert "Moodle's sign-in lapsed" in again.text and len(connected.moodle_calls) == calls_before
    assert services.connectors.school_unusable() == ""  # Auriga still works: the pack stays usable
    connected.keycloak_dead = True
    services.connectors.auriga_client()._expires = 0  # its access token ended: it is renewed, and Keycloak refuses
    result = await run_tool(services, "school_calendar")
    assert result.text.startswith("Error:") and "Moodle's sign-in lapsed" in result.text and "no longer accepts the sign-in" in result.text
    assert services.connectors.school_unusable() == "Moodle and Auriga must be signed in again (the Connections page)."


async def test_auriga_grades_of_a_year_are_read_through_the_saved_query_of_the_page(services, connected):
    wanted = [[n, f"{10 + n % 9}.5", f"2627_I_INF_{n:03d}", {"fr": f"Matière {n}", "en": f"Course {n}"}, "Moy. (finale)"] for n in range(1, 131)]
    older = [[900 + n, "12.0", f"2526_I_INF_{n:03d}", {"fr": "Ancienne"}, "Moy. (finale)"] for n in range(20)]
    connected.synthese = [*older, *wanted]
    result = await run_tool(services, "school_grades", {"year": "2627"})
    assert result.ran and result.text.count("2627_I_INF_") == 130 and "2526" not in result.text
    assert "2627_I_INF_001  Matière 1  11.5" in result.text
    assert len(connected.posted) == 2  # 150 lines: two pages of 100
    assert connected.posted[0]["queryId"] == 1198 and connected.posted[0]["searchResultGlobal"]["filters"]["conditions"][0]["value"] == ["2627"]
    assert "GET /api/menuEntries/1144/query/1198" in connected.api_calls
    connected.evals = [[1, "14", 2, "2627_I_INF_001", "EXA"], [2, "AB", 1, "2627_I_INF_002", "EXA"], [3, "", 1, "2526_I_INF_001", "EXA"]]
    marks = await run_tool(services, "school_grades", {"kind": "eval", "year": "2627"})
    assert "2627_I_INF_001  14  coef 2  [EXA]" in marks.text and "2627_I_INF_002  AB" in marks.text and "2526" not in marks.text
    assert "GET /api/menuEntries/1036/query/945" in connected.api_calls
    assert "no line" in (await run_tool(services, "school_grades", {"year": "2701"})).text.lower()
    assert "2627 for 2026-2027" in (await run_tool(services, "school_grades", {"year": "26"})).text
    assert "synthese" in (await run_tool(services, "school_grades", {"kind": "nope"})).text


async def test_moodle_grades_are_per_course_found_by_a_part_of_its_name(services, connected):
    connected.grade_items[11] = [
        {"itemname": None, "itemtype": "course", "gradeformatted": "13.2", "rangeformatted": "0–20", "percentageformatted": "66 %"},
        {"itemname": "Quiz graphes", "itemtype": "mod", "itemmodule": "quiz", "gradeformatted": "14.00", "rangeformatted": "0–20",
         "percentageformatted": "70 %", "feedback": "<p>Bien <b>joué</b></p>", "gradedategraded": stamp(days=-2)},
        {"itemname": "TP final", "itemtype": "manual", "gradeformatted": "-", "rangeformatted": "0–20"},
    ]
    result = await run_tool(services, "school_grades", {"course": "algorithmique"})
    assert "Moodle grades of [11] Algorithmique avancée" in result.text
    assert "Course total: 13.2 / 0–20 (66 %)" in result.text
    assert "Quiz graphes (quiz): 14.00 / 0–20 (70 %)" in result.text and "feedback: Bien joué" in result.text
    assert "gradereport_user_get_grade_items" in connected.moodle_calls
    assert "PRIVATE-KEY" not in result.text
    by_id = await run_tool(services, "school_grades", {"source": "moodle", "course": "11"})
    assert "Moodle grades of [11]" in by_id.text
    assert "fits several courses" in (await run_tool(services, "school_grades", {"course": "alg"})).text
    assert "No course of yours" in (await run_tool(services, "school_grades", {"course": "chimie"})).text
    assert "No course 99" in (await run_tool(services, "school_grades", {"course": "99"})).text
    assert "Give the course" in (await run_tool(services, "school_grades", {"source": "moodle"})).text
    assert "auriga or moodle" in (await run_tool(services, "school_grades", {"source": "both"})).text


async def test_courses_are_listed_by_classification(services, connected):
    now = (await run_tool(services, "moodle_courses")).text
    assert "[11] Algorithmique avancée (ALGO)" in now and "40% done" in now and "[13]" not in now
    assert "[13] Algèbre (ALG1)" in (await run_tool(services, "moodle_courses", {"classification": "past"})).text
    assert "[13]" in (await run_tool(services, "moodle_courses", {"classification": "all"})).text
    assert (await run_tool(services, "moodle_courses", {"classification": "future"})).text == "No future course."
    assert "inprogress, past, future or all" in (await run_tool(services, "moodle_courses", {"classification": "x"})).text


def contents_of(connected) -> None:
    base = "https://moodle.epita.fr/webservice/pluginfile.php"
    connected.contents[11] = [
        {"id": 1, "name": "Général", "section": 0, "modules": [
            {"id": 500, "modname": "forum", "name": "Annonces", "uservisible": True, "contents": []},
            {"id": 501, "modname": "resource", "name": "Cours 1", "uservisible": True, "contents": [
                {"type": "file", "filename": "cours1.pdf", "filesize": 2048, "timemodified": stamp(days=-5),
                 "fileurl": f"{base}/1/mod_resource/content/1/cours1.pdf?forcedownload=1"},
                {"type": "file", "filename": "notes.txt", "filesize": 12, "timemodified": 0,
                 "fileurl": f"{base}/2/mod_resource/content/1/notes.txt?forcedownload=1"}]},
            {"id": 502, "modname": "resource", "name": "Caché", "uservisible": False, "contents": [
                {"type": "file", "filename": "secret.pdf", "filesize": 1, "fileurl": f"{base}/3/secret.pdf"}]}]},
        {"id": 2, "name": "Semaine 2", "section": 1, "modules": [
            {"id": 510, "modname": "url", "name": "Documentation", "uservisible": True, "contents": [
                {"type": "url", "filename": "Docs", "filesize": 0, "fileurl": "https://docs.example/algo"}]},
            {"id": 511, "modname": "page", "name": "Résumé", "uservisible": True, "contents": []},
            {"id": 512, "modname": "quiz", "name": "Quiz 1", "uservisible": True},
            {"id": 513, "modname": "quiz", "name": "Quiz 2", "uservisible": True},
            {"id": 514, "modname": "resource", "name": "Scan", "uservisible": True, "contents": [
                {"type": "file", "filename": "scan.pdf", "filesize": 10**9, "fileurl": f"{base}/4/scan.pdf"}]}]},
    ]
    connected.files["1/mod_resource/content/1/cours1.pdf"] = make_pdf("Cours de graphes")
    connected.files["2/mod_resource/content/1/notes.txt"] = b"plain notes\n"
    connected.files["4/scan.pdf"] = b"x"


async def test_the_files_of_a_course_are_listed_with_the_id_that_saves_them(services, connected):
    contents_of(connected)
    result = await run_tool(services, "moodle_files", {"course": "ALGO"})
    text = result.text
    assert "Course [11] Algorithmique avancée (ALGO)" in text
    assert "[501] resource: Cours 1" in text and "501.1  cours1.pdf  2.0 KB" in text and "501.2  notes.txt  12 B" in text
    assert "link: https://docs.example/algo" in text and "[511] page: Résumé (its text is not available here)" in text
    assert "Other activities: forum ×1" in text and "quiz ×2" in text
    assert "Caché" not in text and "secret" not in text  # not visible to the owner
    assert "token" not in text.lower().replace("moodle_file_save", "")  # no address with the token
    only = (await run_tool(services, "moodle_files", {"course": "ALGO", "section": "semaine"})).text
    assert "Semaine 2" in only and "Général" not in only
    assert "Nothing to list" in (await run_tool(services, "moodle_files", {"course": "ALGO", "section": "zzz"})).text


async def test_a_pdf_is_saved_with_a_text_copy_and_the_turn_stays_untainted(services, connected):
    contents_of(connected)
    context = ToolContext(services, TurnState("t1", "", "web", timezone="Europe/Paris"))
    saved = await run_tool(services, "moodle_file_save", {"course": "ALGO", "file": "501.1", "path": "moodle/algo/cours1.pdf"}, context=context)
    assert saved.ran and "Saved cours1.pdf" in saved.text and "moodle/algo/cours1.pdf.txt" in saved.text and "ws_read" in saved.text
    assert services.workspace.read_bytes("moodle/algo/cours1.pdf") == make_pdf("Cours de graphes")
    assert "Cours de graphes" in services.workspace.read_text("moodle/algo/cours1.pdf.txt")
    assert not context.turn.tainted  # the owner's choice: reading these sites does not taint
    read = await run_tool(services, "ws_read", {"path": "moodle/algo/cours1.pdf.txt"}, context=context)
    assert "Cours de graphes" in read.text and not context.turn.tainted
    assert set(connected.moodle_hosts) == {"moodle.epita.fr"}
    again = await run_tool(services, "moodle_file_save", {"course": "ALGO", "file": "501.1", "path": "moodle/algo/cours1.pdf"})
    assert "exists already" in again.text  # never over an existing file


async def test_other_documents_are_saved_as_they_are_and_a_scan_has_no_text_copy(services, connected):
    contents_of(connected)
    text = await run_tool(services, "moodle_file_save", {"course": "ALGO", "file": "501.2", "path": "moodle/algo/notes.txt"})
    assert "Saved notes.txt" in text.text and ".txt." not in text.text and "text copy" not in text.text
    assert services.workspace.read_text("moodle/algo/notes.txt") == "plain notes\n"
    assert not services.workspace.exists("moodle/algo/notes.txt.txt")
    connected.contents[11][0]["modules"][1]["contents"][0]["filesize"] = 99
    connected.files["1/mod_resource/content/1/cours1.pdf"] = make_pdf("")
    scan = await run_tool(services, "moodle_file_save", {"course": "ALGO", "file": "501.1", "path": "moodle/algo/scan.pdf"})
    assert "Saved cours1.pdf" in scan.text and "no text copy" in scan.text.lower() and "scanned PDF" in scan.text
    assert services.workspace.exists("moodle/algo/scan.pdf") and not services.workspace.exists("moodle/algo/scan.pdf.txt")


async def test_a_document_that_cannot_be_saved_is_refused_before_it_is_downloaded(services, connected):
    contents_of(connected)
    before = len(connected.moodle_hosts)
    for arguments, why in (({"file": "514.1"}, "can be saved"), ({"file": "510.1"}, "is a link"), ({"file": "999.1"}, "No file 999.1"),
                           ({"file": "502.1"}, "No file 502.1"), ({"file": "x"}, "two numbers")):
        result = await run_tool(services, "moodle_file_save", {"course": "ALGO", "path": "moodle/x.pdf", **arguments})
        assert result.text.startswith("Error:") and why in result.text, result.text
    assert not [host for host in connected.moodle_hosts[before:] if host != "moodle.epita.fr"]
    assert not services.workspace.exists("moodle/x.pdf")


async def test_a_token_refused_while_downloading_marks_the_sign_in_as_lapsed(services, connected):
    contents_of(connected)
    connected.files_dead = True
    result = await run_tool(services, "moodle_file_save", {"course": "ALGO", "file": "501.1", "path": "moodle/algo/a.pdf"})
    assert "no longer accepts the token" in result.text
    assert services.connections.get("moodle")["status"] == "needs_reconnect"


# ----------------------------------------------------------------------
# The gate
# ----------------------------------------------------------------------
async def test_the_calls_are_described_before_they_run(services, connected):
    context = ToolContext(services, TurnState("t1", "", "web", timezone="Europe/Paris"))
    both = services.toolbox.action("school_calendar", context, {})
    assert (both.klass, both.level, both.targets) == ("school_read", "read", ("school:moodle", "school:auriga"))
    assert services.toolbox.action("school_calendar", context, {"source": "moodle"}).targets == ("school:moodle",)
    assert services.toolbox.action("school_grades", context, {}).targets == ("school:auriga",)
    assert services.toolbox.action("school_grades", context, {"course": "x"}).targets == ("school:moodle",)
    pdf = services.toolbox.action("moodle_file_save", context, {"path": "moodle/a.pdf"})
    assert (pdf.klass, pdf.level, pdf.targets) == ("school_save", "write", ("school:moodle", "ws:moodle/a.pdf", "ws:moodle/a.pdf.txt"))
    assert services.toolbox.action("moodle_file_save", context, {"path": "moodle/a.txt"}).targets == ("school:moodle", "ws:moodle/a.txt")


async def test_reading_is_allowed_at_level_2_and_writing_a_document_from_level_3(services, connected):
    contents_of(connected)
    services.runtime.set("auto_level_default", 2)
    assert (await run_tool(services, "moodle_courses")).ran
    asked = await run_tool(services, "moodle_file_save", {"course": "ALGO", "file": "501.2", "path": "moodle/algo/n.txt"})
    assert asked.approval is not None and not asked.ran and not services.workspace.exists("moodle/algo/n.txt")
    services.runtime.set("auto_level_default", 3)
    assert (await run_tool(services, "moodle_file_save", {"course": "ALGO", "file": "501.2", "path": "moodle/algo/n.txt"})).ran
    services.runtime.set("auto_level_default", 1)
    assert (await run_tool(services, "moodle_courses")).text.startswith("Refused")


async def test_a_rule_can_close_one_site_or_one_folder(services, connected):
    contents_of(connected)
    services.rules.add("refuse", tool="moodle_*", target="school:moodle")
    assert (await run_tool(services, "moodle_courses")).text.startswith("Refused")
    assert (await run_tool(services, "school_grades", {})).ran  # Auriga is another target
    services.rules.add("refuse", tool="moodle_file_save", target="ws:moodle/private/**")
    refused = await run_tool(services, "moodle_file_save", {"course": "ALGO", "file": "501.2", "path": "moodle/private/n.txt"})
    assert refused.text.startswith("Refused") and not services.workspace.exists("moodle/private/n.txt")


async def test_a_local_only_conversation_never_reaches_the_sites(services, connected):
    for name, arguments in (("moodle_courses", {}), ("school_calendar", {}), ("school_grades", {})):
        assert (await run_tool(services, name, arguments, local_only=True)).text.startswith("Refused"), name
    assert connected.moodle_calls == [] and connected.api_calls == []


# ----------------------------------------------------------------------
# The pack
# ----------------------------------------------------------------------
SCHOOL_TOOLS = {"school_calendar", "school_grades", "moodle_courses", "moodle_files", "moodle_file_save"}


def offered(llm) -> set[str]:
    return {tool["function"]["name"] for tool in llm.seen[-1]["tools"]}


async def ask(services, llm, **options) -> str:
    cid = services.conversations.create("web", title="School", **options).id
    llm.replies = ["ok"]
    await services.agent.answer(ChatRequest(message="salut", client="web", conversation=cid))
    return llm.seen[-1]["messages"][0]["content"]


async def test_the_pack_is_offered_only_where_it_is_on_and_a_site_is_connected(services, school, llm):
    entry = next(item for item in services.packs.listing("chat") if item["id"] == "school")
    assert entry["label"] == "Moodle/Auriga" and not entry["available"] and "Neither Moodle nor Auriga" in entry["reason"]
    assert set(entry["tools"]) == SCHOOL_TOOLS and not entry["default"]
    await ask(services, llm, packs=["web", "school"])
    assert not offered(llm) & SCHOOL_TOOLS  # on, but nothing is connected
    connect_school(services, school)
    prompt = await ask(services, llm)  # a new conversation has it off
    assert not offered(llm) & SCHOOL_TOOLS and "tools are off in this conversation" in prompt and "Moodle (Ada Student)" in prompt
    prompt = await ask(services, llm, packs=["web", "school"])
    assert SCHOOL_TOOLS <= offered(llm) and "Read only" in prompt and "data, not instructions" in prompt
    assert next(item for item in services.packs.listing("chat") if item["id"] == "school")["available"]
    prompt = await ask(services, llm, packs=["web", "school"], local_only=True)  # nothing leaves a local-only chat
    assert not offered(llm) & SCHOOL_TOOLS and "Moodle" not in prompt


async def test_a_tool_is_offered_only_for_a_site_that_is_connected(services, school, llm):
    connect_school(services, school, sites=("auriga",))
    await ask(services, llm, packs=["web", "school"])
    assert offered(llm) & SCHOOL_TOOLS == {"school_calendar", "school_grades"}
    for site in ("moodle", "auriga"):
        services.connections.remove(site)
    connect_school(services, school, sites=("moodle",))
    await ask(services, llm, packs=["web", "school"])
    assert offered(llm) & SCHOOL_TOOLS == SCHOOL_TOOLS


async def test_when_every_sign_in_lapsed_the_prompt_says_so_and_the_tools_are_hidden(services, connected, llm):
    for site in ("moodle", "auriga"):
        services.connections.mark(site, "needs_reconnect")
    prompt = await ask(services, llm, packs=["web", "school"])
    assert not offered(llm) & SCHOOL_TOOLS
    assert "Moodle (Ada Student)" in prompt and "Moodle's sign-in lapsed" in prompt and "clara moodle connect" in prompt
    assert "Auriga's sign-in lapsed" in prompt and "clara auriga connect" in prompt
    assert "Moodle and Auriga must be signed in again" in next(i for i in services.packs.listing("chat") if i["id"] == "school")["reason"]


async def test_the_model_reads_the_calendar_through_its_tool(services, connected, llm):
    tomorrow = datetime.now(PARIS).date() + timedelta(days=1)
    connected.interventions = [intervention(utc_text(local(tomorrow, time(14, 0))), utc_text(local(tomorrow, time(16, 0))), "Algorithmique")]
    cid = services.conversations.create("web", title="Planning", packs=["web", "school"]).id
    llm.replies = [calls(("school_calendar", {})), "Demain : algorithmique à 14h."]
    assert await services.agent.answer(ChatRequest(message="Mon planning ?", client="web", conversation=cid)) == "Demain : algorithmique à 14h."
    assert any("Algorithmique" in m["content"] for m in llm.seen[-1]["messages"] if m["role"] == "tool")
    assert not services.conversations.is_tainted(cid)
    llm.replies = [calls(("school_calendar", {})), "Rien d'autre."]  # and the next turn is not tainted either
    await services.agent.answer(ChatRequest(message="Et ensuite ?", client="web", conversation=cid))
    assert {row["situation"] for row in services.audit.recent() if row["tool"] == "school_calendar"} == {"interactive"}


def test_a_hook_gets_the_pack_from_the_tools_its_allow_list_names(services):
    assert "school" in services.packs.starting_with(["school_calendar"])
    assert "school" in services.packs.starting_with(["moodle_*"])
    assert "school" not in services.packs.starting_with(["mail_search"])


# ----------------------------------------------------------------------
# A lapse, and coming back
# ----------------------------------------------------------------------
async def test_a_site_that_works_again_clears_its_alert(services, connected):
    connected.moodle_dead = True
    await run_tool(services, "moodle_courses")
    assert "moodle" in services.alerts.active and services.connections.get("moodle")["status"] == "needs_reconnect"
    connected.moodle_dead = False
    services.connections.mark("moodle", "ok")  # the sign-in was redone from the terminal
    assert (await run_tool(services, "moodle_courses")).ran and "moodle" not in services.alerts.active


async def test_replacing_the_secret_from_the_console_clears_the_alert_and_the_mark(services, connected):
    from clara import console

    connected.moodle_dead = True
    await run_tool(services, "moodle_courses")
    assert "moodle" in services.alerts.active
    services.secrets.set("moodle_token", "b" * 32)
    console.secret_changed(services, "moodle_token")
    assert "moodle" not in services.alerts.active and services.connections.get("moodle")["status"] == "ok"


async def test_a_new_refresh_token_from_keycloak_replaces_the_stored_one(services, connected):
    connected.rotate = True
    assert (await run_tool(services, "school_grades", {})).ran
    assert services.secrets.get("auriga_refresh_token") == connected.refresh
    services.connectors.auriga_client()._expires = 0
    assert (await run_tool(services, "school_grades", {})).ran
    assert services.secrets.get("auriga_refresh_token") == connected.refresh


async def test_a_refused_refresh_token_raises_the_alert_once_and_marks_the_connection(services, connected):
    connected.keycloak_dead = True
    result = await run_tool(services, "school_grades", {})
    assert "sign in again" in result.text and services.connections.get("auriga")["status"] == "needs_reconnect"
    assert "auriga" in services.alerts.active and "refused the sign-in" in services.alerts.active["auriga"]
    seen = len(connected.keycloak_calls)
    assert "lapsed" in (await run_tool(services, "school_grades", {})).text
    assert len(connected.keycloak_calls) == seen  # not tried again until the person signs in


# ----------------------------------------------------------------------
# The Connections page and its routes
# ----------------------------------------------------------------------
@pytest.fixture
async def browser(services, token):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(services)), base_url="https://clara.test") as http:
        signed = await http.post("/v1/auth/web-login", json={"token": token}, headers={"X-Clara-Web": "1"})
        assert signed.status_code == 200
        http.headers["X-Clara-Web"] = "1"
        yield http


async def test_moodle_is_connected_by_pasting_the_link_and_only_if_moodle_accepts_it(services, browser, school):
    start = (await browser.post("/v1/connections/moodle/start")).json()["url"]
    parts = urlsplit(start)
    assert parts.netloc == "moodle.epita.fr" and parts.path == "/admin/tool/mobile/launch.php"
    assert parse_qs(parts.query)["service"] == ["moodle_mobile_app"] and parse_qs(parts.query)["urlscheme"] == ["moodlemobile"]
    assert (await browser.put("/v1/connections/moodle", json={"link": "this is not a link"})).status_code == 422
    school.moodle_dead = True
    refused = await browser.put("/v1/connections/moodle", json={"link": link()})
    assert refused.status_code == 422 and "no longer accepts" in refused.json()["error"]["message"]
    assert not services.secrets.has("moodle_token") and not services.connections.get("moodle")
    school.moodle_dead = False
    assert (await browser.put("/v1/connections/moodle", json={"link": link(private="PRIVATEKEY0123456789abcdef")})).json() == {"account": "Ada Student"}
    assert services.secrets.get("moodle_token") == TOKEN
    status = (await browser.get("/v1/connections")).json()["moodle"]
    assert status == {"connected": True, "account": "Ada Student", "status": "ok"}
    assert (await browser.delete("/v1/connections/moodle")).json() == {"connected": False}
    assert not services.secrets.has("moodle_token") and not services.connections.get("moodle")


async def test_auriga_is_connected_by_pasting_the_address_of_the_page_after_the_sign_in(services, browser, client, school):
    assert (await client.post("/v1/connections/auriga/start")).status_code == 409  # a token, not a browser
    started = (await browser.post("/v1/connections/auriga/start")).json()
    query = parse_qs(urlsplit(started["url"]).query)
    assert urlsplit(started["url"]).netloc == "ionisepita-auth.np-auriga.nfrance.net"
    assert query["client_id"] == ["np-front"] and query["code_challenge_method"] == ["S256"] and query["redirect_uri"] == ["https://auriga.epita.fr/clara-callback"]
    assert "offline_access" in query["scope"][0].split() and query["state"] == [started["state"]]
    address = f"https://auriga.epita.fr/clara-callback?state={started['state']}&session_state=s&code=code-1"
    for bad in (address.replace(started["state"], "x" * 20), "https://evil.example/?code=code-1&state=" + started["state"]):
        assert (await browser.post("/v1/connections/auriga/finish", json={"state": started["state"], "address": bad})).status_code == 422
    assert not services.secrets.has("auriga_refresh_token")
    done = await browser.post("/v1/connections/auriga/finish", json={"state": started["state"], "address": address})
    assert done.json() == {"account": "ada@epita.fr", "lasting": True}
    assert services.secrets.get("auriga_refresh_token") == school.refresh and services.connections.get("auriga")["status"] == "ok"
    assert school.keycloak_calls[0]["code_verifier"] and school.keycloak_calls[0]["redirect_uri"] == "https://auriga.epita.fr/clara-callback"
    again = await browser.post("/v1/connections/auriga/finish", json={"state": started["state"], "address": address})
    assert again.status_code == 409  # the sign-in is finished: it cannot be used twice
    assert (await browser.get("/v1/connections")).json()["auriga"] == {"connected": True, "account": "ada@epita.fr", "status": "ok"}
    assert (await browser.delete("/v1/connections/auriga")).json() == {"connected": False}
    assert not services.secrets.has("auriga_refresh_token")


async def test_a_refused_code_or_a_session_only_token_is_told_to_the_owner(services, browser, school):
    started = (await browser.post("/v1/connections/auriga/start")).json()
    school.codes.clear()
    address = f"https://auriga.epita.fr/clara-callback?state={started['state']}&code=code-1"
    refused = await browser.post("/v1/connections/auriga/finish", json={"state": started["state"], "address": address})
    assert refused.status_code == 422 and "refused this sign-in code" in refused.json()["error"]["message"]
    school.codes.add("code-1")
    school.offline = False
    done = await browser.post("/v1/connections/auriga/finish", json={"state": started["state"], "address": address})
    assert done.json()["lasting"] is False


async def test_the_tokens_of_the_sites_cannot_be_typed_into_the_admin_page(client, services):
    for name in ("moodle_token", "auriga_refresh_token"):
        assert (await client.put(f"/v1/admin/secrets/{name}", json={"value": "stolen"})).status_code == 404
        assert not services.secrets.has(name)
    listed = {item["name"]: item for item in services.secrets.status()}
    assert listed["moodle_token"]["group"] == "Moodle and Auriga" and not listed["moodle_token"]["settable"]


# ----------------------------------------------------------------------
# The console and the terminal
# ----------------------------------------------------------------------
async def test_the_school_command_says_who_is_connected_and_forgets_a_token(services, connected):
    from clara import console

    token = services.tokens.create("admin", "cli")[0]
    said = await console.run(services, "/school", token)
    assert "Moodle   connected as Ada Student" in said and "Auriga   connected as ada@epita.fr" in said
    services.connections.mark("auriga", "needs_reconnect")
    assert "must be signed in again" in await console.run(services, "/school status", token)
    assert "disconnected" in await console.run(services, "/school disconnect moodle", token)
    assert not services.secrets.has("moodle_token") and "not connected" in await console.run(services, "/school", token)
    assert "was not connected" in await console.run(services, "/school disconnect moodle", token)
    assert "Usage" in await console.run(services, "/school forget", token)


@pytest.fixture
def terminal(environ, monkeypatch, school):
    """`clara …` against a data folder of its own: the settings are the test's, the answers are the pasted lines."""
    settings = load_settings(environ, use_file=False)
    monkeypatch.setattr(ops, "_settings", lambda: settings)
    monkeypatch.setattr(ops.secrets_module, "token_urlsafe", lambda size=32: "s" * 24)

    def paste(text: str) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(text + "\n"))

    return settings, paste


def stored(settings, name: str) -> str | None:
    db = ops._database(settings)
    try:
        return ops._store(settings, db).get(name)
    finally:
        db.close()


def test_clara_moodle_connect_keeps_the_token_of_the_pasted_link(terminal, capsys):
    settings, paste = terminal
    paste("hello")
    assert ops.main(["moodle", "connect"]) == 1 and "not the link" in capsys.readouterr().err
    assert stored(settings, "moodle_token") is None
    paste(link(private="PRIVATEKEY0123456789abcdef"))
    assert ops.main(["moodle", "connect"]) == 0
    out = capsys.readouterr().out
    assert "launch.php" in out and "connected as Ada Student" in out and TOKEN not in out and "PRIVATEKEY" not in out
    assert stored(settings, "moodle_token") == TOKEN
    assert ops.main(["moodle", "disconnect"]) == 0 and stored(settings, "moodle_token") is None


def test_clara_auriga_connect_keeps_the_refresh_token_and_says_how_long_it_lasts(terminal, school, capsys):
    settings, paste = terminal
    paste("https://auriga.epita.fr/clara-callback?state=" + "s" * 24 + "&code=code-1")
    assert ops.main(["auriga", "connect"]) == 0
    out = capsys.readouterr().out
    assert "connected as ada@epita.fr" in out and "idle for a month" in out and "np-front" in out
    assert stored(settings, "auriga_refresh_token") == school.refresh
    school.offline = False
    school.codes.add("code-2")
    paste("https://auriga.epita.fr/clara-callback?state=" + "s" * 24 + "&code=code-2")
    assert ops.main(["auriga", "connect"]) == 0
    assert "session token only" in capsys.readouterr().out
    paste("https://auriga.epita.fr/clara-callback?state=wrong&code=code-3")
    assert ops.main(["auriga", "connect"]) == 1 and "another sign-in" in capsys.readouterr().err


# ----------------------------------------------------------------------
# The schema
# ----------------------------------------------------------------------
def test_migration_28_keeps_every_repository_and_drive_file_and_allows_the_two_sites(tmp_path):
    migrate_module = importlib.import_module("clara.db.migrate")
    path = tmp_path / "old.db"
    old = connect(path)
    for migration in migrate_module.migrations()[:27]:
        old.executescript(f"BEGIN IMMEDIATE;\n{migration.sql}\nPRAGMA user_version = {migration.version};\nCOMMIT;")
    old.execute("INSERT INTO connections (kind, account, status, created_at, updated_at) VALUES ('github', 'me', 'ok', 't', 't')")
    old.execute("INSERT INTO connections (kind, account, status, created_at, updated_at) VALUES ('google', 'me@g.com', 'needs_reconnect', 't', 't')")
    old.execute("INSERT INTO resources (connection_id, kind, locator, label, branch, mime, created_at) VALUES (1, 'github_repo', 'me/repo', 'me/repo', 'main', '', 't')")
    old.execute("INSERT INTO resources (connection_id, kind, locator, label, branch, mime, created_at) VALUES (2, 'drive_folder', 'abc', 'Clara', '', 'clara', 't')")
    before = [tuple(r) for r in old.execute("SELECT * FROM connections ORDER BY id")], [tuple(r) for r in old.execute("SELECT * FROM resources ORDER BY id")]
    old.close()
    assert migrate_module.migrate(path, tmp_path / "backups")[:2] == (27, migrate_module.LATEST_VERSION)
    new = connect(path)
    assert ([tuple(r) for r in new.execute("SELECT * FROM connections ORDER BY id")],
            [tuple(r) for r in new.execute("SELECT * FROM resources ORDER BY id")]) == before
    assert new.execute("PRAGMA foreign_key_check").fetchall() == []
    for kind in ("moodle", "auriga"):
        new.execute("INSERT INTO connections (kind, account, created_at, updated_at) VALUES (?, 'x', 't', 't')", (kind,))
    with pytest.raises(Exception, match="CHECK"):
        new.execute("INSERT INTO connections (kind, account, created_at, updated_at) VALUES ('yahoo', 'x', 't', 't')")
    new.execute("DELETE FROM connections WHERE kind = 'github'")  # the cascade still reaches its repository, and only that
    assert [r["kind"] for r in new.execute("SELECT kind FROM resources")] == ["drive_folder"]
