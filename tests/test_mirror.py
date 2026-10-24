"""The Discord mirror, on the server: which place each conversation is in, what its messages read as, what the bot is told
and reports, the archive, the limits, and what Discord changes by itself (which asks you before anything is deleted)."""

from __future__ import annotations

import pytest

from clara.gate.approvals import ApprovalError
from clara.memory.conversations import TurnRow
from clara.mirror import text
from clara.mirror.service import INCOGNITO, MAIN


@pytest.fixture
def bot(services) -> dict[str, str]:
    return {"Authorization": f"Bearer {services.tokens.create('bot', 'discord')[1]}", "X-Clara-Client": "discord"}


def talk(services, title: str = "Plan", question: str = "Bonjour", reply: str = "Salut", turn: str = "t-1", **kwargs) -> str:
    conversation = services.conversations.create("web", title=title, **kwargs)
    services.conversations.add_turn(
        conversation.id, question, [TurnRow("assistant", reply)], client="web", turn_id=turn
    )
    return conversation.id


def place_of(services, conversation_id: str, since: int = 0) -> str | None:
    entries = {entry["id"]: entry for entry in services.mirror.sync(since)["conversations"]}
    return entries[conversation_id]["place"]


# ----------------------------------------------------------------------
# Places
# ----------------------------------------------------------------------
def test_a_loose_conversation_is_in_the_main_place_and_an_incognito_one_in_its_own(services):
    loose = talk(services)
    private = talk(services, title="Secret", incognito=True)
    assert place_of(services, loose) == MAIN
    assert place_of(services, private) == INCOGNITO


def test_a_local_only_conversation_is_never_on_discord(services):
    local = talk(services, local_only=True)
    assert place_of(services, local) is None
    assert services.mirror.sync(0)["conversations"][0]["turns"] == []


def test_a_project_is_a_place_only_while_its_history_is_shown(services):
    project = services.projects.create("Work")
    conversation = talk(services, project_id=project.id)
    assert place_of(services, conversation) is None  # not shown in the history: not on Discord
    services.projects.show_in_history(project.id, True)
    services.mirror.group_switched(f"project:{project.id}", True)
    assert place_of(services, conversation) == f"project:{project.id}"
    places = {place["key"]: place for place in services.mirror.sync(0)["places"]}
    assert places[f"project:{project.id}"]["name"] == "Work"
    assert places[MAIN]["name"] == "Clara"


async def test_switching_a_group_off_archives_its_conversations_and_on_unarchives_them(client, services):
    project = services.projects.create("Work")
    services.projects.show_in_history(project.id, True)
    conversation = talk(services, project_id=project.id)
    services.mirror.group_switched(f"project:{project.id}", True)
    await client.patch(f"/v1/projects/{project.id}", json={"in_history": False})
    described = services.conversations.get(conversation).describe()
    assert described["archived"] and described["archived_by"] == "group"
    await client.patch(f"/v1/projects/{project.id}", json={"in_history": True})
    assert not services.conversations.get(conversation).describe()["archived"]


async def test_deleting_a_project_moves_its_conversations_out_of_it_on_discord_too(services):
    project = services.projects.create("Work")
    services.projects.show_in_history(project.id, True)
    conversation = talk(services, project_id=project.id)
    before = services.mirror.sync(0)["cursor"]
    services.projects.delete(project.id)
    entries = {entry["id"]: entry for entry in services.mirror.sync(before)["conversations"]}
    assert entries[conversation]["place"] == MAIN


# ----------------------------------------------------------------------
# What a turn reads as
# ----------------------------------------------------------------------
def test_a_turn_is_a_question_and_an_answer_with_the_tools_line(services):
    conversation = services.conversations.create("web", title="Recherche")
    rows = [
        TurnRow("assistant", "", tool_calls=[{"function": {"name": "web_search", "arguments": "{}"}}]),
        TurnRow("tool", "résultats", tool_name="web_search"),
        TurnRow("assistant", "Voilà."),
    ]
    services.conversations.add_turn(conversation.id, "cherche", rows, client="web", turn_id="t-9")
    (entry,) = services.mirror.sync(0)["conversations"]
    (turn,) = entry["turns"]
    assert turn["key"] == "t-9" and turn["user"]["text"] == "cherche" and turn["user"]["ref"] == ""
    assert turn["reply"]["text"] == "🔧 Searched the web · 1 actions\nVoilà."
    assert "résultats" not in turn["reply"]["text"]  # the results of tools are never shown


def test_documents_of_a_question_are_named_not_repeated(services):
    question = '<document name="notes.md" type="text">\nlong body\n</document>\nRésume-le'
    text_, names = text.question_text(question)
    assert names == ["notes.md"] and text_ == "Résume-le\n📎 notes.md"


def test_the_tools_line_counts_and_names_the_kinds_like_the_web_site(services):
    names = ["web_search"] * 24 + ["web_fetch"] * 6 + ["ws_read"]
    assert text.tools_line(names) == "🔧 Searched the web ×24, read a page ×6, read · 31 actions"
    assert text.tools_line([]) == ""


def test_a_question_asked_again_is_a_new_turn_and_the_old_one_leaves_the_mirror(services):
    conversation = talk(services, turn="t-1")
    services.conversations.add_turn(conversation, "encore", [TurnRow("assistant", "non")], client="web", turn_id="t-2")
    keys = [turn["key"] for turn in services.mirror.sync(0)["conversations"][0]["turns"]]
    assert keys == ["t-1", "t-2"]


# ----------------------------------------------------------------------
# The revision clock and what the bot is told
# ----------------------------------------------------------------------
def test_a_sync_gives_only_what_changed_after_the_cursor(services):
    first = talk(services, title="Un")
    cursor = services.mirror.sync(0)["cursor"]
    second = talk(services, title="Deux")
    changed = [entry["id"] for entry in services.mirror.sync(cursor)["conversations"]]
    assert changed == [second] and first not in changed


def test_the_report_keeps_the_ids_and_a_changed_thread_forgets_its_posts(services):
    conversation = talk(services)
    services.mirror.report(
        {
            "places": [{"key": MAIN, "category_id": "10", "forum_id": "11"}],
            "threads": [{"conversation": conversation, "thread_id": "20", "place": MAIN, "archived": False}],
            "posts": [{"conversation": conversation, "turn": "t-1", "side": "user", "ids": ["30"], "digest": "d1"}],
        }
    )
    entry = next(item for item in services.mirror.sync(0)["conversations"] if item["id"] == conversation)
    assert entry["thread"] == {"thread_id": "20", "place": MAIN, "archived": False}
    assert entry["posted"]["t-1"]["user"] == {"ids": ["30"], "digest": "d1"}
    services.mirror.report({"threads": [{"conversation": conversation, "thread_id": "21", "place": MAIN}]})
    entry = next(item for item in services.mirror.sync(0)["conversations"] if item["id"] == conversation)
    assert entry["posted"] == {}  # a new thread: nothing it holds was written there yet


def test_a_deleted_conversation_leaves_its_thread_for_the_bot_to_remove(services):
    conversation = talk(services)
    services.mirror.report({"threads": [{"conversation": conversation, "thread_id": "20", "place": MAIN}]})
    services.conversations.delete(conversation)
    assert services.mirror.sync(0)["trash"] == ["20"]
    services.mirror.report({"cleared": ["20"]})
    assert services.mirror.sync(0)["trash"] == []


def test_a_stop_is_an_alert_and_resuming_clears_it(services):
    services.mirror.report({"stopped": "the server has as many channels as Discord allows (500)"})
    assert services.mirror.sync(0)["stopped"].startswith("the server has as many channels")
    assert any(event.kind == "alert" for event in services.events.recent(10))
    services.mirror.resume()
    assert services.mirror.sync(0)["stopped"] is None


# ----------------------------------------------------------------------
# Archive
# ----------------------------------------------------------------------
async def test_an_archived_conversation_can_be_read_but_takes_no_message(client, services):
    conversation = talk(services)
    response = await client.patch(f"/v1/conversations/{conversation}", json={"archived": True})
    assert response.json()["archived"] is True
    refused = await client.post("/v1/chat", json={"message": "encore", "conversation": conversation})
    assert refused.status_code == 409
    shown = await client.get(f"/v1/conversations/{conversation}/messages")
    assert shown.status_code == 200 and shown.json()["messages"]
    listed = (await client.get("/v1/conversations", params={"archived": "yes"})).json()["conversations"]
    assert [item["id"] for item in listed] == [conversation]
    assert conversation not in [item["id"] for item in (await client.get("/v1/conversations")).json()["conversations"]]


async def test_archiving_on_discord_is_followed_and_unarchiving_there_too(services, bot, client):
    conversation = talk(services)
    services.mirror.report({"threads": [{"conversation": conversation, "thread_id": "20", "place": MAIN}]})
    response = await client.post("/v1/discord/event", json={"kind": "thread_archived", "thread": "20", "archived": True},
                                 headers=bot)
    assert response.json()["conversation"] == conversation
    assert services.conversations.get(conversation).describe()["archived_by"] == "discord"
    await client.post("/v1/discord/event", json={"kind": "thread_archived", "thread": "20", "archived": False},
                      headers=bot)
    assert not services.conversations.get(conversation).describe()["archived"]


# ----------------------------------------------------------------------
# The live feed
# ----------------------------------------------------------------------
def test_a_turn_of_a_mirrored_conversation_is_watched_from_its_start_to_its_stored_answer(services, monkeypatch):
    published: list[dict] = []
    monkeypatch.setattr(services.mirror.live, "publish", published.append)
    conversation = talk(services, title="Live")
    watch = services.mirror.watch("Quoi de neuf ?")
    watch.see({"type": "turn", "id": "t-live", "conversation": conversation})
    watch.see({"type": "token", "text": "Rien "})
    watch.see({"type": "tool_start", "name": "web_search"})
    calls = [{"function": {"name": "web_search", "arguments": "{}"}}]
    services.conversations.add_turn(
        conversation, "Quoi de neuf ?", [TurnRow("assistant", "Rien.", tool_calls=calls)], client="web", turn_id="t-live"
    )
    watch.see({"type": "done", "reply": "Rien.", "files": []})
    assert [event["type"] for event in published] == ["start", "token", "summary", "done"]
    start = published[0]
    assert start["turn"] == "t-live" and start["title"] == "Live" and start["text"] == "Quoi de neuf ?"
    assert published[-1]["text"] == "🔧 Searched the web · 1 actions\nRien."
    assert published[-1]["digest"] == text.digest(published[-1]["text"])


def test_a_turn_that_starts_in_a_conversation_with_no_thread_tells_the_bot_what_came_before(services, monkeypatch):
    """A project switched into the history after its conversations began, a bot that was off: the conversation reached
    Discord at its latest question only, and its earlier turns came last."""
    published: list[dict] = []
    monkeypatch.setattr(services.mirror.live, "publish", published.append)
    conversation = talk(services, title="Live", question="Q1", reply="A1")  # stored, never posted
    watch = services.mirror.watch("Q2")
    watch.see({"type": "turn", "id": "t-2", "conversation": conversation})
    earlier = published[0]["earlier"]
    assert [(turn["user"]["text"], turn["reply"]["text"]) for turn in earlier] == [("Q1", "A1")]
    services.mirror.link_thread(conversation, "55")  # once it has its thread, the sync keeps it right: nothing to tell
    watch = services.mirror.watch("Q3")
    watch.see({"type": "turn", "id": "t-3", "conversation": conversation})
    assert published[1]["thread"] == "55" and published[1]["earlier"] == []


def test_a_turn_that_fails_ends_what_was_shown_as_failed(services, monkeypatch):
    published: list[dict] = []
    monkeypatch.setattr(services.mirror.live, "publish", published.append)
    conversation = talk(services, title="Panne")
    watch = services.mirror.watch("essaie")
    watch.see({"type": "turn", "id": "t-fail", "conversation": conversation})
    watch.see({"type": "token", "text": "Je"})
    watch.close()
    assert published[-1]["type"] == "done" and published[-1]["failed"] is True and published[-1]["text"] == "Je"


async def test_the_bot_routes_are_for_the_discord_client_only(client):
    response = await client.get("/v1/discord/sync")  # the client fixture signs in as a command-line client
    assert response.status_code == 403


async def test_a_thread_deleted_on_discord_asks_you_and_the_bot_cannot_answer(client, services, bot):
    conversation = talk(services, title="Brouillon")
    services.mirror.report({"threads": [{"conversation": conversation, "thread_id": "20", "place": MAIN}]})
    response = await client.post("/v1/discord/event", json={"kind": "thread_deleted", "thread": "20"}, headers=bot)
    approval_id = response.json()["asked"]
    assert approval_id is not None
    assert services.conversations.get(conversation) is not None  # nothing is deleted before you answer
    (request,) = [item for item in services.approvals.list("pending") if item.id == approval_id]
    assert request.tool == "discord_forget" and request.level == "destructive"
    assert "Brouillon" in request.summary
    discord_token = services.tokens.create("bot-again", "discord")[0]
    with pytest.raises(ApprovalError):  # the bot's token may not answer a deletion
        await services.approvals.decide(approval_id, "allow_once", discord_token)
    assert services.approvals.get(approval_id).status == "pending"


async def test_a_run_cannot_write_in_an_archived_conversation_either(services):
    from clara.agent.turn import ChatRequest

    conversation = talk(services)
    services.mirror.set_archived(conversation, True, "you")
    with pytest.raises(ValueError, match="archived"):
        services.agent.plan(ChatRequest(message="rappel", client="server", conversation=conversation))


async def test_choosing_to_deliver_to_discord_shows_the_automation_and_brings_its_conversations_back(client, services):
    conversation = talk(services, kind="schedule")
    schedule = services.schedules.create(
        "Bilan", "Fais le bilan.", "2099-01-01T08:00", timezone="Europe/Paris", conversation_id=conversation
    )
    await client.patch(f"/v1/schedules/{schedule.id}", json={"in_history": True})
    await client.patch(f"/v1/schedules/{schedule.id}", json={"in_history": False})
    assert services.conversations.get(conversation).describe()["archived_by"] == "group"
    assert place_of(services, conversation) is None
    changed = (await client.patch(f"/v1/schedules/{schedule.id}", json={"deliver": "discord"})).json()
    assert changed["in_history"] is True  # its runs live in a forum thread, made from the switch
    assert not services.conversations.get(conversation).describe()["archived"]
    assert place_of(services, conversation) == f"schedule:{schedule.id}"
