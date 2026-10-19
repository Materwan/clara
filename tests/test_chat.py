from __future__ import annotations

import contextlib

import pytest

from clara.agent.turn import ChatRequest
from conftest import parse_sse
from fakes import calls


async def test_streamed_chat_creates_a_conversation_and_stores_the_turn(client, services, llm):
    llm.replies = ["Hello there, Alex."]
    response = await client.post("/v1/chat/stream", json={"message": "hi"})
    assert response.status_code == 200
    events = parse_sse(response.text)
    kinds = [event["type"] for event in events]
    assert kinds[0] == "turn" and kinds[-1] == "done"
    assert "".join(event["text"] for event in events if event["type"] == "token") == "Hello there, Alex."
    done = events[-1]
    conversation = done["conversation"]
    stored = services.conversations.read_back(conversation)
    assert [m["role"] for m in stored] == ["user", "assistant"]
    assert stored[1]["content"] == "Hello there, Alex."
    usage = services.usage.stats("24h", "kind")
    assert usage["totals"]["calls"] >= 1


async def test_a_conversation_started_from_a_project_stays_in_it(client, llm):
    """What the site does from a project's page: create the conversation in the project, then chat in it."""
    llm.replies = ["Hello."]
    project = (await client.post("/v1/projects", json={"name": "Thesis"})).json()
    made = (await client.post("/v1/conversations", json={"project": project["id"]})).json()
    assert made["project"] == project["id"]
    response = await client.post("/v1/chat/stream", json={"message": "hi", "conversation": made["id"]})
    assert response.status_code == 200
    listed = (await client.get("/v1/conversations", params={"project": str(project["id"])})).json()["conversations"]
    assert [item["id"] for item in listed] == [made["id"]]
    assert (await client.get(f"/v1/projects/{project['id']}")).json()["conversations"] == 1
    outside = (await client.get("/v1/conversations", params={"project": "none"})).json()["conversations"]
    assert outside == []


async def test_plain_chat_and_remember_tool(client, services, llm):
    llm.replies = [calls(("remember", {"fact": "Alex lives in Paris."})), "Noted."]
    response = await client.post("/v1/chat", json={"message": "I live in Paris"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "Noted."
    assert body["tools"] == ["remember"]
    facts = services.facts.live()
    assert [fact.text for fact in facts] == ["Alex lives in Paris."]
    assert facts[0].source == "model" and facts[0].confirmed


async def test_a_token_is_required(services):
    import httpx

    from clara.app import create_app

    transport = httpx.ASGITransport(app=create_app(services))
    async with httpx.AsyncClient(transport=transport, base_url="http://clara.test") as anonymous:
        assert (await anonymous.get("/health")).json() == {"status": "ok"}
        refused = await anonymous.get("/v1/conversations")
        assert refused.status_code == 401
        assert refused.json()["error"]["code"] == "unauthorized"
        assert (await anonymous.get("/docs")).status_code == 404


async def test_a_revoked_token_is_refused_at_once(client, services, token):
    assert (await client.get("/v1/conversations")).status_code == 200
    services.tokens.revoke("tester")
    assert (await client.get("/v1/conversations")).status_code == 401


async def test_a_stopped_answer_is_kept_as_interrupted(services, llm):
    """The web's Stop button closes the stream: what was said stays, marked interrupted."""
    llm.replies = ["A long answer that will be cut somewhere in the middle of it."]
    conversation = None
    async with contextlib.aclosing(services.agent.turn(ChatRequest(message="tell me", client="web"))) as events:
        async for event in events:
            if event["type"] == "turn":
                conversation = event["conversation"]
            if event["type"] == "token":
                break
    stored = services.conversations.read_back(conversation)
    assert [m["role"] for m in stored] == ["user", "assistant"]
    assert "[This answer was interrupted" in stored[1]["content"]


def test_attachment_limits_fit_the_request_limit():
    """Four files of the largest kind would not fit the 48 MB request once base64-encoded: the total is what counts."""
    from clara.api.middleware import LIMITS
    from clara.knowledge.attachments import MAX_ATTACHMENTS, MAX_TOTAL_BYTES, Attached, FileError, prepare

    chat_limit = next(limit for pattern, limit in LIMITS if pattern.match("/v1/chat/stream"))
    assert MAX_TOTAL_BYTES * 4 / 3 + 1_000_000 < chat_limit
    with pytest.raises(FileError, match="in all"):
        prepare("hi", [Attached(f"notes{n}.txt", b"x" * 9_000_000) for n in range(MAX_ATTACHMENTS)])
    assert prepare("hi", [Attached("notes.txt", b"hello")])


async def test_the_history_lists_the_groups_you_switched_on(client, services):
    """Projects and schedules start hidden; a conversation follows its schedule's switch, else its project's."""
    project = (await client.post("/v1/projects", json={"name": "Thesis"})).json()
    daily = services.schedules.create("Morning brief", "Brief me", "2030-01-01T08:00", repeat="daily",
                                      project_id=project["id"], per_run=True)
    weekly = services.schedules.create("Weekly review", "Review", "2030-01-01T08:00", repeat="weekly", weekdays=[0])
    conversations = services.conversations
    loose = conversations.create("web", title="Loose").id
    chapter = conversations.create("web", title="Chapter", project_id=project["id"]).id
    run = conversations.create("server", title="Morning brief — 2030-01-01", project_id=project["id"]).id
    services.db.execute("INSERT INTO schedule_runs (schedule_id, due_at, status, conversation_id) VALUES (?, ?, 'ok', ?)",
                        (daily.id, "2030-01-01T07:00:00+00:00", run))
    review = conversations.create("server", kind="schedule", title="Weekly review").id
    services.db.execute("UPDATE schedules SET conversation_id = ? WHERE id = ?", (review, weekly.id))
    orphan = conversations.create("server", kind="schedule", title="Gone").id

    async def listed(**params) -> dict[str, dict]:
        found = (await client.get("/v1/conversations", params={"kind": "any", **params})).json()["conversations"]
        return {item["id"]: item for item in found}

    assert set(await listed()) == {loose, chapter, run, review, orphan}
    assert set(await listed(history="true")) == {loose}
    assert (await client.patch(f"/v1/projects/{project['id']}", json={"in_history": True})).json()["in_history"] is True
    assert set(await listed(history="true")) == {loose, chapter}  # the run answers to its schedule, not to the project
    assert (await client.patch(f"/v1/schedules/{daily.id}", json={"in_history": True})).json()["in_history"] is True
    shown = await listed(history="true")
    assert set(shown) == {loose, chapter, run}
    assert shown[run]["group"] == {"kind": "schedule", "id": daily.id, "name": "Morning brief"}
    assert shown[chapter]["group"] == {"kind": "project", "id": project["id"], "name": "Thesis"}
    assert shown[loose]["group"] is None
    conversations.update(review, pinned=True)  # pinned ones stay, whatever their group
    assert review in await listed(history="true")
    assert (await client.patch(f"/v1/projects/{project['id']}", json={"in_history": False})).json()["in_history"] is False
    assert chapter not in await listed(history="true")
    assert (await client.get("/v1/schedules")).json()["schedules"][1]["in_history"] is False
