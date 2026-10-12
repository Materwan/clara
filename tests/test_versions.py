"""A question asked again or edited is a new version of it: the old one, and what followed, are kept aside."""

import pytest
from conftest import FakeBackend, fake_providers, say
from fastapi.testclient import TestClient

from clara.agent import Agent, ChatRequest
from clara.llm import LlmError
from clara.memory import TurnRow
from clara.prompt import SystemPrompt
from clara.server import create_app
from clara.tools import default_toolbox

AUTH = {"Authorization": "Bearer secret-cli"}
PC = {"surface": "app", "user_id": "pc"}
CONVERSATION = "app:pc:1"


@pytest.fixture
def erwan(memory):
    return memory.resolve("app", "pc", "Erwan")


def story(memory, erwan, exchanges=("one", "two", "three")):
    """A conversation of plain exchanges; the ids of the questions."""
    return [memory.add_turn(CONVERSATION, erwan.id, q, [TurnRow("assistant", f"re {q}")]) for q in exchanges]


def path(memory):
    return [(m.role, m.content) for m in memory.messages_after(CONVERSATION)]


# --- the memory ----------------------------------------------------------------------------------


def test_a_question_asked_again_takes_the_old_version_and_what_followed_off_the_path(memory, erwan):
    one, two, three = story(memory, erwan)
    detached = memory.detach_from(CONVERSATION, two)
    assert path(memory) == [("user", "one"), ("assistant", "re one")]
    assert len(detached) == 4
    new = memory.add_turn(CONVERSATION, erwan.id, "two, better", [TurnRow("assistant", "re two, better")])
    assert path(memory)[-2:] == [("user", "two, better"), ("assistant", "re two, better")]
    assert memory.versions_of(CONVERSATION, [new, one]) == {new: [two, new]}
    assert memory.history(CONVERSATION, 10)[0].content == "one"  # what the model reads
    assert memory.turns_after(CONVERSATION) == 2


def test_going_back_to_another_version_brings_what_followed_it(memory, erwan):
    one, two, three = story(memory, erwan)
    memory.detach_from(CONVERSATION, two)
    new = memory.add_turn(CONVERSATION, erwan.id, "two, better", [TurnRow("assistant", "re two, better")])
    memory.switch_version(CONVERSATION, two, 0)
    assert path(memory) == [(r, c) for q in ("one", "two", "three") for r, c in (("user", q), ("assistant", f"re {q}"))]
    memory.switch_version(CONVERSATION, new, 0)
    assert path(memory)[-2:] == [("user", "two, better"), ("assistant", "re two, better")]
    assert len(path(memory)) == 4
    assert memory.question(CONVERSATION, three) is None  # off the path


def test_a_new_turn_goes_after_the_version_that_is_followed(memory, erwan):
    one, two = story(memory, erwan, ("one", "two"))
    memory.detach_from(CONVERSATION, two)
    memory.add_turn(CONVERSATION, erwan.id, "two b", [TurnRow("assistant", "re two b")])
    memory.add_turn(CONVERSATION, erwan.id, "after b", [TurnRow("assistant", "re after b")])
    memory.switch_version(CONVERSATION, two, 0)
    assert [c for r, c in path(memory) if r == "user"] == ["one", "two"]
    memory.add_turn(CONVERSATION, erwan.id, "after a", [TurnRow("assistant", "re after a")])
    assert [c for r, c in path(memory) if r == "user"] == ["one", "two", "after a"]


def test_the_first_question_can_have_versions_too(memory, erwan):
    one, two = story(memory, erwan, ("one", "two"))
    memory.detach_from(CONVERSATION, one)
    new = memory.add_turn(CONVERSATION, erwan.id, "uno", [TurnRow("assistant", "re uno")])
    assert memory.versions_of(CONVERSATION, [new]) == {new: [one, new]}
    memory.switch_version(CONVERSATION, one, 0)
    assert [c for r, c in path(memory) if r == "user"] == ["one", "two"]


def test_nothing_goes_another_way_where_a_summary_stands(memory, erwan):
    one, two, three = story(memory, erwan)
    memory.detach_from(CONVERSATION, two)
    new = memory.add_turn(CONVERSATION, erwan.id, "two b", [TurnRow("assistant", "re two b")])
    with pytest.raises(ValueError, match="summarised"):
        memory.switch_version(CONVERSATION, two, new)  # the summary covers up to the new version
    with pytest.raises(ValueError, match="No such"):
        memory.switch_version(CONVERSATION, 9999, 0)


def test_a_server_that_stopped_while_a_question_was_asked_again_gets_the_old_version_back(tmp_path):
    from clara.memory import Memory

    path_ = tmp_path / "db.sqlite"
    memory = Memory(path_)
    erwan = memory.resolve("app", "pc", "Erwan")
    story(memory, erwan, ("one", "two"))
    memory.detach_from(CONVERSATION, memory.messages_after(CONVERSATION)[2].id)  # then the server stopped
    memory.close()
    memory = Memory(path_)
    assert [c for r, c in path(memory) if r == "user"] == ["one", "two"]
    memory.close()


def test_messages_from_before_the_versions_follow_each_other(tmp_path):
    from clara.memory import Memory

    path_ = tmp_path / "db.sqlite"
    memory = Memory(path_)
    erwan = memory.resolve("app", "pc", "Erwan")
    story(memory, erwan, ("one", "two"))
    with memory.database:  # as the rows of an old database are
        memory.database.execute("ALTER TABLE messages DROP COLUMN parent_id")
        memory.database.execute("ALTER TABLE messages DROP COLUMN active")
    memory.close()
    memory = Memory(path_)
    assert len(path(memory)) == 4
    ids = [m.id for m in memory.messages_after(CONVERSATION)]
    assert memory.versions_of(CONVERSATION, ids) == {}
    memory.close()


# --- the agent -------------------------------------------------------------------------------------


def agent_of(memory, tmp_path, backend):
    return Agent(memory, backend, default_toolbox(), SystemPrompt(tmp_path / "none.md"))


async def run(agent, message="", **fields):
    request = ChatRequest("app", "pc", "Erwan", message, conversation=CONVERSATION, **fields)
    return [event async for event in agent.turn(request)]


async def test_a_question_asked_again_is_answered_without_the_old_version(memory, tmp_path, erwan):
    backend = FakeBackend(say("fresh"))
    agent = agent_of(memory, tmp_path, backend)
    one, two, three = story(memory, erwan)
    events = await run(agent, "two, better", fork_of=two)
    sent = [m["content"] for m in backend.calls[0][0] if m["role"] != "system"]
    assert "re three" not in " ".join(sent) and "re two" not in " ".join(sent) and "re one" in " ".join(sent)
    done = events[-1]
    assert done["versions"] == [two, done["message_id"]]
    assert [c for r, c in path(memory) if r == "assistant"] == ["re one", "fresh"]


async def test_a_failed_new_version_leaves_the_old_one_where_it_was(memory, tmp_path, erwan):
    class Down(FakeBackend):
        async def stream(self, messages, tools):
            raise LlmError("down", 401)
            yield  # pragma: no cover

    agent = agent_of(memory, tmp_path, Down())
    one, two, three = story(memory, erwan)
    with pytest.raises(LlmError):
        await run(agent, "two, better", fork_of=two)
    assert [c for r, c in path(memory) if r == "user"] == ["one", "two", "three"]


async def test_it_says_which_tools_of_the_old_version_may_have_changed_something(memory, tmp_path, erwan):
    agent = agent_of(memory, tmp_path, FakeBackend(say("fresh"), say("fresh")))
    memory.add_turn(CONVERSATION, erwan.id, "note it", [
        TurnRow("assistant", "", [{"function": {"name": "remember", "arguments": {"fact": "x"}}}]),
        TurnRow("tool", "Remembered", tool_name="remember"),
        TurnRow("assistant", "Noted."),
    ])
    question = memory.messages_after(CONVERSATION)[0].id
    events = await run(agent, "", fork_of=question)
    discarded = [e for e in events if e["type"] == "discarded"]
    assert discarded and "remember" in discarded[0]["tools"]


# --- over HTTP -------------------------------------------------------------------------------------


@pytest.fixture
def backend():
    return FakeBackend()


@pytest.fixture
def client(settings, backend):
    with TestClient(create_app(settings, fake_providers(settings, backend))) as http:
        yield http


def chat(http, backend, message, answer="Sure.", **fields):
    backend.rounds.append(say(answer))
    response = http.post("/v1/chat", json={**PC, "message": message, "conversation": CONVERSATION, **fields}, headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def shown(http):
    return http.get(f"/v1/conversations/{CONVERSATION}/messages", params=PC, headers=AUTH).json()["messages"]


def test_retry_edit_and_go_back_over_http(client, backend):
    first = chat(client, backend, "Name a fruit", "Apple.")
    chat(client, backend, "Another", "Pear.")
    retried = chat(client, backend, "", "Banana.", fork_of=first["message_id"])  # no message: the same question
    assert [m["content"] for m in shown(client)] == ["Name a fruit", "Banana."]
    sent = [m["content"] for m in backend.calls[-1][0] if m["role"] == "user"]
    assert any("Name a fruit" in text for text in sent)

    edited = chat(client, backend, "Name a vegetable", "Leek.", fork_of=retried["message_id"])
    question = shown(client)[0]
    assert (question["content"], question["version"], len(question["versions"]), question["forkable"]) == (
        "Name a vegetable", 3, 3, True,
    )
    assert edited["versions"] == question["versions"]

    back = client.post(f"/v1/conversations/{CONVERSATION}/version", json={**PC, "message": first["message_id"]}, headers=AUTH)
    assert back.status_code == 200
    assert [m["content"] for m in shown(client)] == ["Name a fruit", "Apple.", "Another", "Pear."]


def test_only_the_persons_own_question_on_the_path_can_be_asked_again(client, backend):
    first = chat(client, backend, "hello", "Hi.")
    refused = client.post("/v1/chat", json={**PC, "conversation": CONVERSATION, "fork_of": 9999}, headers=AUTH)
    assert refused.status_code == 404
    answer_id = shown(client)[1]["id"]
    refused = client.post("/v1/chat", json={**PC, "conversation": CONVERSATION, "fork_of": answer_id}, headers=AUTH)
    assert refused.status_code == 404  # an answer is not a question
    other = {"surface": "app", "user_id": "someone"}
    refused = client.post("/v1/chat", json={**other, "conversation": CONVERSATION, "fork_of": first["message_id"]}, headers=AUTH)
    assert refused.status_code in (403, 404)


def test_a_model_for_one_turn_must_be_one_the_person_may_choose(client, backend):
    first = chat(client, backend, "hello", "Hi.")
    backend.rounds.append(say("Again."))
    refused = client.post(
        "/v1/chat", json={**PC, "conversation": CONVERSATION, "fork_of": first["message_id"], "model": "cloud:nope"},
        headers=AUTH,
    )
    assert refused.status_code == 422
