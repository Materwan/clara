"""New activity on GitHub, for hooks: a poll of each repository you added, on its own loop (ops/upkeep.py). GitHub cannot call
Clara (she is on your tailnet only), so she looks. Two streams per repository, each only while a hook waits for its kind:

* **pulls** (`github.pull_request`): the pull requests most recently updated; a pull request opened, merged or closed since
  the place the poll had reached is an event. Nothing is remembered per pull request: the times GitHub gives are enough.
* **branches** (`github.push`): the head of each branch against the head seen last time; a branch that moved, or is new, is
  an event with its commits. GitHub does not say when a push was made, so an event is dated from the last look: a push found
  after a long gap (the server was off) is a *backlog* for the hook's `catch_up`, exactly like a mail found late.

Like the mail poll: the place moves in the same transaction that stores the events and plans the runs (`Triggers.ingest`), so a
crash loses none and repeats none; the first look only notes where things stand (for pull requests, as of the moment the
repository began to be waited for, which can be a poll earlier than the look), so a new hook never replays history; and with
no hook waiting the place is forgotten. Each answer carries an etag, and "nothing changed" costs no rate limit.

Nothing here is Clara's own: the token is yours, so her commits and yours look the same and no event is `self_caused`. What
keeps a hook from answering itself is the gate: a hook's run is tainted, so every change it makes on GitHub asks you first.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from ..timeutil import iso, now_iso, parse_iso, utcnow
from ..triggers.events import SourceEvent
from ..triggers.service import STALE_AFTER
from .github import PULLS_PAGE, GitHub, GitHubError

if TYPE_CHECKING:
    from ..services import Services

log = logging.getLogger(__name__)

STREAMS = {"pulls": "github.pull_request", "branches": "github.push"}
MAX_PAGES = 3  # pages of PULLS_PAGE pull requests looked at in one poll
MAX_COMMITS = 10  # commits listed in a push event (its count is always given)
SKEW = timedelta(seconds=10)  # GitHub's clock is not ours, and it rounds its times to the second
SHORT = 300


def short(value: Any, limit: int = SHORT) -> str:
    """One line of someone else's text, cut."""
    return " ".join(str(value or "").split())[:limit]


def login_of(user: Any) -> str:
    return str((user or {}).get("login") or "")


def pull_events(repo: str, pr: dict, since: datetime) -> list[SourceEvent]:
    """The events of one pull request at or after `since`: opened, and merged or closed. (A key names the moment, so seeing
    the same one again, as the instant `since` itself is, is one event.)"""
    moments = [("opened", pr.get("created_at"))]
    if pr.get("merged_at"):
        moments.append(("merged", pr["merged_at"]))
    elif pr.get("closed_at"):
        moments.append(("closed", pr["closed_at"]))
    number, title, author = pr["number"], short(pr.get("title")), login_of(pr.get("user"))
    head, base = pr.get("head") or {}, pr.get("base") or {}
    found = []
    for action, at in moments:
        if not at or parse_iso(at) < since:
            continue
        found.append(
            SourceEvent(
                source="github",
                kind="github.pull_request",
                key=f"{repo}#{number}:{action}:{at}",
                occurred_at=iso(parse_iso(at)),
                actor=author,
                title=f"{repo} #{number} {action}: {title}"[:SHORT],
                data={
                    "repo": repo,
                    "number": number,
                    "action": action,
                    "title": title,
                    "author": author,
                    "branch": short(head.get("ref"), 200),
                    "base": short(base.get("ref"), 200),
                    "snippet": short(pr.get("body")),
                    "draft": bool(pr.get("draft")),
                    "from_fork": str((head.get("repo") or {}).get("full_name") or "").lower() != repo.lower(),
                    "sha": str(head.get("sha") or ""),
                    "url": str(pr.get("html_url") or ""),
                },
            )
        )
    return found


class GitHubWatch:
    def __init__(self, services: Services):
        self.s = services
        self._last_error: dict[str, str] = {}  # what was last logged per repository and stream: an outage is one line

    # ------------------------------------------------------------------
    def cursor(self, repo: str, stream: str) -> tuple[dict, str] | None:
        row = self.s.db.one("SELECT state, etag FROM github_cursors WHERE repo = ? AND stream = ?", (repo, stream))
        return (json.loads(row["state"]), row["etag"]) if row else None

    def save(self, repo: str, stream: str, state: dict, etag: str) -> None:
        self.s.db.execute(
            "INSERT INTO github_cursors (repo, stream, state, etag, updated_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (repo, stream) DO UPDATE SET state = excluded.state, etag = excluded.etag,"
            " updated_at = excluded.updated_at",
            (repo, stream, json.dumps(state), etag, now_iso()),
        )

    def keep(self, repos: list[str], streams: list[str]) -> None:
        """Forget the place of a repository that was removed and of a stream nobody waits for: the next listener starts from
        then, not from an old place."""
        for row in self.s.db.all("SELECT repo, stream FROM github_cursors"):
            if row["repo"] not in repos or row["stream"] not in streams:
                self.s.db.execute(
                    "DELETE FROM github_cursors WHERE repo = ? AND stream = ?", (row["repo"], row["stream"])
                )

    # ------------------------------------------------------------------
    async def poll(self) -> None:
        connectors = self.s.connectors
        client = connectors.github_client() if connectors is not None else None
        triggers = self.s.triggers
        streams = [stream for stream, kind in STREAMS.items() if triggers is not None and triggers.wants(kind)]
        repos = [resource.locator for resource in self.s.connections.resources("github_repo")] if client else []
        self.keep(repos, streams)
        if client is None or not streams:
            return
        reached = False
        for repo in repos:
            for stream in streams:
                try:
                    await self._poll(client, repo, stream)
                    reached = True
                    self._last_error.pop(f"{repo} {stream}", None)
                except GitHubError as error:
                    connectors.github_failed(error)
                    if error.reconnect:
                        return  # the token is refused: every other call would be too
                    if self._last_error.get(f"{repo} {stream}") != str(error):
                        self._last_error[f"{repo} {stream}"] = str(error)
                        log.warning("github %s (%s): %s", repo, stream, error)
                except Exception:
                    log.exception("github %s (%s): the poll failed", repo, stream)
        if reached:
            self.s.alerts.clear("github")

    async def _poll(self, client: GitHub, repo: str, stream: str) -> None:
        found = self.cursor(repo, stream)
        if stream == "pulls":
            await self._pulls(client, repo, found)
        else:
            await self._branches(client, repo, found)

    def _hand_over(self, events: list[SourceEvent], move) -> None:
        if events:
            self.s.triggers.ingest(events, also=move)
        else:
            move()

    # ------------------------------------------------------------------
    def _began(self, repo: str, stream: str, now: datetime) -> datetime:
        """When a repository began to be waited for in a stream: the later of its being added and a hook's being made for the
        kind. A first look starts from there, not from the moment the poll got round to it, so what happened in between is not
        lost. Never further back than STALE_AFTER, so a server that was off (or had polling off) replays no history."""
        added = self.s.connections.repo(repo)
        waiting = self.s.triggers.waiting_since(STREAMS[stream])
        moments = [parse_iso(at) for at in (added and added.created_at, waiting) if at]
        return min(max([now - STALE_AFTER, *moments]), now)

    def watching(self, repo: str) -> dict[str, str]:
        """Since when each kind of event is looked for in a repository (a place noted before this was recorded says nothing)."""
        found = {}
        for stream, kind in STREAMS.items():
            cursor = self.cursor(repo, stream)
            if cursor and cursor[0].get("from"):
                found[kind] = cursor[0]["from"]
        return found

    async def _pulls(self, client: GitHub, repo: str, found: tuple[dict, str] | None) -> None:
        now = utcnow()
        if found is None:
            began = self._began(repo, "pulls", now)
            self.save(repo, "pulls", {"since": iso(began - SKEW), "from": iso(began)}, "")
            return
        state, etag = found
        since = parse_iso(state["since"])
        newest, new_etag = since, etag
        events: list[SourceEvent] = []
        for page in range(1, MAX_PAGES + 1):
            items, tag = await client.pulls_updated(repo, etag if page == 1 else "", page)
            if page == 1:
                new_etag = tag
            if items is None:
                break  # nothing changed since the etag
            for pr in items:
                newest = max(newest, parse_iso(pr["updated_at"]))
                events += pull_events(repo, pr, since)
            if len(items) < PULLS_PAGE or parse_iso(items[-1]["updated_at"]) < since:
                break  # the pull requests this poll cares about are over
        else:
            log.warning(
                "github %s: more than %d pull requests changed at once; the oldest were not looked at",
                repo,
                MAX_PAGES * PULLS_PAGE,
            )

        def move() -> None:
            self.save(repo, "pulls", {**state, "since": iso(newest)}, new_etag)

        self._hand_over(events, move)

    # ------------------------------------------------------------------
    async def _branches(self, client: GitHub, repo: str, found: tuple[dict, str] | None) -> None:
        now = utcnow()
        if found is None:
            heads, tag = await client.branch_heads(repo)
            # the heads before this look are unknown, so a push cannot be told from before: it starts now
            self.save(repo, "branches", {"heads": heads, "checked": iso(now), "from": iso(now)}, tag)
            return
        state, etag = found
        heads, tag = await client.branch_heads(repo, etag)
        if heads is None:
            self.save(repo, "branches", {**state, "checked": iso(now)}, etag)
            return
        before: dict[str, str] = state["heads"]
        events = []
        for branch, sha in heads.items():
            if before.get(branch) == sha:
                continue
            event = await self._push(client, repo, branch, sha, before.get(branch), state["checked"], heads)
            if event is not None:
                events.append(event)

        def move() -> None:
            self.save(repo, "branches", {**state, "heads": heads, "checked": iso(now)}, tag)

        self._hand_over(events, move)

    @staticmethod
    def _tolerate(error: GitHubError) -> None:
        """A commit or a comparison GitHub does not know (or cannot make) must not stop the push being reported; a refused
        token, a rate limit or an outage stops the poll, which tries again from the old place."""
        if error.reconnect or error.status not in (404, 409, 422):
            raise error

    async def _push(
        self, client: GitHub, repo: str, branch: str, sha: str, old: str | None, checked: str, heads: dict[str, str]
    ) -> SourceEvent | None:
        """The event of a branch that moved from `old` to `sha`, or None for a new branch that holds nothing new."""
        created = old is None
        if created and any(other != branch and head == sha for other, head in heads.items()):
            return None  # the same commit as another branch: a name was added, nothing was pushed
        commits: list[dict] = []
        status = "unknown"
        # If the old head is gone (history was rewritten) or GitHub cannot compare, the event says only what is certain.
        try:
            base = heads.get(await client.default_branch(repo)) if created else old
            if base:
                found = await client.compare(repo, base, sha)
                status, commits = found.get("status", "unknown"), list(found.get("commits") or [])
        except GitHubError as error:
            self._tolerate(error)
        if not commits:
            try:
                commits = [await client.commit(repo, sha)]
            except GitHubError as error:
                self._tolerate(error)
        forced = not created and status in ("diverged", "behind", "unknown")
        last = commits[-1] if commits else {}
        author = login_of(last.get("author")) or short((last.get("commit") or {}).get("author", {}).get("name"), 100)
        message = short(((last.get("commit") or {}).get("message") or "").split("\n", 1)[0], 200)
        return SourceEvent(
            source="github",
            kind="github.push",
            key=f"{repo}:{branch}:{sha}",
            occurred_at=checked,  # it happened after the last look; if that was long ago, it is a backlog
            actor=author,
            title=f"{repo} {branch}{' (new branch)' if created else ''}: {message}"[:SHORT],
            data={
                "repo": repo,
                "branch": short(branch, 200),
                "sha": sha,
                "before": old or "",
                "author": author,
                "message": message,
                "created": created,
                "forced": forced,
                "commit_count": len(commits),
                "commits": [
                    {
                        "sha": str(item.get("sha") or "")[:7],
                        "author": login_of(item.get("author")),
                        "message": short(((item.get("commit") or {}).get("message") or "").split("\n", 1)[0], 200),
                    }
                    for item in commits[-MAX_COMMITS:]
                ],
                "url": str(last.get("html_url") or ""),
            },
        )
