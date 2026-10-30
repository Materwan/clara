"""Hooks on GitHub: pull requests and pushes found by a poll (GitHub cannot call Clara), stored once, planned atomically; and a
hook's run on GitHub is untrusted and unattended, so whatever it changes there asks first."""

from __future__ import annotations

from datetime import timedelta

import pytest

from clara.connectors.github import GitHub, GitHubError
from clara.settings import SettingsError, load_settings
from clara.timeutil import iso, utcnow
from fakes import calls
from githubworld import REPO, GitHubWorld
from test_triggers import chat_calls, notifications, runs, work


@pytest.fixture
def github(monkeypatch) -> GitHubWorld:
    """GitHub as a stand-in: every client the server makes talks to it."""
    found = GitHubWorld()
    transport = found.transport()
    real = GitHub.__init__

    def init(self, *args, **kwargs):
        kwargs["transport"] = transport
        real(self, *args, **kwargs)

    monkeypatch.setattr(GitHub, "__init__", init)
    return found


def connect_github(services, repo: str = REPO) -> None:
    services.secrets.set("github_token", "ghp_test")
    services.connections.set("github", "Materwan")
    services.connections.add("github", "github_repo", repo, repo)


def hook(services, name: str = "Review", kind: str = "github.pull_request", **fields):
    fields.setdefault("prompt", "Review it.")
    return services.triggers.create(name, kind, **fields)


async def poll(services) -> None:
    await services.connectors.github_watch.poll()


def cursors(services) -> list[tuple[str, str]]:
    return [(row["repo"], row["stream"]) for row in services.db.all("SELECT repo, stream FROM github_cursors ORDER BY stream")]


def stored(services, kind: str) -> list:
    return sorted(services.triggers.recent_events(50, kind), key=lambda event: event.id)


# ----------------------------------------------------------------------
# Pull requests
# ----------------------------------------------------------------------
async def test_the_first_look_notes_where_things_stand_and_a_pull_request_after_it_is_one_event(services, github):
    connect_github(services)
    github.open_pr(1, "Old one", at=-3600)  # there before the hook
    trigger = hook(services)
    await poll(services)  # the baseline: no question asked, nothing replayed
    assert github.requests == [] and runs(services, trigger) == []
    github.open_pr(2, "Fix the README", head="test-pr", body="It has a typo.")
    await poll(services)
    assert runs(services, trigger) == [("queued", 1)]
    [event] = stored(services, "github.pull_request")
    assert (event.source, event.key.startswith(f"{REPO}#2:opened:"), event.actor, event.self_caused) == ("github", True, "alice", False)
    assert event.data["action"] == "opened" and event.data["branch"] == "test-pr" and event.data["base"] == "main"
    assert event.data["snippet"] == "It has a typo." and event.data["from_fork"] is False
    await poll(services)  # nothing changed: GitHub says so, and that costs no rate limit
    assert github.not_modified == 1 and runs(services, trigger) == [("queued", 1)]


async def test_a_pull_request_is_an_event_for_being_opened_and_for_being_merged_or_closed(services, github):
    connect_github(services)
    hook(services, "All", filter=None)
    await poll(services)
    for number, title in ((1, "A"), (2, "B"), (3, "C")):
        github.open_pr(number, title, at=number)
    github.close_pr(2, merged=True, at=6)
    github.close_pr(3, at=7)
    await poll(services)
    got = sorted((e.data["number"], e.data["action"]) for e in stored(services, "github.pull_request"))
    assert got == [(1, "opened"), (2, "merged"), (2, "opened"), (3, "closed"), (3, "opened")]  # merged is not also closed


async def test_a_filter_picks_pull_requests_by_their_fields(services, github):
    connect_github(services)
    only = hook(services, filter={"draft": {"eq": False}, "from_fork": {"eq": False}, "action": "opened", "author": {"not_contains": "bot"}})
    await poll(services)
    github.open_pr(1, "Plain")
    github.open_pr(2, "Draft", draft=True)
    github.open_pr(3, "From a stranger", fork=True)
    github.open_pr(4, "Dependency bump", author="dependabot-bot")
    await poll(services)
    assert len(stored(services, "github.pull_request")) == 4  # all stored,
    assert runs(services, only) == [("queued", 1)]  # one wanted


async def test_the_same_pull_request_event_seen_again_is_one_event(services, github):
    connect_github(services)
    trigger = hook(services)
    watch = services.connectors.github_watch
    await poll(services)
    github.open_pr(1, "Once")
    await poll(services)
    state, _ = watch.cursor(REPO, "pulls")
    watch.save(REPO, "pulls", state, "")  # as if the answer had to be read again (no etag)
    await poll(services)
    assert len(stored(services, "github.pull_request")) == 1 and runs(services, trigger) == [("queued", 1)]


async def test_a_failed_ingest_moves_nothing_so_the_next_poll_finds_the_pull_request_again(services, github, monkeypatch):
    connect_github(services)
    trigger = hook(services)
    watch = services.connectors.github_watch
    await poll(services)
    before = watch.cursor(REPO, "pulls")
    github.open_pr(1, "Found twice")
    real = services.triggers.ingest

    def broken(events, also=None):
        raise RuntimeError("disk full")

    monkeypatch.setattr(services.triggers, "ingest", broken)
    await poll(services)
    assert watch.cursor(REPO, "pulls") == before and runs(services, trigger) == []
    monkeypatch.setattr(services.triggers, "ingest", real)
    await poll(services)
    assert runs(services, trigger) == [("queued", 1)]


def began(services, repo_seconds: int, hook_seconds: int) -> None:
    """As if the repository was added, and the hook made, that long before the poll got round to them."""
    ago = lambda seconds: iso(utcnow() - timedelta(seconds=seconds))  # noqa: E731
    services.db.execute("UPDATE resources SET created_at = ?", (ago(repo_seconds),))
    services.db.execute("UPDATE triggers SET updated_at = ?", (ago(hook_seconds),))


async def test_what_happens_between_adding_the_repository_and_the_first_look_is_not_lost(services, github):
    connect_github(services)
    hook(services, filter=None)
    began(services, repo_seconds=100, hook_seconds=200)  # the repository was added after the hook, 100 s ago
    github.open_pr(1, "Opened before the repository was added", at=-150)
    github.close_pr(1, merged=True, at=-50)  # merged after it: the one thing Clara can know
    await poll(services)  # the first look
    await poll(services)
    assert [(e.data["number"], e.data["action"]) for e in stored(services, "github.pull_request")] == [(1, "merged")]


async def test_a_first_look_never_goes_further_back_than_a_late_event_is_a_backlog(services, github):
    connect_github(services)
    trigger = hook(services)
    began(services, repo_seconds=7 * 86400, hook_seconds=7 * 86400)  # waited for a week, but nobody looked (polling was off)
    github.open_pr(1, "Last week", at=-86400)
    github.open_pr(2, "Three hours ago", at=-3 * 3600)
    github.open_pr(3, "A few minutes ago", at=-300)
    await poll(services)
    await poll(services)
    assert [e.data["number"] for e in stored(services, "github.pull_request")] == [3]
    assert runs(services, trigger) == [("queued", 1)]


async def test_the_connections_say_since_when_each_kind_of_event_is_looked_for(services, github, client):
    connect_github(services)
    async def watching() -> dict:
        return (await client.get("/v1/connections")).json()["github"]["repos"][0]["watching"]

    assert await watching() == {}  # no hook waits: nothing is looked for
    hook(services)
    hook(services, "Pushes", "github.push")
    await poll(services)
    first = await watching()
    assert sorted(first) == ["github.pull_request", "github.push"] and all(first.values())
    github.open_pr(1, "Something")
    await poll(services)  # the place moves, the start does not
    assert await watching() == first


# ----------------------------------------------------------------------
# Pushes
# ----------------------------------------------------------------------
async def test_a_push_is_one_event_with_its_commits_and_a_filter_can_pick_the_branch(services, github):
    connect_github(services)
    on_main = hook(services, "Main", "github.push", filter={"branch": {"eq": "main"}})
    on_feature = hook(services, "Feature", "github.push", filter={"branch": {"starts_with": "feat"}})
    await poll(services)  # the baseline notes the head of each branch
    assert f"GET /repos/{REPO}/branches" in github.requests
    github.push("main", "one")
    github.push("main", "two", "bob")
    github.push("main", "three")
    await poll(services)
    [event] = stored(services, "github.push")
    assert event.key == f"{REPO}:main:{github.branches['main']}" and event.self_caused is False
    assert (event.data["author"], event.data["message"], event.data["commit_count"]) == ("alice", "three", 3)
    assert [c["message"] for c in event.data["commits"]] == ["one", "two", "three"]  # oldest first
    assert event.data["created"] is False and event.data["forced"] is False
    assert runs(services, on_main) == [("queued", 1)] and runs(services, on_feature) == []
    await poll(services)
    assert github.not_modified == 1 and len(stored(services, "github.push")) == 1


async def test_a_new_branch_is_a_push_only_when_it_holds_something_new(services, github):
    connect_github(services)
    trigger = hook(services, kind="github.push")
    await poll(services)
    github.branch("just-a-name")  # points where main does: nothing was pushed
    await poll(services)
    assert runs(services, trigger) == []
    github.push("test-pr", "Add a file")
    await poll(services)
    [event] = stored(services, "github.push")
    assert (event.data["branch"], event.data["created"], event.data["commit_count"]) == ("test-pr", True, 1)
    assert event.title.startswith(f"{REPO} test-pr (new branch): Add a file")


async def test_a_rewritten_branch_is_a_forced_push_even_when_its_old_commit_is_gone(services, github):
    connect_github(services)
    github.push("feature", "first")
    github.push("feature", "second")
    hook(services, kind="github.push")
    await poll(services)
    github.rewrite("feature", "second, reworded")
    await poll(services)
    old = github.branches["feature"]
    github.rewrite("feature", "third")
    del github.commits[old]  # GitHub no longer knows what the branch was: it cannot compare
    await poll(services)
    forced = [(e.data["forced"], e.data["message"]) for e in stored(services, "github.push")]
    assert forced == [(True, "second, reworded"), (True, "third")]  # and the second still says what it was


async def test_a_push_found_after_a_long_stop_is_a_backlog_for_the_hook_and_one_found_at_once_is_not(services, github):
    connect_github(services)
    trigger = hook(services, kind="github.push", catch_up="skip")
    watch = services.connectors.github_watch
    await poll(services)
    state, _ = watch.cursor(REPO, "branches")
    watch.save(REPO, "branches", {**state, "checked": iso(utcnow() - timedelta(hours=3))}, "")  # the last look was long ago
    github.push("main", "while the server was off")
    await poll(services)
    assert runs(services, trigger) == [("skipped", 1)]
    github.push("main", "just now")
    await poll(services)
    assert runs(services, trigger) == [("queued", 1), ("skipped", 1)]


# ----------------------------------------------------------------------
# The poll itself
# ----------------------------------------------------------------------
async def test_the_poll_looks_only_while_a_hook_waits_and_only_at_what_it_waits_for(services, github):
    connect_github(services)
    await poll(services)
    assert github.requests == [] and cursors(services) == []  # nobody listens: no question asked
    pulls = hook(services)
    await poll(services)
    await poll(services)
    assert not [r for r in github.requests if r.endswith("/branches")] and cursors(services) == [(REPO, "pulls")]
    pushes = hook(services, "Pushes", "github.push")
    await poll(services)
    assert cursors(services) == [(REPO, "branches"), (REPO, "pulls")]
    services.triggers.delete(pulls.id)
    await poll(services)
    assert cursors(services) == [(REPO, "branches")]  # the place of a stream nobody waits for is forgotten
    github.push("main", "x")
    services.triggers.delete(pushes.id)
    await poll(services)
    assert cursors(services) == []
    hook(services, "Again")
    await poll(services)
    assert cursors(services) == [(REPO, "pulls")]
    services.connections.delete(services.connections.repo(REPO).id)  # the repository is no longer Clara's
    await poll(services)
    assert cursors(services) == []


async def test_a_repository_that_is_not_clara_s_is_never_looked_at(services, github):
    connect_github(services)
    services.connections.delete(services.connections.repo(REPO).id)
    hook(services, kind="github.push")
    await poll(services)
    assert github.requests == []


async def test_a_refused_token_marks_the_connection_and_raises_the_alert_until_a_poll_works_again(services, github):
    connect_github(services)
    hook(services)
    await poll(services)
    github.refuse = 401
    await poll(services)
    assert "github" in services.alerts.active and services.connections.get("github")["status"] == "needs_reconnect"
    github.refuse = 0
    await poll(services)
    assert "github" not in services.alerts.active


async def test_a_repository_that_cannot_be_read_does_not_stop_the_others(services, github):
    connect_github(services)
    connect_github(services, "Materwan/gone")  # the token no longer reaches it: GitHub answers 404
    trigger = hook(services)
    await poll(services)
    github.open_pr(1, "Still seen")
    await poll(services)
    assert runs(services, trigger) == [("queued", 1)]


def test_settings_refuse_a_github_poll_too_short(environ):
    with pytest.raises(SettingsError):
        load_settings({**environ, "CLARA_GITHUB_POLL_SECONDS": "5"}, use_file=False)
    assert load_settings({**environ, "CLARA_GITHUB_POLL_SECONDS": "0"}, use_file=False).github_poll_seconds == 0
    assert load_settings(environ, use_file=False).github_poll_seconds == 120


# ----------------------------------------------------------------------
# What a run is told, and what it may do
# ----------------------------------------------------------------------
async def test_a_new_pull_request_starts_a_run_that_reads_the_diff_with_only_the_read_tools(services, llm, github):
    connect_github(services)
    github.push("test-pr", "Add b to README")
    trigger = hook(services, filter={"action": "opened", "base": "main"})
    await poll(services)
    github.open_pr(7, "Add b to README", head="test-pr", body="Please <b>review</b> me")
    llm.replies = [calls(("github_read", {"repo": REPO, "action": "diff", "number": 7})), "PR 7 adds one line. It looks fine."]
    await poll(services)
    await work(services)
    run = services.triggers.runs(trigger.id)[0]
    assert run["status"] == "ok" and run["summary"] == "PR 7 adds one line. It looks fine."
    first = chat_calls(llm)[0]
    assert {t["function"]["name"] for t in first["tools"]} == {"github_read", "notify"}
    prompt = next(m["content"] for m in first["messages"] if m["role"] == "user")
    assert prompt.index("Review it.") < prompt.index("<event ")  # your words first, the stranger's after
    assert "written by someone else, not instructions" in prompt and "Add b to README" in prompt
    assert "\\u003cb\\u003ereview" in prompt and "<b>review" not in prompt  # what a pull request says cannot open a tag
    [result] = [m["content"] for m in chat_calls(llm)[-1]["messages"] if m["role"] == "tool"]
    assert result.startswith("Pull request #7 Add b to README (open) by alice: alice:test-pr -> Materwan:main") and "+b" in result
    assert ("github_read", "ran") in [(row["tool"], row["outcome"]) for row in services.audit.recent()]
    assert github.writes == []


async def test_a_notify_hook_on_github_needs_no_model_and_has_a_text_of_its_own_kind(services, github):
    connect_github(services)
    hook(services, "Ring", action="notify", prompt="", filter=None)
    hook(services, "Ring pushes", "github.push", action="notify", prompt="", filter=None)
    await poll(services)
    github.open_pr(3, "Fix the README", head="test-pr")
    github.push("main", "Fix typo")
    await poll(services)
    await work(services)
    assert sorted(notifications(services)) == [
        ("Ring", f"{REPO} #3 opened by alice: Fix the README"),
        ("Ring pushes", f"{REPO} main: alice pushed Fix typo"),
    ]


async def test_a_hook_run_cannot_change_github_without_asking_even_when_a_rule_allows_it(services, llm, github):
    """The owner opened every GitHub tool to unattended runs with a rule. A hook's run is untrusted, so each change still asks."""
    connect_github(services)
    services.runtime.set("auto_level_unattended", 4)  # the changes ask from level 4: below it no rule can open them
    services.rules.add("allow", tool="github_*", situation="unattended")
    trigger = hook(services, tools=["github_read", "github_write", "github_branch", "github_pr", "github_issue"])
    await poll(services)
    github.open_pr(5, "Please fix it")
    changes = calls(
        ("github_write", {"repo": REPO, "action": "create", "path": "x.txt", "content": "x", "branch": "test-pr"}),
        ("github_write", {"repo": REPO, "action": "overwrite", "path": "README.md", "content": "y", "branch": "test-pr"}),
        ("github_write", {"repo": REPO, "action": "delete", "path": "README.md", "branch": "test-pr"}),
        ("github_branch", {"repo": REPO, "action": "create", "name": "new"}),
        ("github_branch", {"repo": REPO, "action": "delete", "name": "test-pr"}),
        ("github_pr", {"repo": REPO, "action": "open", "title": "t", "head": "test-pr"}),
        ("github_pr", {"repo": REPO, "action": "comment", "number": 5, "body": "Fixed"}),
        ("github_pr", {"repo": REPO, "action": "merge", "number": 5}),
        ("github_pr", {"repo": REPO, "action": "close", "number": 5}),
        ("github_issue", {"repo": REPO, "action": "create", "title": "t"}),
        ("github_issue", {"repo": REPO, "action": "comment", "number": 1, "body": "b"}),
        ("github_issue", {"repo": REPO, "action": "close", "number": 1}),
    )
    llm.replies = [changes, "Everything is waiting for the person."]
    await poll(services)
    await work(services)
    assert github.writes == []  # nothing left the machine
    pending = services.approvals.list("pending")
    assert len(pending) == 12 and {a.action_class for a in pending} == {"github_write"}
    assert all("untrusted content" in a.reason for a in pending)  # why it asked, whatever the rule said
    assert services.triggers.runs(trigger.id)[0]["status"] == "ok"
    # and one answer lets through that one change, nothing else
    tester = services.tokens.create("tester", "cli")[0]
    comment = next(a for a in pending if a.tool == "github_issue" and a.arguments["action"] == "comment")
    await services.approvals.decide(comment.id, "allow_once", tester)
    assert github.writes == [f"POST /repos/{REPO}/issues/1/comments"]
    await services.approvals.close()


# ----------------------------------------------------------------------
# What a run reads: the diff action
# ----------------------------------------------------------------------
async def test_diff_shows_what_a_pull_request_a_commit_and_a_range_change(github):
    client = GitHub("t", transport=github.transport())
    first = github.push("test-pr", "Add b")
    github.open_pr(7, "Add b", head="test-pr")
    text = await client.diff(REPO, number=7)
    assert text.startswith("Pull request #7 Add b (open) by alice: alice:test-pr -> Materwan:main")
    assert "--- README.md (modified, +1 -0)" in text and "+b" in text
    assert "Commit " + first in await client.diff(REPO, ref=first)
    second = github.push("test-pr", "Add c")
    ranged = await client.diff(REPO, ref=f"{first}...{second}")
    assert ranged.startswith(f"Changes {first}...{second}: 1 file(s)")
    assert "1 more file" in await client.diff(REPO, number=7, max_chars=60)  # cut, and says so


@pytest.mark.parametrize("ref", ["../../user", "a...b...c", "x y", "...", "a/../b"])
async def test_diff_refuses_a_reference_that_is_not_a_commit_or_a_range(github, ref):
    with pytest.raises(GitHubError):
        await GitHub("t", transport=github.transport()).diff(REPO, ref=ref)
    assert github.requests == []


async def test_diff_needs_something_to_show(github):
    with pytest.raises(GitHubError, match="number"):
        await GitHub("t", transport=github.transport()).diff(REPO)
