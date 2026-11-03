"""The `present` tool: a card under Clara's answer for a workspace file she wrote in the conversation, or any file when the
"Files created" pack is on; the file is never read back to her; the card is found again from the stored conversation."""

from __future__ import annotations

import contextlib

from clara.agent.turn import ChatRequest
from clara.memory.conversations import TurnRow
from clara.mirror import text
from clara.workspace.present import PRESENTED, presented
from clara.workspace.store import Workspace
from fakes import calls

SECRET = "the-only-words-of-this-report"


async def run(services, message="hi", *, conversation=None, client="web") -> list[dict]:
    events = []
    request = ChatRequest(message=message, client=client, conversation=conversation)
    async with contextlib.aclosing(services.agent.turn(request)) as stream:
        async for event in stream:
            events.append(event)
    return events


def new_chat(services, packs=None) -> str:
    return services.conversations.create("web", packs=packs or ["web"]).id


def tool_texts(llm) -> list[str]:
    """What the model read as the results of tools, in the last prompt it was sent."""
    return [m["content"] for m in llm.seen[-1]["messages"] if m["role"] == "tool"]


async def write_elsewhere(services, path="reports/old.md", content="old\n", conversation="c" + "1" * 16) -> None:
    """A file that another conversation wrote: it is in the workspace, and not among this one's files."""
    await services.workspace.write(
        path, content, overwrite=False, message=Workspace.message("ws_write", path, "t-x", conversation)
    )


async def test_a_file_written_in_the_conversation_is_presented_without_being_read_back(services, llm):
    chat = new_chat(services)
    llm.replies = [
        calls(("ws_write", {"path": "reports/plan.md", "content": f"# Plan\n{SECRET}\n"})),
        calls(("present", {"path": "reports/plan.md"})),
        "It is below.",
    ]
    events = await run(services, "write a plan and give it to me", conversation=chat)
    card = next(e for e in events if e["type"] == "present")
    assert card == {"type": "present", "path": "reports/plan.md", "size": len(f"# Plan\n{SECRET}\n"), "origin": None}
    # the model read one line, never the content of the file
    result = tool_texts(llm)[-1]
    assert result.startswith(f"{PRESENTED} reports/plan.md\n") and SECRET not in result
    # reports/ is not notes/: no chat note card and nothing for Discord to upload
    assert events[-1]["type"] == "done" and events[-1]["files"] == []


async def test_a_file_from_another_conversation_needs_the_files_created_pack(services, llm):
    await write_elsewhere(services)
    chat = new_chat(services)
    llm.replies = [calls(("present", {"path": "reports/old.md"})), "I cannot."]
    events = await run(services, "give me the old report", conversation=chat)
    assert not [e for e in events if e["type"] == "present"]
    refusal = tool_texts(llm)[-1]
    assert refusal.startswith("Error:") and "not written in this conversation" in refusal and "Files created" in refusal

    services.conversations.update(chat, packs=["web", "files_created"])
    llm.replies = [calls(("present", {"path": "reports/old.md"})), "Here it is."]
    events = await run(services, "and now?", conversation=chat)
    assert [e["path"] for e in events if e["type"] == "present"] == ["reports/old.md"]


async def test_a_file_that_is_not_there_or_not_a_regular_file_is_refused(services, llm):
    chat = new_chat(services, ["web", "files_created"])
    (services.workspace.root / "folder").mkdir()
    llm.replies = [
        calls(("present", {"path": "missing.pdf"}), ("present", {"path": "folder"}), ("present", {"path": ".git/config"})),
        "No.",
    ]
    events = await run(services, "give me these", conversation=chat)
    assert not [e for e in events if e["type"] == "present"]
    results = tool_texts(llm)
    assert "does not exist" in results[0] and "is a folder" in results[1] and ".git" in results[2]


async def test_a_file_the_library_would_not_serve_is_not_presented(services, llm):
    chat = new_chat(services, ["web", "files_created"])
    await write_elsewhere(services, "reports/big.bin", "x" * 50)
    services.workspace.max_file_bytes = 10
    llm.replies = [calls(("present", {"path": "reports/big.bin"})), "Too big."]
    await run(services, "give it", conversation=chat)
    assert "bigger than" in tool_texts(llm)[-1]


async def test_at_most_five_files_per_answer_and_a_file_only_once(services, llm):
    chat = new_chat(services, ["web", "files_created"])
    for number in range(7):
        await write_elsewhere(services, f"reports/r{number}.md", f"{number}\n")
    llm.replies = [
        calls(*[("present", {"path": f"reports/r{number}.md"}) for number in range(7)], ("present", {"path": "reports/r0.md"})),
        "Done.",
    ]
    events = await run(services, "give me everything", conversation=chat)
    assert [e["path"] for e in events if e["type"] == "present"] == [f"reports/r{n}.md" for n in range(5)]
    assert "At most 5 files" in tool_texts(llm)[5]

    llm.replies = [calls(("present", {"path": "reports/r0.md"}), ("present", {"path": "reports/r0.md"})), "Once."]
    events = await run(services, "again", conversation=chat)
    assert len([e for e in events if e["type"] == "present"]) == 1
    assert "already presented" in tool_texts(llm)[-1]


async def test_a_file_that_came_from_outside_is_presented_with_where_it_came_from(services, llm):
    chat = new_chat(services, ["web", "files_created"])
    await write_elsewhere(services, "inbox/invoice.txt", "total 12\n")
    services.provenance.mark(["inbox/invoice.txt"], "a mail attachment", kind="quarantine")
    llm.replies = [calls(("present", {"path": "inbox/invoice.txt"})), "There."]
    events = await run(services, "give me the invoice", conversation=chat)
    card = next(e for e in events if e["type"] == "present")
    assert card["origin"] == "a mail attachment"
    assert not events[-1]["tainted"]  # nothing was read: presenting does not taint the conversation
    services.provenance.trust("inbox/invoice.txt")
    llm.replies = [calls(("present", {"path": "inbox/invoice.txt"})), "Again."]
    events = await run(services, "and again", conversation=chat)
    assert next(e for e in events if e["type"] == "present")["origin"] is None  # you trusted it: no badge


async def test_a_rule_on_the_path_applies_as_it_does_to_reading(services, llm):
    chat = new_chat(services)
    services.rules.add("refuse", tool="present", target="private/**")
    llm.replies = [
        calls(("ws_write", {"path": "private/diary.md", "content": "x\n"}), ("ws_write", {"path": "open/news.md", "content": "y\n"})),
        calls(("present", {"path": "private/diary.md"}), ("present", {"path": "open/news.md"})),
        "Only one.",
    ]
    events = await run(services, "write both and give them", conversation=chat)
    assert [e["path"] for e in events if e["type"] == "present"] == ["open/news.md"]
    assert tool_texts(llm)[-2].startswith("Refused")


async def test_the_card_comes_back_when_the_conversation_is_opened_again(client, services, llm):
    chat = new_chat(services)
    llm.replies = [
        calls(("ws_write", {"path": "reports/plan.md", "content": "# Plan\n"})),
        calls(("present", {"path": "reports/plan.md"}), ("present", {"path": "reports/nothing.md"})),
        "Below.",
    ]
    await run(services, "plan", conversation=chat)
    found = (await client.get(f"/v1/conversations/{chat}/messages", params={"calls": "true"})).json()["messages"]
    carrying = [m for m in found if m.get("presented")]
    assert len(carrying) == 1  # the call that failed (no such file) left no card
    assert carrying[0]["presented"] == [{"path": "reports/plan.md", "size": 7, "origin": None}]
    plain = (await client.get(f"/v1/conversations/{chat}/messages")).json()["messages"]
    assert [m["presented"] for m in plain if m.get("presented")] == [carrying[0]["presented"]]  # kept without the calls

    await services.workspace.delete("reports/plan.md", message="x")
    again = (await client.get(f"/v1/conversations/{chat}/messages", params={"calls": "true"})).json()["messages"]
    gone = next(m for m in again if m.get("presented"))["presented"][0]
    assert gone == {"path": "reports/plan.md", "size": None, "origin": None, "missing": True}


async def test_the_workspace_serves_the_card_as_a_download(client, services, llm):
    chat = new_chat(services)
    llm.replies = [calls(("ws_write", {"path": "reports/plan.md", "content": "# Plan\n"})), calls(("present", {"path": "reports/plan.md"})), "x"]
    await run(services, "plan", conversation=chat)
    got = await client.get("/v1/workspace/file", params={"path": "reports/plan.md", "raw": "true"})
    assert got.content == b"# Plan\n" and "attachment" in got.headers["content-disposition"]


async def test_an_unattended_run_can_present_and_the_card_waits_in_the_conversation(client, services, llm):
    chat = new_chat(services)
    llm.replies = [
        calls(("ws_write", {"path": "reports/daily.md", "content": "d\n"})),
        calls(("present", {"path": "reports/daily.md"})),
        "Done.",
    ]
    request = ChatRequest(message="daily report", client="web", conversation=chat, mode="unattended")
    async with contextlib.aclosing(services.agent.turn(request)) as stream:
        events = [event async for event in stream]
    assert [e["path"] for e in events if e["type"] == "present"] == ["reports/daily.md"]
    found = (await client.get(f"/v1/conversations/{chat}/messages", params={"calls": "true"})).json()["messages"]
    assert any(m.get("presented") for m in found)


# ---- finding the cards in what was stored ------------------------------------------------------------------------------
def test_presented_reads_the_calls_and_the_results_that_say_they_worked():
    call = lambda path: {"name": "present", "arguments": {"path": path}}  # noqa: E731
    other = {"name": "ws_read", "arguments": {"path": "a.md"}}
    results = [f"{PRESENTED} a.md\nThe person is offered it", "Error: b.md does not exist."]
    assert presented([other, call("a.md"), call("b.md"), call("./a.md")], results) == ["a.md"]
    assert presented([call("../escape")], [f"{PRESENTED} ../escape\nx"]) == []
    assert presented([{"name": "present", "arguments": "not an object"}], results) == []


def test_discord_gets_a_line_with_the_path_and_the_live_and_stored_answers_agree(services):
    conversation = services.conversations.create("web", title="Plan")
    calls_ = [{"id": "1", "type": "function", "function": {"name": "present", "arguments": {"path": "reports/plan.md"}}}]
    services.conversations.add_turn(
        conversation.id,
        "give me the plan",
        [
            TurnRow("assistant", "", tool_calls=calls_),
            TurnRow("tool", f"{PRESENTED} reports/plan.md\nOffered.", tool_name="present"),
            TurnRow("assistant", "Below."),
        ],
        client="web",
        turn_id="t-1",
    )
    sync = next(c for c in services.mirror.sync(0)["conversations"] if c["id"] == conversation.id)
    stored = sync["turns"][0]["reply"]["text"]
    assert stored == "🔧 Presented · 1 actions\nBelow.\n📎 reports/plan.md"
    assert text.answer_text(["present"], "Below.", ["reports/plan.md"]) == stored  # what the live path writes
