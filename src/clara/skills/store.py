"""The skills Clara holds, and what a chat command makes of one. See skills/parse.py for how one is read, DECISIONS.md, « Skills ».

A skill is text. It lives in the database (`skills`, `skill_projects`, `skill_files`), so it is in Clara's backups. Nothing changes
in it after it was added: adding it again under its name, with `replace`, writes it anew in one transaction. Only `SKILL.md` is ever
read; the other files are stored as bytes and are never run, served or put in a prompt.

**A command in a chat** (`/review-pr 123`): the message the model gets is the skill's instructions with the arguments put in, the
stored message shows what you typed (`messages.shown`). The text is yours, as the skill is: it is not outside content and it
does not taint the turn. A skill that is not visible to the conversation's project is not a skill there: the message is plain text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from ..db import Database
from ..gate import Action, Audit, Decision, Situation
from ..timeutil import now_iso
from .parse import MAX_PROJECTS, MAX_SKILLS, RESERVED, Parsed, note_for

MAX_EXPANDED = 120_000  # bytes of the text a command expands to, arguments included
SUMMARY_LINE = "Skills run in this conversation: "
COMMAND = re.compile(r"^/([a-z0-9][a-z0-9-]{0,39})(?=\s|$)")
WORDS = re.compile(r"\"([^\"]*)\"|'([^']*)'|(\S+)")  # simple quoting: "a b" and 'a b' are one word, no escapes
ARGUMENTS = re.compile(r"\$ARGUMENTS(?![A-Za-z0-9_])")
SUBSTITUTE = re.compile(r"\$(ARGUMENTS(?![A-Za-z0-9_])|[1-9](?![0-9]))")
BRIEF = (
    "SELECT s.name, s.description, s.argument_hint, s.packs, s.disable_model_invocation, s.scope, s.size, s.created_at, s.created_by,"
    " (SELECT COUNT(*) FROM skill_files f WHERE f.skill = s.name) AS file_count FROM skills s"
)
VISIBLE = "(s.scope = 'global' OR EXISTS (SELECT 1 FROM skill_projects p WHERE p.skill = s.name AND p.project_id = ?))"


class SkillError(ValueError):
    """A skill that cannot be added, found or run; `status` is the HTTP status the API answers with."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Expansion:
    name: str
    text: str  # what the model gets
    shown: str  # what the conversation shows: `/name arguments` as typed


def substitute(body: str, arguments: str) -> str:
    """The instructions with the arguments put in: `$ARGUMENTS` is all of them, `$1` to `$9` the words (a missing one is empty).
    One pass, so an argument that holds `$1` is not substituted again. A skill that never says `$ARGUMENTS` gets them on a last line."""
    words = [quoted or single or bare for quoted, single, bare in WORDS.findall(arguments)]

    def put(found: re.Match) -> str:
        if found[1].startswith("ARGUMENTS"):
            return arguments
        number = int(found[1])
        return words[number - 1] if number <= len(words) else ""

    text = SUBSTITUTE.sub(put, body)
    if arguments and not ARGUMENTS.search(body):
        text += f"\n\nArguments: {arguments}"
    return text


def split_summary(summary: str) -> tuple[str, list[str]]:
    """A summary without its last line « Skills run in this conversation: … », and the names that line gave."""
    lines = summary.rstrip().split("\n")
    if lines and lines[-1].startswith(SUMMARY_LINE):
        return "\n".join(lines[:-1]).rstrip(), [name for name in lines[-1][len(SUMMARY_LINE) :].split(", ") if name]
    return summary, []


def with_skills(summary: str, names: list[str]) -> str:
    return f"{summary.rstrip()}\n\n{SUMMARY_LINE}{', '.join(names)}" if names else summary


class Skills:
    def __init__(self, db: Database, audit: Audit | None = None):
        self.db = db
        self.audit = audit

    def _audit(self, detail: str) -> None:
        """Who added or removed a skill, in the audit log. The name only, never the text."""
        if self.audit is not None:
            self.audit.record(
                Action("skill", "write", "memory", ("skill",), detail),
                Situation("interactive"),
                "",
                Decision("allow", "you changed it"),
                "skill",
                detail=detail,
            )

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def count(self) -> int:
        return int(self.db.value("SELECT COUNT(*) FROM skills", (), 0))

    def exists(self, name: str | None) -> bool:
        return bool(name) and self.db.one("SELECT 1 FROM skills WHERE name = ?", (name,)) is not None

    def _brief(self, row, projects: dict[str, list[int]]) -> dict:
        return {
            "name": row["name"],
            "description": row["description"],
            "argument_hint": row["argument_hint"],
            "packs": json.loads(row["packs"]),
            "disable_model_invocation": bool(row["disable_model_invocation"]),
            "scope": row["scope"],
            "projects": projects.get(row["name"], []),
            "file_count": row["file_count"],
            "size": row["size"],
            "created_at": row["created_at"],
            "created_by": row["created_by"],
        }

    def listing(self, *, everything: bool = False, project: int | None = None) -> list[dict]:
        """The skills of a conversation in that project (None: no project): the global ones and those reserved for it. With
        `everything`, every skill whatever its scope: what the owner's page lists."""
        if everything:
            rows = self.db.all(f"{BRIEF} ORDER BY s.name")
        else:
            rows = self.db.all(f"{BRIEF} WHERE {VISIBLE} ORDER BY s.name", (-1 if project is None else project,))
        projects: dict[str, list[int]] = {}
        for row in self.db.all("SELECT skill, project_id FROM skill_projects ORDER BY project_id"):
            projects.setdefault(row["skill"], []).append(row["project_id"])
        return [self._brief(row, projects) for row in rows]

    def get(self, name: str, *, project: int | None = None, everything: bool = True) -> dict | None:
        """One skill with its SKILL.md and the list of its other files (never their bytes), or None when there is none, or when
        it is not visible to `project` (unless `everything`)."""
        found = next(
            (item for item in self.listing(everything=everything, project=project) if item["name"] == name), None
        )
        if found is None:
            return None
        row = self.db.one("SELECT skill_md FROM skills WHERE name = ?", (name,))
        files = self.db.all(
            "SELECT path, size, sha256, kind, substr(data, 1, 2) AS head FROM skill_files WHERE skill = ? ORDER BY path",
            (name,),
        )
        return {
            **found,
            "skill_md": row["skill_md"],
            "files": [
                {
                    "path": item["path"],
                    "size": item["size"],
                    "sha256": item["sha256"],
                    "kind": item["kind"],
                    "note": note_for(item["path"], bytes(item["head"])),
                }
                for item in files
            ],
        }

    # ------------------------------------------------------------------
    # Adding, replacing, removing (the routes allow only a token that may answer every request)
    # ------------------------------------------------------------------
    def add(self, parsed: Parsed, *, scope: str, projects: list[int], replace: bool, by: str = "") -> dict:
        """Store a skill that `read_skill` found no error in, in one transaction. A name that is taken needs `replace`."""
        if parsed.errors or not parsed.name:
            raise SkillError(" ".join(parsed.errors) or "The skill has no name.")
        name = parsed.name
        projects = list(dict.fromkeys(projects))
        if scope == "projects":
            if not projects or len(projects) > MAX_PROJECTS:
                raise SkillError(f"A skill reserved for projects names 1 to {MAX_PROJECTS} of them.")
        with self.db.transaction() as tx:
            for project in projects if scope == "projects" else ():
                if tx.execute("SELECT 1 FROM projects WHERE id = ?", (project,)).fetchone() is None:
                    raise SkillError(f"No project {project}.", 404)
            taken = tx.execute("SELECT 1 FROM skills WHERE name = ?", (name,)).fetchone() is not None
            if taken and not replace:
                raise SkillError(f"A skill named {name} exists: send replace=true to replace it.", 409)
            if not taken and tx.execute("SELECT COUNT(*) FROM skills").fetchone()[0] >= MAX_SKILLS:
                raise SkillError(f"{MAX_SKILLS} skills are held: remove one before adding another.", 409)
            tx.execute("DELETE FROM skills WHERE name = ?", (name,))  # the projects and the files go with it
            tx.execute(
                "INSERT INTO skills (name, description, argument_hint, packs, disable_model_invocation, skill_md, body, scope, size,"
                " created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    parsed.description,
                    parsed.argument_hint,
                    json.dumps(parsed.packs),
                    int(parsed.disable_model_invocation),
                    parsed.skill_md,
                    parsed.body,
                    scope,
                    parsed.size,
                    now_iso(),
                    by,
                ),
            )
            if scope == "projects":
                tx.executemany(
                    "INSERT INTO skill_projects (skill, project_id) VALUES (?, ?)", [(name, item) for item in projects]
                )
            tx.executemany(
                "INSERT INTO skill_files (skill, path, data, size, sha256, kind) VALUES (?, ?, ?, ?, ?, ?)",
                [(name, item.path, item.data, item.size, item.sha256, item.kind) for item in parsed.files],
            )
        self._audit(f"{by or 'someone'} {'replaced' if taken else 'added'} the skill {name}")
        found = self.get(name)
        assert found is not None
        return found

    def remove(self, name: str, by: str = "") -> bool:
        removed = self.db.execute("DELETE FROM skills WHERE name = ?", (name,)).rowcount > 0
        if removed:
            self._audit(f"{by or 'someone'} removed the skill {name}")
        return removed

    # ------------------------------------------------------------------
    # A chat command
    # ------------------------------------------------------------------
    def expand(self, message: str, project: int | None) -> Expansion | None:
        """What a message that starts with `/<name>` is, if `<name>` is a skill of the conversation's project (not a word a client
        takes for a command). None: the message is plain text. SkillError: the expansion is over `MAX_EXPANDED`."""
        found = COMMAND.match(message)
        if found is None or found[1] in RESERVED:
            return None
        name = found[1]
        row = self.db.one(
            f"SELECT s.body FROM skills s WHERE s.name = ? AND {VISIBLE}", (name, -1 if project is None else project)
        )
        if row is None:
            return None
        text = substitute(row["body"], message[found.end() :].strip())
        if len(text.encode()) > MAX_EXPANDED:
            raise SkillError(
                f"/{name} with these arguments makes {len(text.encode()):,} bytes of instructions: at most {MAX_EXPANDED:,}.",
                413,
            )
        return Expansion(name, text, message.strip())

    def run_in(self, conversation_id: str, project: int | None, carried: Sequence[str] = ()) -> list[str]:
        """The skills run in a conversation, in the order they were first run: the names an earlier summary gave (`carried`) and
        those of its messages whose `shown` starts with `/<name>`. Only a skill that exists now and is visible to the project."""
        known = {item["name"] for item in self.listing(project=project)}
        names = list(carried)
        for row in self.db.all(
            "SELECT shown FROM messages WHERE conversation_id = ? AND role = 'user' AND active = 1 AND shown LIKE '/%' ORDER BY id",
            (conversation_id,),
        ):
            found = COMMAND.match(row["shown"])
            if found and found[1] in known and found[1] not in names:
                names.append(found[1])
        return [name for name in dict.fromkeys(names) if name in known]
