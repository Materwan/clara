"""Skills: instructions you add (a SKILL.md or a zip of a folder) and run from a chat command (`/name arguments`). How a skill is read
(the frontmatter, hostile archives), what is stored, who may change it, the scope, the expansion in a chat, the line a summary gets."""

from __future__ import annotations

import base64
import io
import re
import sqlite3
import stat
import struct
import zipfile
from pathlib import Path

import httpx
import pytest

from clara import console
from clara.app import create_app
from clara.db.connection import connect
from clara.db.migrate import _apply, migrations
from clara.packs.registry import PACKS
from clara.skills import MAX_SKILLS, RESERVED, SkillError, read_skill
from clara.skills.parse import MAX_FILES, MAX_SKILL_BYTES, MAX_SKILL_MD
from clara.skills.store import MAX_EXPANDED, SUMMARY_LINE, split_summary, substitute, with_skills
from conftest import parse_sse

ROOT = Path(__file__).resolve().parent.parent


def md(name="review-pr", description="Review a pull request", body="Review $ARGUMENTS carefully.", extra=""):
    head = f"name: {name}\n" if name else ""
    return f"---\n{head}description: {description}\n{extra}---\n{body}\n"


def make_zip(entries, *, folder="", infos=()) -> bytes:
    """A zip of these files (name -> text or bytes), and of ready-made entries (ZipInfo, data) for the hostile cases."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(f"{folder}{name}", data)
        for info, data in infos:
            archive.writestr(info, data)
    return buffer.getvalue()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def link(name="SKILL.md", kind=stat.S_IFLNK) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (kind | 0o777) << 16
    return info


def patch_sizes(raw: bytes, name: str, size: int) -> bytes:
    """The archive with the uncompressed size of one entry rewritten in its local and its central header (a lie)."""
    data = bytearray(raw)
    local = data.find(name.encode()) - 30
    struct.pack_into("<I", data, local + 22, size)
    central = data.find(b"PK\x01\x02")
    while central != -1:
        if data[central + 46 : central + 46 + len(name)] == name.encode():
            struct.pack_into("<I", data, central + 24, size)
        central = data.find(b"PK\x01\x02", central + 1)
    return bytes(data)


def asked(llm) -> str:
    """What the model was last given as the question of a turn (the title and summary jobs also call it, with their own text)."""
    return next(call for call in reversed(llm.seen) if call["messages"][-1]["content"].startswith("[time:"))["messages"][-1]["content"]


async def add(client, text=None, **extra):
    return await client.post("/v1/skills", json={"skill_md": text if text is not None else md(), **extra})


# ----------------------------------------------------------------------
# The frontmatter
# ----------------------------------------------------------------------
def test_a_plain_skill_is_read():
    found = read_skill(skill_md=md(extra="argument-hint: <number>\npacks: [mail, github]\ndisable-model-invocation: true\n"))
    assert found.errors == [] and found.name == "review-pr" and found.description == "Review a pull request"
    assert found.argument_hint == "<number>" and found.packs == ["mail", "github"] and found.disable_model_invocation
    assert found.body == "Review $ARGUMENTS carefully."


def test_values_may_be_quoted_and_a_comma_list_works_for_packs():
    found = read_skill(skill_md=md(description='"Say \\"hi\\": a, b # c"', extra="argument-hint: 'it''s'\npacks: mail, github\n"))
    assert found.errors == []
    assert found.description == 'Say "hi": a, b # c' and found.argument_hint == "it's" and found.packs == ["mail", "github"]
    assert read_skill(skill_md=md(description="Use when: reviewing")).description == "Use when: reviewing"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("no frontmatter at all\n", "must start with a line of three dashes"),
        ("---\nname: a\ndescription: d\nbody\n", "not closed"),
        ("---\nname: a\ndescription: d\n  indented: x\n---\nb\n", "Line 4: an indented line"),
        ("---\nname: a\ndescription: d\njust text\n---\nb\n", "Line 4: not a `key: value` line"),
        ("---\nname: a\nname: b\ndescription: d\n---\nb\n", "Line 3: name is given twice"),
        ("---\nname: a\ndescription: |\n  long\n---\nb\n", "Line 3: this kind of YAML value"),
        ("---\nname: a\ndescription: {x: 1}\n---\nb\n", "Line 3: this kind of YAML value"),
        ("---\nname: a\ndescription: d\npacks:\n  - mail\n---\nb\n", "Line 5: an indented line"),
        ("---\nname: a\ndescription: d\npacks: [mail\n---\nb\n", "Line 4: a list starts with"),
        ("---\nname: a\ndescription: d\npacks: [mail,, web]\n---\nb\n", "Line 4: a list item is empty"),
        ("---\nname: a\ndescription: d\nargument-hint: \"open\n---\nb\n", "Line 4: a quote is not closed"),
        ("---\nname: a\ndescription: d\nargument-hint: \"a\" b\n---\nb\n", "Line 4: there is text after the closing quote"),
        ("---\nname: a\ndescription: \"a\\q\"\n---\nb\n", "Line 3: the escape"),
        ("---\nname: a\ndescription: Review PR #12\n---\nb\n", "Line 3: ' #' starts a comment"),
        ("---\nname: a\ndescription: d\n---\n\n", "has no instructions"),
        ("---\nname: a\n---\nb\n", "needs a description"),
        ("---\nname: a\ndescription: d\ndisable-model-invocation: maybe\n---\nb\n", "must be true or false"),
        ("---\nname: a\ndescription: [x, y]\n---\nb\n", "description must be text"),
    ],
)
def test_a_frontmatter_that_is_not_the_subset_is_refused_with_its_line(text, message):
    errors = read_skill(skill_md=text).errors
    assert any(message in error for error in errors), errors


def test_unknown_fields_and_allowed_tools_are_ignored_and_said_so():
    found = read_skill(skill_md=md(extra="allowed-tools: Bash(rm:*)\nmodel: opus\ncontext: fork\n"))
    assert found.errors == [] and found.ignored == ["allowed-tools", "model", "context"]
    assert any("allowed-tools is ignored" in warning and "grants no right" in warning for warning in found.warnings)
    assert any("model, context" in warning for warning in found.warnings)


def test_packs_must_exist_and_belong_to_a_chat():
    assert any("No such tool pack" in e for e in read_skill(skill_md=md(extra="packs: [mail, telepathy]\n")).errors)
    assert any("automation runs only" in e for e in read_skill(skill_md=md(extra="packs: [ask_person]\n")).errors)
    assert read_skill(skill_md=md(extra="packs: []\n")).packs == []


@pytest.mark.parametrize("name", ["Review", "-start", "a b", "a_b", "x" * 41, "é", "../up"])
def test_a_name_must_follow_the_rules(name):
    assert any("is not valid" in error for error in read_skill(skill_md=md(name=f'"{name}"')).errors)


def test_a_name_comes_from_the_frontmatter_else_the_one_given_and_both_are_said_when_they_differ():
    assert read_skill(skill_md=md(name=None), name="weekly").name == "weekly"
    assert any("has no name" in e for e in read_skill(skill_md=md(name=None)).errors)
    found = read_skill(skill_md=md(name="review-pr"), name="other")
    assert found.name == "review-pr" and any("'other' you gave is not used" in w for w in found.warnings)
    assert read_skill(skill_md=md(name="a"), name="a").warnings == []


def test_reserved_names_cannot_be_skills_and_every_client_command_is_reserved():
    for word in ("tools", "new", "model", "help", "clear", "skills"):
        assert any("command of Clara's clients" in e for e in read_skill(skill_md=md(name=word)).errors)
    found: set[str] = set()
    chat = (ROOT / "src/clara/cli/chat.py").read_text(encoding="utf-8")
    for line in chat.splitlines():
        if re.match(r"\s+(el)?if name (==|in) ", line):
            found |= set(re.findall(r'"/([a-z-]+)"', line))
    found |= set(re.findall(r'tree\.command\(name="([a-z-]+)"', (ROOT / "src/clara_discord/bot.py").read_text(encoding="utf-8")))
    found |= {command.name for command in console.COMMANDS}
    assert {"new", "tools", "approvals", "status", "quit", "remind"} <= found, "the scan above found nothing: it is out of date"
    assert not sorted(found - RESERVED), "a client command that is not reserved: add it to RESERVED in skills/parse.py"


def test_a_skill_md_is_text_of_a_bounded_size():
    assert any("not a text file" in e for e in read_skill(skill_md="---\nname: a\n---\nx\x00y").errors)
    assert any(f"at most {MAX_SKILL_MD:,}" in e for e in read_skill(skill_md=md(body="x" * (MAX_SKILL_MD + 1))).errors)
    assert read_skill(skill_md="﻿" + md().replace("\n", "\r\n")).errors == []  # a BOM and Windows line ends


def test_exactly_one_source_is_given():
    assert read_skill().errors and read_skill(skill_md=md(), zip_base64="").errors
    assert any("not base64" in e for e in read_skill(zip_base64="@@@").errors)


# ----------------------------------------------------------------------
# An archive
# ----------------------------------------------------------------------
def test_a_zip_of_a_folder_gives_its_files_and_the_folder_names_the_skill():
    raw = make_zip(
        {"SKILL.md": md(name=None), "notes/ref.md": "# ref", "scripts/run.sh": "#!/bin/sh\necho hi\n", "logo.png": b"\x89PNG\x00\x01"},
        folder="weekly-report/",
    )
    found = read_skill(zip_base64=b64(raw))
    assert found.errors == [] and found.name == "weekly-report"
    assert [(f.path, f.kind) for f in found.files] == [("logo.png", "binary"), ("notes/ref.md", "text"), ("scripts/run.sh", "text")]
    assert "scripts are not run" in found.files[2].note and "never read or run" in found.files[1].note
    top = read_skill(zip_base64=b64(make_zip({"SKILL.md": md(), "a.txt": "x"})))  # SKILL.md at the top of the archive
    assert top.errors == [] and top.name == "review-pr" and [f.path for f in top.files] == ["a.txt"]


def test_junk_of_the_system_is_left_out_and_said():
    found = read_skill(zip_base64=b64(make_zip({"SKILL.md": md(), "__MACOSX/._SKILL.md": "x", ".DS_Store": b"x", "a.txt": "x"})))
    assert found.errors == [] and [f.path for f in found.files] == ["a.txt"] and any("2 files of the system" in w for w in found.warnings)


@pytest.mark.parametrize(
    ("entries", "infos", "message"),
    [
        ({"SKILL.md": md(), "../x": "x"}, (), "may not go up"),
        ({"SKILL.md": md(), "a/../../x": "x"}, (), "may not go up"),
        ({"SKILL.md": md(), "/etc/passwd": "x"}, (), "an absolute path"),
        ({"SKILL.md": md(), "C:/x": "x"}, (), "an absolute path"),
        ({"SKILL.md": md(), "a\\..\\x": "x"}, (), "may not go up"),
        ({"SKILL.md": md()}, ((link("evil"), "/etc/passwd"),), "a symbolic link"),
        ({"SKILL.md": md()}, ((link("pipe", stat.S_IFIFO), ""),), "a special file"),
        ({"SKILL.md": md()}, ((link("dev", stat.S_IFCHR), ""),), "a special file"),
        ({"SKILL.md": md(), "A.txt": "1", "a.TXT": "2"}, (), "same path once normalised"),
        ({"SKILL.md": md(), "a.txt": "1", "./a.txt": "2"}, (), "same path once normalised"),
        ({"SKILL.md": md(), "é.txt": "1", "e\u0301.txt": "2"}, (), "same path once normalised"),
        ({"SKILL.md": md(), "x" * 201: "1"}, (), "longer than 200"),
        ({"notes.txt": "x"}, (), "no SKILL.md"),
        ({"skill.md": md()}, (), "no SKILL.md"),
        ({"a/SKILL.md": md(), "b/x.txt": "x"}, (), "no SKILL.md"),
    ],
)
def test_a_hostile_archive_is_refused(entries, infos, message):
    found = read_skill(zip_base64=b64(make_zip(entries, infos=infos)))
    assert any(message in error for error in found.errors), found.errors
    assert found.files == []


def test_an_encrypted_entry_is_refused():
    raw = bytearray(make_zip({"SKILL.md": md()}))
    central = raw.find(b"PK\x01\x02")
    struct.pack_into("<H", raw, central + 8, 1)  # the general purpose flag: bit 0 is « encrypted »
    assert any("encrypted" in e for e in read_skill(zip_base64=b64(bytes(raw))).errors)


def test_a_bomb_is_stopped_by_counting_the_real_bytes():
    bomb = make_zip({"SKILL.md": md(), "zeros.bin": b"\0" * 5_000_000})  # a few KB that unpack to 5 MB
    assert len(bomb) < 20_000
    assert any("unpack to more than 1,000,000 bytes" in e for e in read_skill(zip_base64=b64(bomb)).errors)
    big_md = make_zip({"SKILL.md": md(body="a" * 3_000_000)})
    assert any("SKILL.md unpacks to more than 100,000 bytes" in e for e in read_skill(zip_base64=b64(big_md)).errors)
    many = make_zip({"SKILL.md": md(), **{f"part{n}.bin": b"\0" * 400_000 for n in range(3)}})  # each fits, the sum does not
    assert any("in all" in e for e in read_skill(zip_base64=b64(many)).errors)


def test_a_header_that_lies_about_a_size_is_not_believed():
    low = patch_sizes(make_zip({"SKILL.md": md(), "big.txt": "a" * 500_000}), "big.txt", 10)  # says 10, holds 500,000
    found = read_skill(zip_base64=b64(low))
    assert found.files == [] and any("could not be unpacked" in e for e in found.errors)
    high = patch_sizes(make_zip({"SKILL.md": md(), "small.txt": "hello"}), "small.txt", 900_000_000)  # says 900 MB, holds 5 bytes
    found = read_skill(zip_base64=b64(high))
    assert found.errors == [] and [(f.path, f.size) for f in found.files] == [("small.txt", 5)]


def test_the_archive_itself_and_its_entries_are_capped():
    assert any("at most 1,400,000" in e for e in read_skill(zip_base64=b64(b"x" * 1_400_001)).errors)
    assert any("not a .zip" in e for e in read_skill(zip_base64=b64(b"not a zip")).errors)
    assert any(f"at most {MAX_FILES}" in e for e in read_skill(zip_base64=b64(make_zip({"SKILL.md": md(), **{f"{n}.txt": "x" for n in range(MAX_FILES)}}))).errors)
    assert read_skill(zip_base64=b64(make_zip({"SKILL.md": md(), **{f"{n}.txt": "x" for n in range(MAX_FILES - 1)}}))).errors == []
    assert any("entries" in e for e in read_skill(zip_base64=b64(make_zip({f"d{n}/": "" for n in range(1001)}))).errors)
    assert any("not UTF-8" in e for e in read_skill(zip_base64=b64(make_zip({"SKILL.md": b"---\n\xff\xfe\n---\nx"}))).errors)


def test_a_damaged_archive_is_an_error_never_a_crash():
    """Flip bytes of a good archive, a few hundred ways: the answer is always a result (a skill or errors), never an exception."""
    import random

    good = make_zip({"SKILL.md": md(), "notes/a.txt": "hello", "b.bin": bytes(range(256)) * 4}, folder="s/")
    rng = random.Random(7)
    for _ in range(400):
        data = bytearray(good)
        for _ in range(rng.randint(1, 6)):
            data[rng.randrange(len(data))] = rng.randrange(256)
        if rng.random() < 0.3:
            del data[rng.randrange(len(data)) :]  # cut short
        found = read_skill(zip_base64=b64(bytes(data)))
        assert found.errors or found.name


# ----------------------------------------------------------------------
# Adding, replacing, removing
# ----------------------------------------------------------------------
async def test_a_preview_stores_nothing_and_says_what_would_happen(client, services):
    zipped = make_zip({"SKILL.md": md(extra="allowed-tools: Bash\npacks: [mail, nope]\n"), "scripts/go.py": "print(1)", "pic.png": b"\x89PNG\x00"}, folder="r/")
    preview = (await client.post("/v1/skills/preview", json={"zip_base64": b64(zipped)})).json()
    assert preview["name"] == "review-pr" and preview["can_add"] is False and preview["would_replace"] is False
    assert preview["packs"] == [{"id": "mail", "label": "Mail", "known": True}, {"id": "nope", "label": "nope", "known": False}]
    assert preview["ignored"] == ["allowed-tools"] and any("allowed-tools is ignored" in w for w in preview["warnings"])
    assert [(f["path"], f["kind"]) for f in preview["files"]] == [("pic.png", "binary"), ("scripts/go.py", "text")]
    assert preview["files"][1]["text"] == "print(1)" and preview["files"][0]["text"] is None
    assert "scripts are not run" in preview["files"][1]["note"] and preview["skill_md_text"].startswith("---\nname: review-pr")
    assert any("nope" in e for e in preview["errors"])
    assert services.skills.count() == 0 and services.db.value("SELECT COUNT(*) FROM skill_files", (), 0) == 0
    good = (await client.post("/v1/skills/preview", json={"skill_md": md()})).json()
    assert good["can_add"] is True and good["errors"] == [] and services.skills.count() == 0
    assert (await client.post("/v1/skills/preview", json={})).json()["can_add"] is False  # nothing sent: said, not a crash
    assert (await client.post("/v1/skills/preview", json={"skill_md": md(), "x": 1})).status_code == 422


async def test_add_replace_and_remove(client, services):
    zipped = make_zip({"SKILL.md": md(), "notes.txt": "hello", "pic.png": b"\x89PNG\x00"}, folder="review-pr/")
    made = await client.post("/v1/skills", json={"zip_base64": b64(zipped)})
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["name"] == "review-pr" and body["scope"] == "global" and body["replaced"] is False and body["created_by"] == "tester"
    assert [(f["path"], f["size"], f["kind"]) for f in body["files"]] == [("notes.txt", 5, "text"), ("pic.png", 5, "binary")]
    assert all("data" not in f and "bytes" not in f for f in body["files"]) and "body" not in body
    assert services.db.value("SELECT data FROM skill_files WHERE path = 'notes.txt'") == b"hello"
    assert (await add(client)).status_code == 409  # a name that is taken needs `replace`
    changed = await add(client, md(description="New", body="Do it."), replace=True)
    assert changed.status_code == 200 and changed.json()["replaced"] is True and changed.json()["description"] == "New"
    assert changed.json()["files"] == [] and services.db.value("SELECT COUNT(*) FROM skill_files", (), 0) == 0  # replaced whole
    assert (await client.get("/v1/skills/review-pr")).json()["skill_md"].endswith("Do it.\n")
    assert (await client.delete("/v1/skills/review-pr")).json() == {"removed": True}
    assert (await client.delete("/v1/skills/review-pr")).status_code == 404 and (await client.get("/v1/skills/review-pr")).status_code == 404
    assert services.skills.count() == 0
    lines = [row[0] for row in services.db.all("SELECT detail FROM audit_log WHERE outcome = 'skill' ORDER BY id")]
    assert lines == ["tester added the skill review-pr", "tester replaced the skill review-pr", "tester removed the skill review-pr"]


async def test_an_add_reads_the_skill_again_and_refuses_what_a_preview_would_refuse(client, services):
    response = await add(client, md(name="tools"))
    assert response.status_code == 422 and "command of Clara's clients" in response.json()["error"]["message"]
    assert (await add(client, md(extra="packs: [nope]\n"))).status_code == 422
    assert (await client.post("/v1/skills", json={"zip_base64": b64(make_zip({"SKILL.md": md(), "../x": "x"}))})).status_code == 422
    assert services.skills.count() == 0


async def test_at_most_thirty_skills(client, services):
    for number in range(MAX_SKILLS):
        assert (await add(client, md(name=f"s{number}"))).status_code == 201
    over = await add(client, md(name="one-more"))
    assert over.status_code == 409 and "30 skills are held" in over.json()["error"]["message"]
    preview = (await client.post("/v1/skills/preview", json={"skill_md": md(name="one-more")})).json()
    assert preview["can_add"] is False and preview["skills_held"] == 30 and preview["skills_max"] == 30
    assert (await add(client, md(name="s3", description="again"), replace=True)).status_code == 200  # a replace is not one more
    await client.delete("/v1/skills/s0")
    assert (await add(client, md(name="one-more"))).status_code == 201


async def test_the_request_is_capped_at_two_megabytes(client):
    response = await client.post("/v1/skills/preview", content=b'{"skill_md": "' + b"x" * 2_100_000 + b'"}', headers={"Content-Type": "application/json"})
    assert response.status_code == 413


# ----------------------------------------------------------------------
# Who may change them
# ----------------------------------------------------------------------
async def test_a_script_and_the_discord_bot_cannot_change_skills_but_may_read_them(client, services):
    await add(client)
    app = create_app(services)
    for name, kind in (("script1", "script"), ("bot", "discord")):
        raw = services.tokens.create(name, kind)[1]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://clara.test", headers={"Authorization": f"Bearer {raw}"}) as other:
            for call in (
                other.post("/v1/skills/preview", json={"skill_md": md()}),
                other.post("/v1/skills", json={"skill_md": md(name="x")}),
                other.post("/v1/skills", json={"skill_md": md(), "replace": True}),
                other.delete("/v1/skills/review-pr"),
            ):
                assert (await call).status_code == 403
            assert [s["name"] for s in (await other.get("/v1/skills")).json()["skills"]] == ["review-pr"]
            assert (await other.get("/v1/skills/review-pr")).status_code == 200
    assert services.skills.count() == 1


def test_the_model_has_no_tool_for_skills(services):
    assert not [name for name in services.toolbox.names if "skill" in name]
    assert not [pack.id for pack in PACKS if "skill" in pack.id or any("skill" in tool for tool in pack.tools)]


# ----------------------------------------------------------------------
# Scope
# ----------------------------------------------------------------------
async def test_a_skill_of_a_project_does_not_exist_anywhere_else(client, services, llm):
    first = (await client.post("/v1/projects", json={"name": "Thesis"})).json()["id"]
    second = (await client.post("/v1/projects", json={"name": "Other"})).json()["id"]
    await add(client, md(name="everywhere", body="Global."))
    made = await add(client, md(name="mine", body="Only for the thesis."), scope={"projects": [first]})
    assert made.status_code == 201 and made.json()["scope"] == "projects" and made.json()["projects"] == [first]

    def names(response) -> list[str]:
        return [s["name"] for s in response.json()["skills"]]

    assert names(await client.get("/v1/skills")) == ["everywhere", "mine"]  # the owner's page: all, with their projects
    assert names(await client.get("/v1/skills", params={"project": first})) == ["everywhere", "mine"]
    assert names(await client.get("/v1/skills", params={"project": second})) == ["everywhere"]
    assert names(await client.get("/v1/skills", params={"project": "none"})) == ["everywhere"]
    assert (await client.get("/v1/skills/mine", params={"project": second})).status_code == 404
    assert (await client.get("/v1/skills/mine", params={"project": first})).status_code == 200
    for chosen, expanded in ((first, True), (second, False), (None, False)):
        llm.replies = ["ok"]
        body = {"message": "/mine now"} | ({"project": chosen} if chosen else {})
        done = parse_sse((await client.post("/v1/chat/stream", json=body)).text)[-1]
        sent = asked(llm)
        assert ("Only for the thesis." in sent) is expanded and ("/mine now" in sent) is not expanded
        assert services.conversations.read_back(done["conversation"])[0].get("shown", "") == ("/mine now" if expanded else "")
    # the same on a conversation that already exists
    other = services.conversations.create("web", project_id=second)
    llm.replies = ["ok"]
    await client.post("/v1/chat/stream", json={"message": "/mine", "conversation": other.id})
    assert "Only for the thesis." not in asked(llm)
    status = await client.get("/v1/skills/mine/status", params={"conversation": other.id})
    assert status.status_code == 404  # not in that conversation's menu
    here = services.conversations.create("web", project_id=first)
    assert (await client.get("/v1/skills/mine/status", params={"conversation": here.id})).status_code == 200
    assert (await client.get("/v1/skills/mine/status", params={"project": str(first)})).status_code == 200
    assert (await client.get("/v1/skills/mine/status")).status_code == 404


async def test_a_skill_needs_known_projects_and_goes_with_them(client, services):
    assert (await add(client, scope={"projects": [999]})).status_code == 404
    assert (await add(client, scope={"projects": []})).status_code == 422
    project = (await client.post("/v1/projects", json={"name": "Thesis"})).json()["id"]
    await add(client, scope={"projects": [project, project]})
    assert services.db.value("SELECT COUNT(*) FROM skill_projects", (), 0) == 1
    await client.delete(f"/v1/projects/{project}")
    assert services.db.value("SELECT COUNT(*) FROM skill_projects", (), 0) == 0
    [left] = (await client.get("/v1/skills")).json()["skills"]
    assert left["scope"] == "projects" and left["projects"] == []  # reserved for projects that are gone: nowhere, but listed on the page
    assert (await client.get("/v1/skills", params={"project": "none"})).json()["skills"] == []
    replaced = await add(client, scope="global", replace=True)
    assert replaced.json()["scope"] == "global" and replaced.json()["projects"] == []


async def test_the_packs_of_a_skill_and_their_state_in_a_conversation(client, services):
    await add(client, md(extra="packs: [mail, web, qcm]\n"))
    listed = (await client.get("/v1/skills")).json()["skills"][0]
    assert listed["packs"] == ["mail", "web", "qcm"]
    fresh = (await client.get("/v1/skills/review-pr/status")).json()  # a conversation not started: the packs of a new one
    assert {p["id"]: p["on"] for p in fresh["packs"]} == {"mail": False, "web": True, "qcm": False}
    assert fresh["packs"][0]["label"] == "Mail" and fresh["packs"][0]["available"] is False and "mailbox" in fresh["packs"][0]["reason"]
    conversation = (await client.post("/v1/conversations", json={"packs": ["web", "qcm"]})).json()["id"]
    status = (await client.get("/v1/skills/review-pr/status", params={"conversation": conversation})).json()
    assert {p["id"]: p["on"] for p in status["packs"]} == {"mail": False, "web": True, "qcm": True}
    assert (await client.get("/v1/skills/review-pr/status", params={"conversation": "c" + "0" * 16})).status_code == 404
    # a skill switches nothing on by itself
    await client.post("/v1/chat", json={"message": "/review-pr 1", "conversation": conversation})
    assert "mail" not in services.conversations.get(conversation).active_packs()


# ----------------------------------------------------------------------
# A command in a chat
# ----------------------------------------------------------------------
def test_arguments_are_put_in_the_instructions():
    assert substitute("Review $ARGUMENTS.", "pr 12 now") == "Review pr 12 now."
    assert substitute("$1, $2, $3 and $ARGUMENTS", 'a "b c" \'d e\'') == "a, b c, d e and a \"b c\" 'd e'"
    assert substitute("Only $1 and $2 and $ARGUMENTS.", "x") == "Only x and  and x."  # a missing word is empty
    assert substitute("Do it.", "") == "Do it." and substitute("Do $ARGUMENTS.", "") == "Do ."
    assert substitute("Do it.", "a b") == "Do it.\n\nArguments: a b"  # no $ARGUMENTS: they go on a last line
    assert substitute("Use $1 here.", "a b") == "Use a here.\n\nArguments: a b"  # also when it reads the words
    assert substitute("$ARGUMENTS and $1", "$1 $ARGUMENTS") == "$1 $ARGUMENTS and $1"  # one pass: an argument is never a placeholder
    assert substitute("$10 $ARGUMENTSX $0", "a") == "$10 $ARGUMENTSX $0\n\nArguments: a"
    assert substitute("Say $1 $ARGUMENTS.", '"unbalanced') == 'Say "unbalanced "unbalanced.'


async def test_a_command_sends_the_instructions_and_stores_what_was_typed(client, services, llm):
    await add(client, md(body="Review $1 and note: $ARGUMENTS"))
    llm.replies = ["Reviewed."]
    events = parse_sse((await client.post("/v1/chat/stream", json={"message": "/review-pr 123 urgent", "keep_running": False})).text)
    done = events[-1]
    assert done["type"] == "done" and done["tainted"] is False and done["reply"] == "Reviewed."
    sent = asked(llm)
    assert sent.endswith("Review 123 and note: 123 urgent") and "/review-pr" not in sent and "name: review-pr" not in sent
    shown = (await client.get(f"/v1/conversations/{done['conversation']}/messages")).json()["messages"]
    assert shown[0]["role"] == "user" and shown[0]["shown"] == "/review-pr 123 urgent"
    assert shown[0]["content"].endswith("Review 123 and note: 123 urgent")  # the stored question is what the model read
    assert "shown" not in shown[1]
    [listed] = (await client.get("/v1/conversations")).json()["conversations"]
    assert listed["preview"] == "/review-pr 123 urgent"  # the list shows what was typed
    assert services.agent.starts_tainted(done["conversation"]) is False
    # the next turn replays what the model read, not what was typed
    llm.replies = ["More."]
    await client.post("/v1/chat", json={"message": "and then?", "conversation": done["conversation"]})
    replay = [m["content"] for m in llm.seen[-1]["messages"] if m["role"] == "user"]
    assert any("Review 123 and note: 123 urgent" in text for text in replay)
    # a command alone, on a line of its own, and followed by a newline
    llm.replies = ["ok"]
    await client.post("/v1/chat", json={"message": "/review-pr\nthe second line"})
    assert asked(llm).endswith("Review the and note: the second line")


async def test_a_word_that_is_not_a_skill_is_plain_text(client, services, llm):
    await add(client)
    for text in ("/nothing here", "/tools", "/new", "/Review-pr 1", " /review-pr 1", "/review-pr1", "/review-pr/x", "text /review-pr 1"):
        llm.replies = ["ok"]
        done = (await client.post("/v1/chat", json={"message": text})).json()
        assert asked(llm).endswith(text.strip()) and "carefully" not in asked(llm), text
        assert "shown" not in services.conversations.read_back(done["conversation"])[0]


async def test_a_reserved_name_is_never_expanded_even_if_a_row_holds_it(client, services, llm):
    services.db.execute(
        "INSERT INTO skills (name, description, skill_md, body, scope, size, created_at) VALUES ('tools', 'x', 'x', 'EXPANDED', 'global', 1, 'x')"
    )
    llm.replies = ["ok"]
    await client.post("/v1/chat", json={"message": "/tools on"})
    assert asked(llm).endswith("/tools on")


async def test_the_expansion_is_bounded(client, services, llm):
    await add(client, md(body="Do $ARGUMENTS."))
    before = len(llm.seen)
    response = await client.post("/v1/chat", json={"message": "/review-pr " + "x" * MAX_EXPANDED})
    assert response.status_code == 413 and "at most 120,000" in response.json()["error"]["message"]
    streamed = await client.post("/v1/chat/stream", json={"message": "/review-pr " + "x" * MAX_EXPANDED})
    assert streamed.status_code == 413 and len(llm.seen) == before
    llm.replies = ["ok"]
    assert (await client.post("/v1/chat", json={"message": "/review-pr " + "x" * 8_000})).status_code == 200


async def test_a_command_with_a_file_keeps_the_file_and_shows_its_name(client, services, llm):
    await add(client)
    llm.replies = ["ok"]
    done = (await client.post("/v1/chat", json={"message": "/review-pr this", "attachments": [{"name": "a.txt", "data": b64(b"file text")}]})).json()
    sent = asked(llm)
    assert "Review this carefully." in sent and "file text" in sent
    assert services.conversations.read_back(done["conversation"])[0]["shown"] == "/review-pr this\n\n(Attached: a.txt)"


async def test_the_owners_skill_text_is_the_owners_words(client, services, llm):
    """The skill's addresses may be fetched without asking, as an address the owner typed: its text is not outside content."""
    await add(client, md(body="Read https://example.org/guide first."))
    llm.replies = ["ok"]
    done = (await client.post("/v1/chat", json={"message": "/review-pr"})).json()
    assert done["tainted"] is False
    assert "https://example.org/guide" in services.conversations.read_back(done["conversation"])[0]["content"]


# ----------------------------------------------------------------------
# A summary names the skills that were run
# ----------------------------------------------------------------------
def test_the_line_of_a_summary_is_added_and_read_back():
    assert with_skills("Did things.", ["a", "b"]) == f"Did things.\n\n{SUMMARY_LINE}a, b"
    assert with_skills("Did things.", []) == "Did things."
    assert split_summary(with_skills("Did things.\nMore.", ["a", "b"])) == ("Did things.\nMore.", ["a", "b"])
    assert split_summary("Plain.") == ("Plain.", [])


async def test_a_summary_names_the_skills_run_and_keeps_their_text_out(services, llm):
    services.skills.add(read_skill(skill_md=md(name="review-pr", body="SECRET INSTRUCTIONS $ARGUMENTS")), scope="global", projects=[], replace=False)
    services.skills.add(read_skill(skill_md=md(name="weekly-report", body="Weekly.")), scope="global", projects=[], replace=False)
    conversation = services.conversations.create("web")
    turns = [("SECRET INSTRUCTIONS 1", "/review-pr 1"), ("plain question", ""), ("Weekly.", "/weekly-report"), ("/unknown thing", ""), ("last", ""), ("lastest", "")]
    for question, shown in turns:
        services.conversations.add_turn(conversation.id, question, [], shown=shown)
    llm.replies = ["The person reviewed a pull request and asked for a report."]
    await services.agent.compact(conversation.id)
    summary = services.conversations.get(conversation.id).summary
    assert summary == f"The person reviewed a pull request and asked for a report.\n\n{SUMMARY_LINE}review-pr, weekly-report"
    transcript = llm.seen[-1]["messages"][-1]["content"]
    assert "/review-pr 1" in transcript and "/weekly-report" in transcript and "SECRET INSTRUCTIONS" not in transcript
    assert "/unknown thing" in transcript  # a word that is no skill stays what it was
    # the next summary keeps the line, whether or not the old messages are still there, and drops a skill that was removed
    for number in range(3):
        services.conversations.add_turn(conversation.id, f"more {number}", [])
    services.skills.remove("weekly-report")
    llm.replies = ["A second summary."]
    await services.agent.compact(conversation.id)
    assert "Skills run in this conversation: review-pr" in (current := services.conversations.get(conversation.id).summary)
    assert "weekly-report" not in current and current.count(SUMMARY_LINE) == 1
    assert SUMMARY_LINE not in llm.seen[-1]["messages"][-1]["content"]  # the summariser is given the text, not the line
    services.conversations.purge_summarised(conversation.id, services.conversations.get(conversation.id).summary_upto)  # the line is carried
    services.conversations.add_turn(conversation.id, "after", [])
    services.conversations.add_turn(conversation.id, "after again", [])
    services.conversations.add_turn(conversation.id, "after, again", [])
    llm.replies = ["A third summary."]
    await services.agent.compact(conversation.id)
    assert services.conversations.get(conversation.id).summary.endswith(f"{SUMMARY_LINE}review-pr")


async def test_a_conversation_that_ran_no_skill_gets_no_line(services, llm):
    services.skills.add(read_skill(skill_md=md()), scope="global", projects=[], replace=False)
    conversation = services.conversations.create("web")
    for number in range(4):
        services.conversations.add_turn(conversation.id, f"q{number}", [], shown="")
    llm.replies = ["Summary."]
    await services.agent.compact(conversation.id)
    assert services.conversations.get(conversation.id).summary == "Summary."


# ----------------------------------------------------------------------
# The database
# ----------------------------------------------------------------------
def test_a_database_at_version_35_gets_the_tables_and_keeps_what_it_had(tmp_path):
    connection = connect(tmp_path / "old.db")
    for migration in migrations()[:35]:
        _apply(connection, migration)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 35
    connection.execute("INSERT INTO projects (name, created_at, updated_at) VALUES ('Thesis', 'x', 'x')")
    assert connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'skills'").fetchone() is None
    _apply(connection, migrations()[35])
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 36 and migrations()[35].name.startswith("0036_skills")
    assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
    insert = "INSERT INTO skills (name, description, skill_md, body, scope, size, created_at) VALUES (?, 'd', 'x', 'x', ?, 1, 'x')"
    connection.execute(insert, ("good-1", "global"))
    for values in (("Bad", "global"), ("-x", "global"), ("a b", "global"), ("", "global"), ("x" * 41, "global"), ("ok", "everyone"), ("good-1", "global")):
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, values)
    connection.execute("INSERT INTO skill_files (skill, path, data, size, sha256, kind) VALUES ('good-1', 'a', x'00', 1, 'h', 'binary')")
    connection.execute("INSERT INTO skill_projects (skill, project_id) VALUES ('good-1', 1)")
    with pytest.raises(sqlite3.IntegrityError):  # a project that does not exist
        connection.execute("INSERT INTO skill_projects (skill, project_id) VALUES ('good-1', 99)")
    connection.execute("DELETE FROM skills WHERE name = 'good-1'")  # the files and the projects go with it
    assert connection.execute("SELECT COUNT(*) FROM skill_files").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM skill_projects").fetchone()[0] == 0


def test_the_limits_are_the_documented_ones(services):
    assert (MAX_SKILLS, MAX_FILES, MAX_SKILL_BYTES, MAX_SKILL_MD, MAX_EXPANDED) == (30, 50, 1_000_000, 100_000, 120_000)
    with pytest.raises(SkillError, match="no name"):
        services.skills.add(read_skill(skill_md=md(name=None)), scope="global", projects=[], replace=False)
