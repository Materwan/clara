"""GitHub, live, through its REST API, with your fine-grained token (secret `github_token`).

Clara reaches only the repositories you added on the Connections page. A change is a real commit, branch, pull
request or issue made with your token; there is no force-push and nothing that rewrites history. Every change goes
through the gate, and by default every one asks (class github_write).
"""

from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import quote

import httpx

from ..knowledge.ingest import IngestError, extract
from ..knowledge.projects import read_lines

API = "https://api.github.com"
TIMEOUT = httpx.Timeout(20.0, read=60.0)
MAX_FILE_BYTES = 30_000_000
MAX_BODY = 20_000
LIST_LIMIT = 300
SEARCH_LIMIT = 60
REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_BRANCH = re.compile(r"^[^\s~^:?*\[\\]{1,200}$")


class GitHubError(ValueError):
    def __init__(self, message: str, reconnect: bool = False):
        super().__init__(message)
        self.reconnect = reconnect


def text(value: Any, what: str, limit: int = 300) -> str:
    found = str(value or "").strip()
    if not found:
        raise GitHubError(f"{what} is needed.")
    if len(found) > limit:
        raise GitHubError(f"{what} is too long.")
    return found


def branch_name(value: Any, what: str = "branch") -> str:
    name = text(value, what, 200)
    if not _BRANCH.match(name) or name.startswith("/") or name.endswith("/") or ".." in name:
        raise GitHubError(f"{name!r} is not a valid {what} name.")
    return name


def repo_path(value: Any) -> str:
    parts = [part for part in str(value or "").replace("\\", "/").split("/") if part not in ("", ".")]
    if ".." in parts:
        raise GitHubError("A path may not go up (..).")
    return "/".join(parts)


class GitHub:
    def __init__(self, token: str, transport: httpx.AsyncBaseTransport | None = None):
        self.token = token
        self._transport = transport

    async def request(self, method: str, url: str, *, accept: str = "application/vnd.github+json", **options: Any) -> httpx.Response:
        headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "clara",
                   "Authorization": f"Bearer {self.token}"}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport, follow_redirects=True) as http:
                response = await http.request(method, f"{API}{url}", headers=headers, **options)
        except httpx.HTTPError as error:
            raise GitHubError(f"GitHub could not be reached: {error}") from None
        if response.is_error:
            raise self.problem(response)
        return response

    @staticmethod
    def problem(response: httpx.Response) -> GitHubError:
        status = response.status_code
        try:
            detail = response.json().get("message", "")
        except ValueError:
            detail = response.reason_phrase
        if status == 401:
            return GitHubError("GitHub refused the token (expired or revoked): set it again on the Connections page.", True)
        if status in (403, 429) and response.headers.get("x-ratelimit-remaining") == "0":
            return GitHubError("GitHub says too many requests for now: try again later.")
        if status == 403:
            return GitHubError(f"The token may not do that ({detail}): its permissions may be too small.")
        if status == 404:
            return GitHubError("GitHub cannot find that (or the token has no access to it).")
        if status == 409:
            return GitHubError(f"GitHub refused: {detail} (the file changed meanwhile? read it again).")
        if status == 422:
            return GitHubError(f"GitHub refused it: {detail}")
        return GitHubError(f"GitHub answered HTTP {status}: {detail}")

    async def get_json(self, url: str, **options: Any) -> Any:
        return (await self.request("GET", url, **options)).json()

    async def whoami(self) -> str:
        return str((await self.get_json("/user")).get("login") or "")

    async def default_branch(self, repo: str) -> str:
        about = await self.get_json(f"/repos/{repo}")
        return about.get("default_branch") or "main"

    @staticmethod
    def contents_url(repo: str, path: str) -> str:
        return f"/repos/{repo}/contents/{quote(path)}" if path else f"/repos/{repo}/contents"

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    async def listing(self, repo: str, path: str, branch: str = "") -> str:
        found = await self.get_json(self.contents_url(repo, repo_path(path)), params={"ref": branch} if branch else {})
        if not isinstance(found, list):
            raise GitHubError(f"{path or '/'} is a file, not a folder.")
        found.sort(key=lambda item: (item.get("type") != "dir", str(item.get("name", "")).lower()))
        lines = [f"{item['name']}/" if item.get("type") == "dir" else f"{item['name']} ({item.get('size', 0):,} bytes)"
                 for item in found[:LIST_LIMIT]]
        if len(found) > LIST_LIMIT:
            lines.append(f"[{len(found) - LIST_LIMIT} more]")
        return "\n".join(lines) or "(empty)"

    async def read(self, repo: str, path: str, branch: str = "", start: int = 1, end: int | None = None,
                   max_chars: int = 20_000) -> str:
        path = repo_path(path)
        if not path:
            raise GitHubError("Give the path of a file.")
        response = await self.request("GET", self.contents_url(repo, path), accept="application/vnd.github.raw+json",
                                      params={"ref": branch} if branch else {})
        if response.headers.get("content-type", "").startswith("application/json"):
            raise GitHubError(f"{path} is a folder: list it.")
        if len(response.content) > MAX_FILE_BYTES:
            raise GitHubError(f"{path} is too big to read.")
        try:
            found = extract(path, response.content)
        except IngestError as error:
            raise GitHubError(f"{path}: {error}.") from None
        return read_lines(path, found.text, start, end, max_chars)

    async def search(self, repo: str, query: str, path: str = "") -> str:
        query = text(query, "query", 256)
        folder = repo_path(path)
        q = f"{query} repo:{repo}" + (f" path:{folder}" if folder else "")
        response = await self.request("GET", "/search/code", accept="application/vnd.github.text-match+json",
                                      params={"q": q, "per_page": 30})
        items = response.json().get("items", [])
        if not items:
            return f"No match for {query!r} (GitHub searches the default branch only)."
        lines = []
        for item in items[:SEARCH_LIMIT]:
            fragment = next((m.get("fragment", "") for m in item.get("text_matches", []) if m.get("fragment")), "")
            first = next((line.strip() for line in fragment.splitlines() if query.lower() in line.lower()), "")
            lines.append(f"{item['path']}: {first[:200]}" if first else item["path"])
        return f"Matches in {len(items)} file(s) (default branch):\n" + "\n".join(lines)

    async def branches(self, repo: str) -> str:
        found = await self.get_json(f"/repos/{repo}/branches", params={"per_page": 100})
        return "\n".join(item["name"] for item in found[:LIST_LIMIT]) or "(no branch)"

    async def issues(self, repo: str, pulls: bool) -> str:
        if pulls:
            found = await self.get_json(f"/repos/{repo}/pulls", params={"state": "open", "per_page": 30})
            return "\n".join(f"#{p['number']} {p['title']} ({p['head']['ref']} -> {p['base']['ref']})" for p in found) \
                or "No open pull request."
        found = await self.get_json(f"/repos/{repo}/issues", params={"state": "open", "per_page": 30})
        return "\n".join(f"#{i['number']} {i['title']}" for i in found if "pull_request" not in i) or "No open issue."

    async def thread(self, repo: str, number: int) -> str:
        issue = await self.get_json(f"/repos/{repo}/issues/{number}")
        comments = await self.get_json(f"/repos/{repo}/issues/{number}/comments", params={"per_page": 50})
        lines = [f"#{number} {issue.get('title')} ({issue.get('state')}) by {issue.get('user', {}).get('login')}",
                 str(issue.get("body") or "")[:MAX_BODY]]
        for comment in comments:
            lines.append(f"\n--- {comment.get('user', {}).get('login')} ({comment.get('created_at', '')[:10]}):\n"
                         f"{str(comment.get('body') or '')[:4000]}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Changing
    # ------------------------------------------------------------------
    async def existing(self, repo: str, path: str, branch: str) -> dict | None:
        try:
            found = await self.get_json(self.contents_url(repo, path), params={"ref": branch})
        except GitHubError as error:
            if "cannot find" in str(error):
                return None
            raise
        if isinstance(found, list):
            raise GitHubError(f"{path} is a folder.")
        return found

    async def write(self, repo: str, path: str, content: str, mode: str, branch: str, message: str) -> str:
        path = repo_path(path)
        if not path:
            raise GitHubError("Give the path of the file to write.")
        branch = branch_name(branch) if branch else await self.default_branch(repo)
        current = await self.existing(repo, path, branch)
        if mode == "create" and current is not None:
            raise GitHubError(f"{path} already exists on {branch}: use overwrite or append.")
        if mode == "append" and current is not None:
            old = await self.request("GET", self.contents_url(repo, path), accept="application/vnd.github.raw+json",
                                     params={"ref": branch})
            content = old.text + content
        body: dict[str, Any] = {"message": text(message or f"Clara: {mode} {path}", "message"),
                                "content": base64.b64encode(content.encode("utf-8")).decode(), "branch": branch}
        if current is not None:
            body["sha"] = current["sha"]
        response = await self.request("PUT", self.contents_url(repo, path), json=body)
        commit = response.json().get("commit", {}).get("sha", "")[:7]
        return f"{'Updated' if current is not None else 'Created'} {path} on {branch} (commit {commit})."

    async def delete(self, repo: str, path: str, branch: str, message: str) -> str:
        path = repo_path(path)
        branch = branch_name(branch) if branch else await self.default_branch(repo)
        current = await self.existing(repo, path, branch)
        if current is None:
            raise GitHubError(f"No such file on {branch}: {path}.")
        response = await self.request("DELETE", self.contents_url(repo, path),
                                      json={"message": text(message or f"Clara: delete {path}", "message"),
                                            "sha": current["sha"], "branch": branch})
        return f"Deleted {path} from {branch} (commit {response.json().get('commit', {}).get('sha', '')[:7]})."

    async def branch(self, repo: str, action: str, name: str, source: str = "") -> str:
        name = branch_name(name)
        if action == "create":
            base = branch_name(source) if source else await self.default_branch(repo)
            head = await self.get_json(f"/repos/{repo}/git/ref/heads/{quote(base, safe='/')}")
            await self.request("POST", f"/repos/{repo}/git/refs", json={"ref": f"refs/heads/{name}", "sha": head["object"]["sha"]})
            return f"Created the branch {name} from {base}."
        if action == "delete":
            if name == await self.default_branch(repo):
                raise GitHubError("The default branch cannot be deleted.")
            await self.request("DELETE", f"/repos/{repo}/git/refs/heads/{quote(name, safe='/')}")
            return f"Deleted the branch {name}."
        raise GitHubError("action must be create or delete.")

    async def pull_request(self, repo: str, action: str, args: dict) -> str:
        if action == "open":
            body = {"title": text(args.get("title"), "title"), "head": branch_name(args.get("head"), "head branch"),
                    "base": branch_name(args["base"]) if args.get("base") else await self.default_branch(repo),
                    "body": str(args.get("body") or "")[:MAX_BODY]}
            made = (await self.request("POST", f"/repos/{repo}/pulls", json=body)).json()
            return f"Opened pull request #{made['number']}: {made['html_url']}"
        number = int(args.get("number") or 0)
        if number <= 0:
            raise GitHubError("number is needed.")
        if action == "comment":
            await self.request("POST", f"/repos/{repo}/issues/{number}/comments",
                               json={"body": text(args.get("body"), "body", MAX_BODY)})
            return f"Commented on #{number}."
        if action == "merge":
            await self.request("PUT", f"/repos/{repo}/pulls/{number}/merge", json={})
            return f"Merged pull request #{number}."
        if action == "close":
            await self.request("PATCH", f"/repos/{repo}/pulls/{number}", json={"state": "closed"})
            return f"Closed pull request #{number}."
        raise GitHubError("action must be open, comment, merge or close.")

    async def issue(self, repo: str, action: str, args: dict) -> str:
        if action == "create":
            body = {"title": text(args.get("title"), "title"), "body": str(args.get("body") or "")[:MAX_BODY]}
            made = (await self.request("POST", f"/repos/{repo}/issues", json=body)).json()
            return f"Opened issue #{made['number']}: {made['html_url']}"
        number = int(args.get("number") or 0)
        if number <= 0:
            raise GitHubError("number is needed.")
        if action == "comment":
            await self.request("POST", f"/repos/{repo}/issues/{number}/comments",
                               json={"body": text(args.get("body"), "body", MAX_BODY)})
            return f"Commented on #{number}."
        if action == "close":
            await self.request("PATCH", f"/repos/{repo}/issues/{number}", json={"state": "closed"})
            return f"Closed issue #{number}."
        raise GitHubError("action must be create, comment or close.")
