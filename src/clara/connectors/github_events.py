"""The two GitHub events an automation can wait for, built from what GitHub says: `github.pull_request` and `github.push`.

One place makes them, so that a pull request or a push has the same fields, the same title and the same text whether it was found
by the poll (connectors/github_watch.py, which asks the REST API) or told by the webhook (the adapter below, which reads the body
GitHub posts to an event source of proof `github_hmac`): one automation works with either. The poll's keys are
`owner/repo#12:opened:<time>` and `owner/repo:branch:<sha>`; the webhook's is the delivery id GitHub gives, so a repository is
never watched by both (github_watch.py skips a repository that has a webhook source).

Everything here was written by someone else (a title, a branch, a commit message): it is cut and flattened, never trusted.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..automations.events import SourceEvent
from ..timeutil import iso, parse_iso

MAX_COMMITS = 10  # commits listed in a push event (its count is always given)
SHORT = 300
ZEROS = "0" * 40  # the `before` of a push that made a branch


def short(value: Any, limit: int = SHORT) -> str:
    """One line of someone else's text, cut."""
    return " ".join(str(value or "").split())[:limit]


def _dict(value: Any) -> dict:
    """What a body says is not always an object: anything else is nothing."""
    return value if isinstance(value, dict) else {}


def login_of(user: Any) -> str:
    return str(_dict(user).get("login") or "")


def pull_event(repo: str, pr: dict, action: str, at: str) -> SourceEvent:
    """The event of one pull request that was `action` (opened, merged, closed) at `at` (a key names the moment, so seeing the same one
    again is one event)."""
    number, title, author = pr["number"], short(pr.get("title")), login_of(pr.get("user"))
    head, base = _dict(pr.get("head")), _dict(pr.get("base"))
    return SourceEvent(
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
            "from_fork": str(_dict(head.get("repo")).get("full_name") or "").lower() != repo.lower(),
            "sha": str(head.get("sha") or ""),
            "url": str(pr.get("html_url") or ""),
        },
    )


def pull_moments(pr: dict) -> list[tuple[str, str | None]]:
    """What a pull request went through that is an event: opened, and merged or closed."""
    moments = [("opened", pr.get("created_at"))]
    if pr.get("merged_at"):
        moments.append(("merged", pr["merged_at"]))
    elif pr.get("closed_at"):
        moments.append(("closed", pr["closed_at"]))
    return moments


def pull_events(repo: str, pr: dict, since) -> list[SourceEvent]:
    """The events of one pull request at or after `since`: opened, and merged or closed."""
    return [pull_event(repo, pr, action, at) for action, at in pull_moments(pr) if at and parse_iso(at) >= since]


def push_event(
    repo: str,
    branch: str,
    sha: str,
    old: str | None,
    created: bool,
    forced: bool,
    commits: list[dict],
    occurred_at: str,
) -> SourceEvent:
    """The event of a branch that moved from `old` to `sha`; `commits` are GitHub's REST commits, oldest first."""
    last = commits[-1] if commits else {}
    author = login_of(last.get("author")) or short(_dict(_dict(last.get("commit")).get("author")).get("name"), 100)
    message = short(str(_dict(last.get("commit")).get("message") or "").split("\n", 1)[0], 200)
    return SourceEvent(
        source="github",
        kind="github.push",
        key=f"{repo}:{branch}:{sha}",
        occurred_at=occurred_at,
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
                    "message": short(str(_dict(item.get("commit")).get("message") or "").split("\n", 1)[0], 200),
                }
                for item in commits[-MAX_COMMITS:]
            ],
            "url": str(last.get("html_url") or ""),
        },
    )


# ----------------------------------------------------------------------
# The webhook: what GitHub posts, as the same events
# ----------------------------------------------------------------------
class Ignored(Exception):
    """A delivery that is valid and starts nothing (an action or an event Clara does not turn into an event)."""


class Refused(ValueError):
    """A delivery that is not what it says it is (a body that is not a pull request, a push with no ref): 422."""


def _object(value: Any, what: str) -> dict:
    if not isinstance(value, dict):
        raise Refused(f"{what} is missing or is not an object.")
    return value


def repository_of(payload: dict) -> str:
    """The `owner/name` a delivery says it is about ('' when it says nothing, as a ping of an organisation's hook)."""
    found = payload.get("repository")
    name = found.get("full_name") if isinstance(found, dict) else None
    return name if isinstance(name, str) else ""


def from_webhook(event: str, payload: dict, repo: str, origin: str, delivery: str, now: str) -> SourceEvent:
    """The event of one delivery (`X-GitHub-Event`: `pull_request` or `push`), of the repository `repo`, stored as coming from `origin`
    with the delivery id for its key. Ignored for what the poll would not report either; Refused when the body is not what its event is."""
    if event == "pull_request":
        pr = _object(payload.get("pull_request"), "pull_request")
        number = pr.get("number")
        if not isinstance(number, int) or isinstance(number, bool):
            raise Refused("pull_request.number is missing.")
        action = payload.get("action")
        if action == "opened":
            moment = "opened", pr.get("created_at")
        elif action == "closed":  # merged or not: the poll tells the two apart in the same way
            moment = (
                ("merged", pr.get("merged_at"))
                if pr.get("merged") or pr.get("merged_at")
                else ("closed", pr.get("closed_at"))
            )
        else:
            raise Ignored(f"pull_request.{short(action, 40)}")
        at = moment[1]
        try:
            built = pull_event(repo, pr, moment[0], at if isinstance(at, str) else "")
        except (ValueError, OverflowError, TypeError):
            raise Refused(f"pull_request has no usable time for {moment[0]}.") from None
        return replace(built, source=origin, key=delivery)
    if event == "push":
        ref = payload.get("ref")
        after = payload.get("after")
        if not isinstance(ref, str) or not isinstance(after, str) or not after:
            raise Refused("push has no ref or no after.")
        if not ref.startswith("refs/heads/"):
            raise Ignored("push of a tag")  # the poll looks at branches only
        if payload.get("deleted"):
            raise Ignored("push that deleted a branch")
        raw = payload.get("commits")
        if not isinstance(raw, list):
            raise Refused("push.commits is not a list.")
        head = payload.get("head_commit")
        listed = [item for item in raw if isinstance(item, dict)]
        created = bool(payload.get("created"))
        if created and not listed:
            raise Ignored("push that made a branch with nothing new")  # the poll skips it as well
        if not listed and isinstance(head, dict):
            listed = [head]
        before = payload.get("before")
        old = None if created or not isinstance(before, str) or before in ("", ZEROS) else before
        built = push_event(
            repo,
            ref.removeprefix("refs/heads/"),
            after,
            old,
            created,
            bool(payload.get("forced")) and not created,
            [_rest_commit(item) for item in listed],
            now,
        )
        return replace(built, source=origin, key=delivery)
    raise Ignored(short(event, 40))


def _rest_commit(item: dict) -> dict:
    """A commit of a push body as the REST API shows it, so that `push_event` reads both."""
    author = _dict(item.get("author"))
    return {
        "sha": str(item.get("id") or ""),
        "author": {"login": str(author.get("username") or "")},
        "commit": {"message": str(item.get("message") or ""), "author": {"name": str(author.get("name") or "")}},
        "html_url": str(item.get("url") or ""),
    }
