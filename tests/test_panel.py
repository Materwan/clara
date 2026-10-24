"""The side panel of the web chat: the files Clara wrote, the QCMs answered one question at a time, and the questions
Clara asks while she works (ask_questions)."""

from __future__ import annotations

import asyncio
import contextlib

import httpx
import pytest

from clara.agent.turn import ChatRequest
from clara.app import create_app
from clara.connectors import asks
from clara.connectors.forms import answer_message, build_form, check_answer, final_line, summary
from fakes import calls

FORM = {
    "title": "Capitals",
    "questions": [
        {"text": "Capital of Australia?", "type": "single", "options": ["Sydney", "Canberra", "Perth"],
         "correct": [1], "hint": "Not the biggest city", "explanation": "Canberra was built to be the capital."},
        {"text": "Which are in Europe?", "type": "multiple", "options": ["Paris", "Lima", "Rome"], "correct": [0, 2]},
        {"text": "Why is the sky blue?", "type": "text", "answer": "Rayleigh scattering"},
    ],
}


async def run_turn(services, message="hi", *, client="web", conversation=None, on_event=None) -> list[dict]:
    events = []
    request = ChatRequest(message=message, client=client, conversation=conversation)
    async with contextlib.aclosing(services.agent.turn(request)) as stream:
        async for event in stream:
            events.append(event)
            if on_event is not None:
                await on_event(event)
    return events


def quiz_conversation(services) -> str:
    """A web conversation with the QCM pack on (it is off in a new one, like Mail)."""
    return services.conversations.create("web", packs=["web", "qcm"]).id


async def make_quiz(services, llm) -> tuple[str, str]:
    """A conversation in which Clara made the QCM above: its id and the form's reference."""
    llm.replies = [calls(("qcm", FORM)), "Here it is."]
    conversation = quiz_conversation(services)
    events = await run_turn(services, "quiz me", conversation=conversation)
    form = next(event["form"] for event in events if event["type"] == "qcm")
    return conversation, form["ref"]


@pytest.fixture
async def web(services) -> httpx.AsyncClient:
    """A client with a web token: only the web site answers Clara's questions."""
    token = services.tokens.create("site", "web")[1]
    transport = httpx.ASGITransport(app=create_app(services))
    async with httpx.AsyncClient(transport=transport, base_url="http://clara.test",
                                 headers={"Authorization": f"Bearer {token}"}) as http:
        yield http


# ---- the form and its messages --------------------------------------------------------------------------


def test_a_question_keeps_its_hint():
    form = build_form(FORM)
    assert [q["hint"] for q in form["questions"]] == ["Not the biggest city", "", ""]
    assert form["graded"] and form["ref"] == build_form({**FORM, "questions": [
        {**q, "hint": "other"} for q in FORM["questions"]]})["ref"]  # a hint is not part of the reference


def test_an_answer_is_checked_against_the_question():
    form = build_form(FORM)
    single, multiple, text = form["questions"]
    assert check_answer(single, [1]) == [1] and check_answer(multiple, [2, 0, 2]) == [0, 2]
    assert check_answer(text, "  because  ") == "because"
    for question, bad in ((single, [0, 1]), (single, []), (single, [3]), (single, ["1"]), (multiple, [-1]),
                          (multiple, "A"), (text, "  "), (text, ["x"]), (text, "x" * 5000)):
        with pytest.raises(ValueError):
            check_answer(question, bad)


def test_the_message_for_an_answer_says_which_question_and_what_the_key_says():
    form = build_form(FORM)
    message = answer_message(form, 1, [0])
    assert message.splitlines() == [f"[QCM answer {form['ref']} 1/3] Capitals", "Question: Capital of Australia?",
                                    "Answer: A. Sydney", "Marked by the key: wrong"]
    assert "Marked" not in answer_message(form, 3, "because")  # a free text has no key
    entries = [{"status": "answered", "answer": [1]}, {"status": "skipped", "answer": []},
               {"status": "answered", "answer": "x"}]
    assert final_line(summary(form, entries)) == "Final: 2 of 3 answered, score 1/1, skipped: 2."
    assert answer_message(form, 3, "x", summary(form, entries)).endswith(
        "\n\nThat was the last question. Final: 2 of 3 answered, score 1/1, skipped: 2.")


# ---- answering one question at a time ---------------------------------------------------------------------


async def test_a_qcm_is_answered_one_question_at_a_time(client, services, llm):
    conversation, ref = await make_quiz(services, llm)
    base = f"/v1/conversations/{conversation}"

    panel = (await client.get(f"{base}/panel")).json()
    [form] = panel["forms"]
    assert form["ref"] == ref and form["title"] == "Capitals" and form["created_at"]
    assert form["questions"][0]["hint"] == "Not the biggest city"
    assert form["progress"]["entries"] == [None, None, None] and form["progress"]["attempt"] == 1

    first = (await client.post(f"{base}/qcm/{ref}/answer", json={"question": 1, "answer": [1]})).json()
    assert first["message"].startswith(f"[QCM answer {ref} 1/3] Capitals") and "Marked by the key: right" in first["message"]
    assert "last question" not in first["message"] and first["progress"]["summary"]["answered"] == 1

    skipped = (await client.post(f"{base}/qcm/{ref}/skip", json={"question": 2})).json()
    assert skipped["message"] is None  # a skipped question does not wake Clara
    assert skipped["progress"]["entries"][1]["status"] == "skipped"

    last = (await client.post(f"{base}/qcm/{ref}/answer", json={"question": 3, "answer": "light bends"})).json()
    assert last["message"].endswith("That was the last question. Final: 2 of 3 answered, score 1/1, skipped: 2.")
    assert last["progress"]["finished"] and last["progress"]["closed"]

    # a skipped question can still be answered: Clara corrects it, but is not given the score again
    later = (await client.post(f"{base}/qcm/{ref}/answer", json={"question": 2, "answer": [0, 2]})).json()
    assert "Marked by the key: right" in later["message"] and "Final" not in later["message"]
    assert (await client.post(f"{base}/qcm/{ref}/answer", json={"question": 1, "answer": [0]})).status_code == 409

    # what was done is there when the page is opened again
    again = (await client.get(f"{base}/panel")).json()["forms"][0]["progress"]
    assert [e["status"] for e in again["entries"]] == ["answered"] * 3 and again["summary"]["right"] == 2

    retried = (await client.post(f"{base}/qcm/{ref}/retry")).json()["progress"]
    assert retried["attempt"] == 2 and retried["attempts"] == 2 and retried["entries"] == [None, None, None]
    assert (await client.post(f"{base}/qcm/{ref}/retry")).json()["progress"]["attempt"] == 2  # nothing done: no new one
    again = (await client.post(f"{base}/qcm/{ref}/answer", json={"question": 1, "answer": [1]})).json()
    assert "Final" not in again["message"] and again["progress"]["entries"][0]["status"] == "answered"


async def test_a_last_question_that_is_skipped_sends_the_score_alone(client, services, llm):
    conversation, ref = await make_quiz(services, llm)
    base = f"/v1/conversations/{conversation}/qcm/{ref}"
    await client.post(f"{base}/answer", json={"question": 1, "answer": [0]})
    await client.post(f"{base}/skip", json={"question": 2})
    done = (await client.post(f"{base}/skip", json={"question": 3})).json()
    assert done["message"].splitlines() == [f"[QCM finished {ref}] Capitals",
                                            "Final: 1 of 3 answered, score 0/1, skipped: 2, 3."]
    assert (await client.post(f"{base}/skip", json={"question": 3})).json()["message"] is None  # told once


async def test_a_bad_answer_is_refused_and_leaves_the_question_open(client, services, llm):
    conversation, ref = await make_quiz(services, llm)
    base = f"/v1/conversations/{conversation}/qcm/{ref}"
    for question, answer in ((1, [0, 1]), (1, [7]), (3, "  "), (9, [0])):
        assert (await client.post(f"{base}/answer", json={"question": question, "answer": answer})).status_code == 422
    assert (await client.post(f"/v1/conversations/{conversation}/qcm/00000000/skip", json={"question": 1})).status_code == 404
    assert (await client.get(f"/v1/conversations/c{'0' * 16}/panel")).status_code == 404
    progress = (await client.get(f"/v1/conversations/{conversation}/panel")).json()["forms"][0]["progress"]
    assert progress["entries"] == [None, None, None]


async def test_a_qcm_made_before_the_panel_shows_the_answers_it_was_given(client, services, llm):
    conversation, ref = await make_quiz(services, llm)
    llm.replies = ["Well done."]
    old = (f"[QCM answers {ref}] Capitals\n\n1. Capital of Australia?\nAnswer: B. Canberra\n\n"
           "2. Which are in Europe?\nAnswer: A. Paris; C. Rome\n\n3. Why is the sky blue?\nAnswer: (no answer)")
    await run_turn(services, old, conversation=conversation)
    progress = (await client.get(f"/v1/conversations/{conversation}/panel")).json()["forms"][0]["progress"]
    assert progress["legacy"] and progress["finished"]
    assert [e["status"] for e in progress["entries"]] == ["answered", "answered", "skipped"]
    assert progress["summary"]["right"] == 2 and progress["summary"]["asked"] == 2


async def test_a_deleted_conversation_takes_its_progress_with_it(client, services, llm):
    conversation, ref = await make_quiz(services, llm)
    await client.post(f"/v1/conversations/{conversation}/qcm/{ref}/answer", json={"question": 1, "answer": [1]})
    assert services.db.value("SELECT COUNT(*) FROM quiz_answers") == 1
    assert (await client.delete(f"/v1/conversations/{conversation}")).status_code == 200
    assert services.db.value("SELECT COUNT(*) FROM quiz_attempts") == 0
    assert services.db.value("SELECT COUNT(*) FROM quiz_answers") == 0


# ---- the files ---------------------------------------------------------------------------------------------


async def test_the_panel_lists_the_files_clara_wrote_in_that_conversation(client, services, llm):
    llm.replies = [calls(("ws_write", {"path": "notes/plan.md", "content": "# Plan"}),
                         ("ws_write", {"path": "data/table.csv", "content": "a,b\n1,2\n"})), "Written."]
    first = (await run_turn(services, "write two files"))[-1]["conversation"]
    llm.replies = [calls(("ws_write", {"path": "notes/other.md", "content": "elsewhere"})), "Written."]
    second = (await run_turn(services, "and another conversation's"))[-1]["conversation"]

    files = (await client.get(f"/v1/conversations/{first}/panel")).json()["files"]
    assert sorted(item["path"] for item in files) == ["data/table.csv", "notes/plan.md"]
    assert all(item["created"] and item["size"] > 0 for item in files)
    assert [item["path"] for item in (await client.get(f"/v1/conversations/{second}/panel")).json()["files"]] == [
        "notes/other.md"]

    await services.workspace.delete("data/table.csv", message="test")  # a file that is gone is not offered
    files = (await client.get(f"/v1/conversations/{first}/panel")).json()["files"]
    assert [item["path"] for item in files] == ["notes/plan.md"]


# ---- ask_questions -----------------------------------------------------------------------------------------

QUESTIONS = [
    {"question": "Which database?", "header": "Database", "options": [
        {"label": "SQLite (Recommended)", "description": "One file"}, {"label": "Postgres", "description": "A server"}]},
    {"question": "Which extras?", "header": "Extras", "multi_select": True,
     "options": [{"label": "Cache"}, {"label": "Search"}, {"label": "Mail"}]},
]


def test_the_questions_are_checked():
    ask = asks.build_ask({"questions": QUESTIONS})
    assert ask["questions"][1]["multi_select"] and ask["questions"][0]["options"][1]["label"] == "Postgres"
    too_many = [QUESTIONS[0]] * 5
    for bad in ([], too_many, [{"question": "q", "options": [{"label": "only one"}]}],
                [{"question": "q", "options": ["a", "A"]}], [{"question": "", "options": ["a", "b"]}],
                [{"question": "q", "header": "a header that is far too long", "options": ["a", "b"]}], "nonsense"):
        with pytest.raises(asks.AskError):
            asks.build_ask({"questions": bad})
    # the answers: one option, or your own text, for a single question; any mix for a multiple one
    check = asks.check_replies
    assert check(ask, [{"options": [1], "other": ""}, {"options": [0, 2], "other": "more"}])[1]["other"] == "more"
    assert check(ask, [{"options": [], "other": "MySQL"}, {"options": [], "other": "none"}])
    for bad in ([{"options": [0], "other": "x"}, {"options": [0]}], [{"options": [0]}], [{"options": [5]}, {"options": [0]}],
                [{"options": []}, {"options": [0]}]):
        with pytest.raises(asks.AskError):
            check(ask, bad)


async def test_the_turn_waits_for_the_answers_and_clara_reads_them(services, llm):
    llm.replies = [calls(("ask_questions", {"questions": QUESTIONS})), "SQLite then."]

    async def answer(event):
        if event["type"] == "questions":
            ask = event["ask"]
            assert ask["questions"][1]["options"][2]["label"] == "Mail"
            services.questions.reply(ask["id"], ask["conversation"],
                                     [{"options": [0], "other": ""}, {"options": [1], "other": "a queue"}])

    events = await run_turn(services, "build it", on_event=answer)
    kinds = [event["type"] for event in events]
    assert kinds.index("tool_start") < kinds.index("questions") < kinds.index("questions_done") < kinds.index("tool")
    assert events[-1]["reply"] == "SQLite then."
    result = next(event["result"] for event in events if event["type"] == "tool")
    assert "[Database] Which database?\n   Answer: SQLite (Recommended)" in result
    assert "Answer: Search; Other: a queue" in result
    tool_message = llm.seen[1]["messages"][-1]
    assert tool_message["role"] == "tool" and "Answer: SQLite (Recommended)" in tool_message["content"]
    assert services.questions.waiting(events[-1]["conversation"]) == []


async def test_dismissed_questions_are_a_refusal_clara_is_told_about(services, llm):
    llm.replies = [calls(("ask_questions", {"questions": QUESTIONS[:1]})), "Understood."]

    async def dismiss(event):
        if event["type"] == "questions":
            services.questions.reply(event["ask"]["id"], event["ask"]["conversation"], declined=True)

    events = await run_turn(services, "build it", on_event=dismiss)
    done = next(event for event in events if event["type"] == "questions_done")
    assert done["declined"] and not done["timed_out"]
    assert next(event["result"] for event in events if event["type"] == "tool").startswith("The person dismissed")


async def test_questions_nobody_answers_are_withdrawn(services, llm):
    object.__setattr__(services.settings, "approval_wait", 0.2)
    llm.replies = [calls(("ask_questions", {"questions": QUESTIONS[:1]})), "Going on without."]
    events = await run_turn(services, "build it")
    assert next(event for event in events if event["type"] == "questions_done")["timed_out"]
    assert events[-1]["reply"] == "Going on without." and services.questions.waiting(events[-1]["conversation"]) == []


async def test_a_turn_that_ends_takes_its_questions_back(services, llm):
    llm.replies = [calls(("ask_questions", {"questions": QUESTIONS[:1]})), "never said"]
    async with contextlib.aclosing(services.agent.turn(ChatRequest(message="build it", client="web"))) as stream:
        async for event in stream:
            if event["type"] == "questions":
                ask = event["ask"]
                break  # the client goes away while the turn waits
    assert services.questions.waiting(ask["conversation"]) == []
    with pytest.raises(asks.NotWaiting):
        services.questions.reply(ask["id"], ask["conversation"], declined=True)


async def test_only_a_web_turn_that_can_wait_is_offered_the_questions(services, llm):
    async def offered(**request) -> set[str]:
        llm.seen.clear()
        llm.replies = ["ok"]
        await services.agent.answer(ChatRequest(message="hello", **request))
        return {tool["function"]["name"] for tool in llm.seen[0]["tools"]}

    quiz = quiz_conversation(services)
    names = await offered(client="web")
    assert "ask_questions" in names and "qcm" not in names  # the QCM is a pack: off until it is switched on
    assert {"ask_questions", "qcm"} <= await offered(client="web", conversation=quiz)
    names = await offered(client="cli", conversation=quiz)
    assert "ask_questions" not in names and "qcm" not in names  # and only the web site shows one
    assert "ask_questions" not in await offered(client="web", mode="unattended")
    assert "ask_questions" not in await offered(client="web", wait_for_approvals=False)


async def test_the_answers_come_through_the_api(client, web, services, llm):
    llm.replies = [calls(("ask_questions", {"questions": QUESTIONS})), "Done."]
    turn = asyncio.ensure_future(run_turn(services, "build it"))
    ask = None
    for _ in range(100):
        waiting = [item for item in services.questions._pending.values()]
        if waiting:
            ask = waiting[0]
            break
        await asyncio.sleep(0.02)
    assert ask is not None
    url = f"/v1/conversations/{ask.conversation_id}/questions/{ask.id}"
    good = [{"options": [1], "other": ""}, {"options": [0], "other": ""}]

    assert (await client.post(url, json={"answers": good})).status_code == 403  # a cli token does not answer
    assert (await web.post(url, json={})).status_code == 422
    assert (await web.post(url, json={"answers": [{"options": [0]}]})).status_code == 422  # one answer is missing
    assert (await web.post(f"/v1/conversations/c{'1' * 16}/questions/{ask.id}", json={"answers": good})).status_code == 409
    assert (await web.post(url, json={"answers": good})).json() == {"ok": True}
    assert (await web.post(url, json={"answers": good})).status_code == 409  # answered once
    events = await turn
    assert "Answer: Postgres" in next(event["result"] for event in events if event["type"] == "tool")
