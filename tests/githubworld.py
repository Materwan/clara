"""An in-memory stand-in for GitHub's REST API, for httpx.MockTransport.

It answers the way the real service is documented to, closely enough to run the real client: lists with etags (and 304 when
nothing changed), pull requests of every state, branches, commits, comparisons (ahead, behind, diverged, unknown), and the
files of a pull request. It records what it was asked, and above all every write, so a test can say nothing was written.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import httpx

REPO = "Materwan/test"


def stamp(seconds: float = 0) -> str:
    """A GitHub time (UTC, to the second, with a Z) this many seconds from now."""
    return (datetime.now(UTC) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


class GitHubWorld:
    def __init__(self, repo: str = REPO) -> None:
        self.repo = repo
        self.default = "main"
        self.pulls: dict[int, dict] = {}
        self.commits: dict[str, dict] = {}
        self.branches: dict[str, str] = {}
        self.versions = {"pulls": 0, "branches": 0}  # an etag changes when its list does
        self.requests: list[str] = []
        self.not_modified = 0
        self.writes: list[str] = []
        self.refuse = 0  # answer every read with this status (401: the token is refused)
        self.commit("main", "Initial commit", "Materwan")

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # ------------------------------------------------------------------
    # Making things happen
    # ------------------------------------------------------------------
    def commit(self, branch: str, message: str, author: str = "alice", parent: str | None = None) -> str:
        sha = f"{len(self.commits) + 1:040x}"
        parents = [parent] if parent else ([self.branches[branch]] if branch in self.branches else [])
        self.commits[sha] = {
            "sha": sha,
            "html_url": f"https://github.com/{self.repo}/commit/{sha}",
            "parents": parents,
            "author": {"login": author},
            "commit": {"message": message, "author": {"name": author}},
            "files": [
                {"filename": "README.md", "status": "modified", "additions": 1, "deletions": 0, "patch": "@@ -1 +1,2 @@\n a\n+b"}
            ],
        }
        self.branches[branch] = sha
        self.versions["branches"] += 1
        return sha

    def push(self, branch: str, message: str, author: str = "alice") -> str:
        """A commit on a branch (a new one starts where the default branch is)."""
        if branch not in self.branches:
            self.branches[branch] = self.branches[self.default]
        return self.commit(branch, message, author)

    def branch(self, name: str) -> None:
        """A branch made without a commit: it points where the default branch does."""
        self.branches[name] = self.branches[self.default]
        self.versions["branches"] += 1

    def rewrite(self, branch: str, message: str) -> str:
        """A force push: the new head does not descend from the old one."""
        old = self.commits[self.branches[branch]]
        return self.commit(branch, message, parent=old["parents"][0] if old["parents"] else None)

    def open_pr(self, number: int, title: str, author: str = "alice", head: str = "feature", body: str = "", *, draft: bool = False,
                fork: bool = False, at: float = 5) -> None:
        self.pulls[number] = {
            "number": number, "title": title, "state": "open", "user": {"login": author}, "body": body, "draft": draft,
            "created_at": stamp(at), "updated_at": stamp(at), "closed_at": None, "merged_at": None,
            "html_url": f"https://github.com/{self.repo}/pull/{number}",
            "head": {"ref": head, "label": f"{author}:{head}", "sha": self.branches.get(head, "0" * 40),
                     "repo": {"full_name": f"{author}/test" if fork else self.repo}},
            "base": {"ref": self.default, "label": f"Materwan:{self.default}"},
        }
        self.versions["pulls"] += 1

    def close_pr(self, number: int, *, merged: bool = False, at: float = 10) -> None:
        pr = self.pulls[number]
        pr.update(state="closed", closed_at=stamp(at), updated_at=stamp(at))
        if merged:
            pr["merged_at"] = stamp(at)
        self.versions["pulls"] += 1

    # ------------------------------------------------------------------
    def history(self, sha: str) -> list[str]:
        seen, stack = [], [sha]
        while stack:
            found = stack.pop()
            if found in self.commits and found not in seen:
                seen.append(found)
                stack += self.commits[found]["parents"]
        return seen

    @staticmethod
    def bare(commit: dict) -> dict:
        return {key: value for key, value in commit.items() if key not in ("files", "parents")}

    # ------------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        path, query = request.url.path, dict(request.url.params)
        self.requests.append(f"{request.method} {path}")
        if request.method != "GET":
            self.writes.append(f"{request.method} {path}")
            return httpx.Response(201, json={"number": 99, "html_url": "https://github.com/x", "commit": {"sha": "0" * 40}})
        if self.refuse:
            return httpx.Response(self.refuse, json={"message": "Bad credentials"})
        if path == "/user":
            return httpx.Response(200, json={"login": "Materwan"})
        found = re.match(r"^/repos/([^/]+/[^/]+)(/.*)?$", path)
        if not found or found.group(1) != self.repo:
            return httpx.Response(404, json={"message": "Not Found"})
        rest = found.group(2) or ""
        if rest == "":
            return httpx.Response(200, json={"default_branch": self.default})
        if rest == "/pulls":
            return self.listing(request, "pulls", self.pull_list(query))
        if rest == "/branches":
            return self.listing(
                request, "branches", [{"name": name, "commit": {"sha": sha}} for name, sha in self.branches.items()]
            )
        if match := re.match(r"^/pulls/(\d+)(/files)?$", rest):
            pr = self.pulls.get(int(match.group(1)))
            if pr is None:
                return httpx.Response(404, json={"message": "Not Found"})
            if match.group(2):
                head = self.commits.get(self.branches.get(pr["head"]["ref"], ""), {})
                return httpx.Response(200, json=head.get("files", []))
            return httpx.Response(200, json=pr)
        if match := re.match(r"^/commits/(.+)$", rest):
            sha = self.branches.get(match.group(1), match.group(1))
            if sha not in self.commits:
                return httpx.Response(404, json={"message": "No commit found"})
            return httpx.Response(200, json={**self.bare(self.commits[sha]), "files": self.commits[sha]["files"]})
        if match := re.match(r"^/compare/(.+)\.\.\.(.+)$", rest):
            return self.compare(match.group(1), match.group(2))
        return httpx.Response(404, json={"message": "Not Found"})

    def pull_list(self, query: dict) -> list[dict]:
        ordered = sorted(self.pulls.values(), key=lambda pr: (pr["updated_at"], pr["number"]), reverse=True)
        size, page = int(query.get("per_page", 30)), int(query.get("page", 1))
        return ordered[(page - 1) * size : page * size]

    def listing(self, request: httpx.Request, stream: str, items: list[dict]) -> httpx.Response:
        etag = f'W/"{stream}-{self.versions[stream]}"'
        if request.headers.get("If-None-Match") == etag and request.url.params.get("page", "1") == "1":
            self.not_modified += 1
            return httpx.Response(304, headers={"etag": etag})
        return httpx.Response(200, json=items, headers={"etag": etag})

    def compare(self, base: str, head: str) -> httpx.Response:
        base, head = self.branches.get(base, base), self.branches.get(head, head)
        if base not in self.commits or head not in self.commits:
            return httpx.Response(404, json={"message": "Not Found"})
        before, after = self.history(base), self.history(head)
        new = [sha for sha in reversed(after) if sha not in before]
        status = "identical" if base == head else "ahead" if base in after else "behind" if head in before else "diverged"
        return httpx.Response(200, json={
            "status": status, "ahead_by": len(new), "commits": [self.bare(self.commits[sha]) for sha in new],
            "files": [file for sha in new for file in self.commits[sha]["files"]]})
