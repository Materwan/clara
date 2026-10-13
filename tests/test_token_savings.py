"""What keeps the prompt small and stable: its order, the tools a surface is sent, outputs cut once read, the
token trigger of a compaction, the cached tokens the usage log keeps, the tools only some people get, old documents
sent as a note, and the side jobs done by a small model."""

from pathlib import Path

import pytest
from conftest import FakeBackend, call, say
from test_client_tools import READ_FILE, drive, make_agent, request

from clara.agent import (
    CHIME_INSTRUCTIONS,
    CUT_IN_TURN,
    IN_TURN_RESULT_KEEP,
    TOOL_HISTORY_MAX_TOKENS,
    Agent,
    ChatRequest,
)
from clara.attachments import document_digest
from clara.conversationfiles import ConversationFiles
from clara.llm import LlmChunk, LlmError, ToolCall, cached_tokens
from clara.memory import Fact, Person, StoredMessage
from clara.models import Chosen
from clara.projects import READ_CHARS_LIMIT
from clara.prompt import SystemPrompt
from clara.settings import DEFAULT_HIDDEN_TOOLS, Settings, SettingsError, parse_hidden_tools
from clara.tools import (
    INTEGRATION_TOOLS,
    MUSIC_TOOLS,
    Toolbox,
    ToolContext,
    _read_conversation_file,
    default_toolbox,
)
from clara.usagelog import UsageLog

# --- the prompt keeps what does not change first ------------------------------------------------------


def render(prompt: SystemPrompt, facts, summary="", relation=50) -> str:
    from datetime import datetime

    return prompt.render(
        Person(1, "Erwan"), "web", facts, datetime(2026, 10, 10), instructions="Use the tools.", summary=summary,
        relation=relation, project="## Project\nfiles", files="## Files\nnotes.md",
    )


def test_what_changes_during_a_conversation_comes_last(tmp_path):
    prompt = SystemPrompt(tmp_path / "none.md")
    text = render(prompt, [Fact(1, "likes tea")], summary="so far: tea")
    order = [text.index(h) for h in (
        "## Instructions from web", "## Project", "## Files", "## What you remember about Erwan",
        "## Your relationship with Erwan", "## Earlier in this conversation",
    )]
    assert order == sorted(order)


def test_a_new_fact_or_a_new_score_leaves_the_beginning_of_the_prompt_untouched(tmp_path):
    prompt = SystemPrompt(tmp_path / "none.md")
    before = render(prompt, [Fact(1, "likes tea")], relation=50)
    after = render(prompt, [Fact(1, "likes tea"), Fact(2, "has a cat")], relation=80)
    shared = len(__import__("os").path.commonprefix([before, after]))
    assert shared > before.index("## Files")  # the project, the files and the instructions are in the cached beginning


# --- tools a surface never needs ----------------------------------------------------------------------


def names(backend: FakeBackend) -> set[str]:
    return {schema["function"]["name"] for schema in backend.calls[-1][1] or []}


async def test_a_surface_is_not_sent_the_tools_it_hides(memory, tmp_path):
    backend = FakeBackend(say("a"), say("b"))
    agent = make_agent(memory, tmp_path, backend, hidden_tools={"discord": ["add_task", "remember"]})
    await drive(agent, request(surface="discord", user_id="1"))
    discord = names(backend)
    await drive(agent, request(surface="console"))
    console = names(backend)
    assert "list_tasks" in discord and not {"add_task", "remember"} & discord
    assert {"add_task", "remember"} <= console


def test_the_defaults_keep_answers_short_of_tool_rounds_and_projects_light(settings):
    assert settings.max_tool_rounds == 10 and settings.project_inline_tokens == 20_000 and settings.chime_check
    env = {
        "CLARA_OWNER": "root", "CLARA_TOKENS": "t:abcdefghijklmnopqrstuvwxyz0123456789",
        "CLARA_PROJECT_INLINE_TOKENS": "0", "CLARA_CHIME_CHECK": "false",
    }
    parsed = Settings.from_env(env)
    assert parsed.project_inline_tokens == 0 and not parsed.chime_check


def test_the_default_hidden_tools_are_for_discord_and_can_be_replaced():
    assert parse_hidden_tools(DEFAULT_HIDDEN_TOOLS)["discord"] >= {"add_task", "github_pr"}
    assert parse_hidden_tools("web=notify|forget, web=remind") == {"web": frozenset({"notify", "forget", "remind"})}
    assert parse_hidden_tools("") == {}
    with pytest.raises(SettingsError):
        parse_hidden_tools("discord")


# --- an output the model has read is cut in the next rounds -------------------------------------------


async def test_outputs_already_read_are_cut_in_the_prompt_but_kept_in_the_history(memory, tmp_path):
    backend = FakeBackend(*[call("read_file", path=str(i)) for i in range(3)], say("done"))
    agent = make_agent(memory, tmp_path, backend, toolbox=Toolbox([]), context_window=100_000)
    await drive(agent, request(tools=(READ_FILE,)), lambda n, a: a["path"] * 5_000)

    last = [m["content"] for m in backend.calls[-1][0] if m["role"] == "tool"]
    assert last[0] == "0" * IN_TURN_RESULT_KEEP + CUT_IN_TURN
    assert last[1] == "1" * IN_TURN_RESULT_KEEP + CUT_IN_TURN
    assert last[2] == "2" * 5_000  # the newest one has not been read yet
    assert [m.content for m in memory.messages_after("console:erwan") if m.role == "tool"][0] == "0" * 5_000


async def test_a_short_output_is_never_cut(memory, tmp_path):
    backend = FakeBackend(*[call("read_file", path=str(i)) for i in range(3)], say("done"))
    agent = make_agent(memory, tmp_path, backend, toolbox=Toolbox([]))
    await drive(agent, request(tools=(READ_FILE,)), lambda n, a: "short " + a["path"])
    assert [m["content"] for m in backend.calls[-1][0] if m["role"] == "tool"] == ["short 0", "short 1", "short 2"]


# --- a compaction when the messages weigh a lot, whatever the window -----------------------------------


async def talk(agent: Agent, message: str) -> list[dict]:
    return [e async for e in agent.turn(ChatRequest("cli", "erwan", "Erwan", message))]


async def test_a_conversation_is_summarised_when_its_messages_weigh_the_token_limit(memory, tmp_path):
    backend = FakeBackend(say("a1", prompt_tokens=3_000), say("a summary"))
    agent = make_agent(
        memory, tmp_path, backend, toolbox=Toolbox([]), compact_percent=0, compact_tokens=1_000, keep_recent_turns=0
    )
    events = await talk(agent, "q1")
    assert "compacted" in [e["type"] for e in events]
    assert memory.state("cli:erwan").summary == "a summary"


async def test_the_system_prompt_and_the_tools_do_not_count_for_the_token_limit(memory, tmp_path):
    backend = FakeBackend(say("a1", prompt_tokens=3_000), say("a summary"))
    agent = make_agent(  # 3,000 tokens in all, of which the prompt and the tools are most
        memory, tmp_path, backend, compact_percent=0, compact_tokens=2_900, keep_recent_turns=0
    )
    assert "compacted" not in [e["type"] for e in await talk(agent, "q1")]


async def test_no_token_limit_means_no_compaction(memory, tmp_path):
    backend = FakeBackend(say("a1", prompt_tokens=30_000))
    agent = make_agent(memory, tmp_path, backend, toolbox=Toolbox([]), compact_percent=0, compact_tokens=0)
    assert "compacted" not in [e["type"] for e in await talk(agent, "q1")]


# --- cached tokens ------------------------------------------------------------------------------------


def test_the_providers_ways_of_saying_what_came_from_their_cache():
    assert cached_tokens({"prompt_tokens_details": {"cached_tokens": 800}}) == 800
    assert cached_tokens({"prompt_cache_hit_tokens": 640}) == 640
    assert cached_tokens({"prompt_tokens": 900}) == 0
    assert cached_tokens({"prompt_tokens_details": None}) == 0


async def test_the_usage_log_keeps_the_cached_tokens_of_an_answer(memory, tmp_path):
    usage_log = UsageLog(memory)
    backend = FakeBackend([LlmChunk(text="a"), LlmChunk(prompt_tokens=1_000, completion_tokens=5, cached_tokens=700)])
    agent = Agent(memory, backend, default_toolbox(), SystemPrompt(Path(tmp_path) / "none.md"), usage_log=usage_log)
    await talk(agent, "hi")
    [row] = usage_log.history()["calls"]
    assert row["cached_tokens"] == 700 and row["prompt_tokens"] == 1_000
    assert usage_log.history()["totals"]["cached_tokens"] == 700


# --- only what the person can use ---------------------------------------------------------------------


class Connected:
    """Integrations with something attached to every conversation."""

    def attached(self, conversation, project, person_id):
        return [1]

    def context(self, conversation, project, person_id):
        return "## Connected resources\n[1] my-repo"

    def news(self, conversation):
        return []


async def test_on_the_web_whoever_attached_something_is_offered_the_integrations(memory, tmp_path):
    backend = FakeBackend(say("a"), say("b"))
    agent = make_agent(memory, tmp_path, backend, integrations=Connected())
    await drive(agent, request(surface="web", user_id="friend"))  # not the owner: what they attached works too
    assert INTEGRATION_TOOLS <= names(backend) and "my-repo" in backend.calls[-1][0][0]["content"]
    await drive(agent, request(surface="app", user_id="friend"))
    assert INTEGRATION_TOOLS <= names(backend)


async def test_the_music_tools_are_only_for_someone_with_a_player(memory, tmp_path):
    toolbox = default_toolbox(music=object(), accounts=object())
    ready = memory.resolve("console", "erwan", "Erwan").id
    backend = FakeBackend(say("a"), say("b"))
    agent = make_agent(memory, tmp_path, backend, toolbox=toolbox, music_ready=lambda person_id: person_id == ready)
    await drive(agent, request())
    assert MUSIC_TOOLS <= names(backend)
    await drive(agent, request(user_id="zoe", user_name="Zoe"))
    assert not names(backend) & MUSIC_TOOLS


async def test_a_hidden_tool_the_model_calls_anyway_is_not_run(memory, tmp_path):
    backend = FakeBackend(call("remember", fact="likes tea"), say("ok"))
    agent = make_agent(memory, tmp_path, backend, hidden_tools={"console": ["remember"]})
    events = await drive(agent, request())
    assert next(e for e in events if e["type"] == "tool")["result"] == "Unknown tool: remember."
    assert memory.facts(memory.find_person("console", "erwan").id, 10) == []


# --- old tool outputs and documents -------------------------------------------------------------------


def test_old_tool_outputs_fit_a_token_budget_however_big_the_window(memory, tmp_path):
    agent = make_agent(memory, tmp_path, FakeBackend())
    big = "x" * int(TOOL_HISTORY_MAX_TOKENS * 3.5 * 0.6)  # each is 60% of the budget
    stored = [StoredMessage("tool", big, None, None) for _ in range(3)]
    assert agent._recent_tool_rows(stored, window=1_000_000) == {2}  # 15% of the window would have kept them all


WEB_DOCUMENT = '(Attached: notes.md)\n\n<document name="notes.md" type="markdown">\n```markdown\n{}\n```\n</document>'


async def test_a_document_is_sent_whole_for_three_turns_then_as_a_note_and_can_be_read_again(memory, tmp_path):
    body = "\n".join(f"secret line {n}" for n in range(50))
    read = [LlmChunk(tool_calls=[ToolCall("read_conversation_file", {"name": "notes.md"})])]
    backend = FakeBackend(*(say(f"a{n}") for n in range(4)), read, say("ok"))
    files = ConversationFiles(memory)
    agent = make_agent(memory, tmp_path, backend, conversation_files=files)
    for message in (WEB_DOCUMENT.format(body), "q2", "q3", "q4"):
        await drive(agent, request(surface="web", message=message))
    first_question = [m["content"] for m in backend.calls[2][0] if m["role"] == "user"][0]
    assert "secret line 49" in first_question  # the third turn still has it whole
    first_question = [m["content"] for m in backend.calls[3][0] if m["role"] == "user"][0]
    assert "secret line" not in first_question and "not repeated here" in first_question
    number = files.of("web:erwan")[0].id
    assert f'<document name="notes.md" type="markdown" file="{number}">' in first_question
    assert f"read_conversation_file with file={number}" in first_question
    events = await drive(agent, request(surface="web", message="q5"))
    read = next(e for e in events if e["type"] == "tool")["result"]
    assert "notes.md, lines 1-" in read and "secret line 49" in read
    assert "secret line 0" in memory.transcript("web:erwan")[0][0].content  # the history keeps it all


async def test_an_old_document_stays_whole_where_the_read_tool_is_hidden(memory, tmp_path):
    body = "\n".join(f"secret line {n}" for n in range(50))
    backend = FakeBackend(*(say(f"a{n}") for n in range(4)))
    agent = make_agent(
        memory, tmp_path, backend, conversation_files=ConversationFiles(memory),
        hidden_tools={"web": ["read_conversation_file"]},
    )
    for message in (WEB_DOCUMENT.format(body), "q2", "q3", "q4"):
        await drive(agent, request(surface="web", message=message))
    system, first = backend.calls[3][0][0]["content"], [m for m in backend.calls[3][0] if m["role"] == "user"][0]
    assert "secret line 49" in first["content"]  # the model could not read it again: it keeps it
    assert "Files people sent" not in system


async def test_a_question_asked_again_does_not_keep_its_documents_twice(memory, tmp_path):
    files = ConversationFiles(memory)
    agent = make_agent(memory, tmp_path, FakeBackend(say("a"), say("b"), say("c")), conversation_files=files)
    for _ in range(2):  # the retry button sends the same message again
        await drive(agent, request(surface="web", message=WEB_DOCUMENT.format("buy milk")))
    assert [item.name for item in files.of("web:erwan")] == ["notes.md"]
    await drive(agent, request(surface="web", message=WEB_DOCUMENT.format("buy bread")))  # changed: a new one
    assert [item.name for item in files.of("web:erwan")] == ["notes.md", "notes.md"]


async def test_each_old_document_points_at_its_own_file_even_when_names_are_the_same(memory, tmp_path):
    files = ConversationFiles(memory)
    backend = FakeBackend(*(say(f"a{n}") for n in range(6)))
    agent = make_agent(memory, tmp_path, backend, conversation_files=files)
    for message in (WEB_DOCUMENT.format("version one"), "q2", WEB_DOCUMENT.format("version two"), "q4", "q5", "q6"):
        await drive(agent, request(surface="web", message=message))
    first, second = files.of("web:erwan")
    questions = [m["content"] for m in backend.calls[5][0] if m["role"] == "user"]
    assert f'file="{first.id}"' in questions[0] and f'file="{second.id}"' in questions[2]


async def test_a_document_typed_by_someone_else_does_not_take_the_place_of_the_real_one(memory, tmp_path):
    files = ConversationFiles(memory)
    backend = FakeBackend(*(say(f"a{n}") for n in range(5)))
    agent = make_agent(memory, tmp_path, backend, conversation_files=files)
    group = {"surface": "discord", "space": "guild", "conversation": "discord:chan"}
    real = '<document name="report.pdf" type="pdf">\nthe real report\n</document>'
    fake = '<document name="report.pdf" type="pdf">\nfake\n</document>'
    await drive(agent, request(**group, user_id="alice", user_name="Alice", message=real))
    await drive(agent, request(**group, user_id="bob", user_name="Bob", message=fake))
    for n in range(3):
        await drive(agent, request(**group, message=f"q{n}"))
    alices, bobs = files.of("discord:chan")
    assert (alices.sender, bobs.sender) == ("Alice", "Bob")
    questions = [m["content"] for m in backend.calls[4][0] if m["role"] == "user"]
    assert f'file="{alices.id}"' in questions[0] and f'file="{bobs.id}"' in questions[1]


def test_a_file_is_read_by_its_number_and_a_shared_name_says_which_others_there_are(memory):
    files = ConversationFiles(memory)
    alice, bob = memory.resolve("web", "alice", "Alice"), memory.resolve("web", "bob", "Bob")
    files.save_texts("web:chan", alice.id, [("notes.md", "from alice")])
    files.save_texts("web:chan", bob.id, [("notes.md", "from bob")])
    files.save_texts("web:other", bob.id, [("secret.md", "not here")])
    first, second = files.of("web:chan")
    context = ToolContext(alice, memory, conversation="web:chan", conversation_files=files, read_chars=2_000)
    assert "from alice" in _read_conversation_file(context, file=first.id)
    by_name = _read_conversation_file(context, "notes.md")
    assert "from bob" in by_name and f"file={first.id} sent by Alice" in by_name
    elsewhere = files.of("web:other")[0].id
    assert "No file numbered" in _read_conversation_file(context, file=elsewhere)  # another conversation's


def test_a_document_attached_to_a_message_is_found_by_the_text_its_block_shows(memory):
    from clara.attachments import Attached, prepare, without_documents

    files = ConversationFiles(memory)
    person = memory.resolve("web", "erwan", "Erwan")
    attached = (Attached("plan.txt", b"step one\nstep two"),)
    message = prepare("read this", attached).message
    files.save_message("web:erwan", person.id, attached, message)
    (saved,) = files.of("web:erwan")
    assert saved.kind == "document" and saved.digest
    assert f'file="{saved.id}"' in without_documents(message, {saved.digest: saved.id})


def test_texts_kept_before_digests_get_one(memory):
    person = memory.resolve("web", "erwan", "Erwan")
    with memory.lock, memory.database as db:
        db.execute(
            "INSERT INTO conversation_files (conversation, person_id, name, mime, kind, size, data, created_at)"
            " VALUES ('web:erwan', ?, 'old.md', '', 'text', 3, ?, '2026-10-01')", (person.id, b"old"),
        )
    (saved,) = ConversationFiles(memory).of("web:erwan")
    assert saved.digest == document_digest("old")


async def test_a_long_document_is_read_in_parts_as_the_window_allows(memory, tmp_path):
    files = ConversationFiles(memory)
    person = memory.resolve("web", "erwan", "Erwan")
    files.save_texts("web:erwan", person.id, [("long.txt", "\n".join("y" * 50 for _ in range(400)))])
    context = ToolContext(person, memory, conversation="web:erwan", conversation_files=files, read_chars=2_000)
    first = _read_conversation_file(context, "long.txt")
    assert "(read on with start_line=" in first and len(first) < 2_500
    later = _read_conversation_file(context, "long.txt", start_line=390)
    assert "lines 390-400 of 400" in later
    assert Agent._read_chars(32_768) < Agent._read_chars(200_000) == READ_CHARS_LIMIT


# --- side jobs on a small model -----------------------------------------------------------------------


class Refs(FakeBackend):
    """A backend that also answers for a model by its reference, and notes which one each round went to."""

    def __init__(self, *rounds):
        super().__init__(*rounds)
        self.refs: list[str | None] = []

    async def stream(self, messages, tools):
        self.refs.append(None)
        async for chunk in super().stream(messages, tools):
            yield chunk

    async def stream_ref(self, ref, messages, tools, api_key=None):
        self.refs.append(ref)
        async for chunk in super().stream(messages, tools):
            yield chunk


class Models:
    """A catalogue where people talk to a big model and the side jobs run on a small one."""

    def choose(self, surface, person_id, ref=None):
        return Chosen(ref or "cloud:big", 1.0, 100_000)

    def upkeep(self):
        return Chosen("local:small", 1.0, 32_000)

    def capabilities(self, ref):
        return {"vision": False}


async def test_summaries_and_titles_are_written_by_the_upkeep_model(memory, tmp_path):
    backend = Refs(say("a1"), say("a summary"), say("A title"))
    agent = make_agent(memory, tmp_path, backend, models=Models(), keep_recent_turns=0)
    await drive(agent, request(surface="web"))
    await agent.compact("web:erwan")
    memory.add_turn("web:erwan:2", memory.find_person("web", "erwan").id, "hi", [])
    await agent.title("web:erwan:2")
    assert backend.refs == ["cloud:big", "local:small", "local:small"]


class SmallIsDown(Refs):
    """The side jobs' model cannot be reached; the conversation's can."""

    async def stream_ref(self, ref, messages, tools, api_key=None):
        if ref == "local:small":
            self.refs.append(ref)
            raise LlmError("local: cannot reach the server")
        async for chunk in super().stream_ref(ref, messages, tools, api_key):
            yield chunk


async def test_a_summary_falls_back_on_the_conversations_model_when_the_small_one_fails(memory, tmp_path):
    backend = SmallIsDown(say("a1"), say("a summary"))
    agent = make_agent(memory, tmp_path, backend, models=Models(), keep_recent_turns=0)
    await drive(agent, request(surface="web"))
    await agent.compact("web:erwan")
    assert backend.refs == ["cloud:big", "local:small", "cloud:big"]
    assert memory.state("web:erwan").summary == "a summary"


async def test_a_message_not_for_clara_is_checked_by_the_small_model_first(memory, tmp_path):
    backend = Refs(say("no"), say("yes"), say("Here is the answer."))
    agent = make_agent(memory, tmp_path, backend, models=Models(), chime_check=True)
    group = {"surface": "discord", "user_id": "1", "user_name": "Ann", "conversation": "discord:channel:9", "mode": "maybe"}
    done = (await drive(agent, request(**group, message="see you tomorrow")))[-1]
    assert done["passed"] and done["observed"] and done["reply"] == ""
    assert backend.refs == ["local:small"] and backend.calls[0][0][0]["content"] == CHIME_INSTRUCTIONS
    assert "see you tomorrow" in memory.transcript("discord:channel:9")[0][-1].content  # kept as context
    done = (await drive(agent, request(**group, message="does anyone know how to fix this bug?")))[-1]
    assert done["reply"] == "Here is the answer." and backend.refs == ["local:small", "local:small", "cloud:big"]
    assert "see you tomorrow" in backend.calls[1][0][1]["content"]  # the check read what was said before


async def test_without_the_check_a_maybe_message_goes_straight_to_the_model(memory, tmp_path):
    backend = Refs(say("<pass>"))
    agent = make_agent(memory, tmp_path, backend, models=Models())
    done = (await drive(agent, request(surface="discord", user_id="1", mode="maybe", conversation="discord:channel:9")))[-1]
    assert done["passed"] and backend.refs == ["cloud:big"]
