"""A skill read from what you paste or send: a SKILL.md (frontmatter and instructions), or a .zip of the skill's folder.

Nothing here touches the database or runs anything: `read_skill` returns what it found, the problems that stop it being added
(`errors`) and what it noticed without refusing (`warnings`, `ignored`). It never raises on bad input, so that the preview can show
every problem. Both the preview and the add route call it, so what was previewed is what is stored.

**The frontmatter is a strict subset of YAML**: `key: value` lines between two `---` lines. A value is a plain or quoted scalar
(`"…"` with `\\n`, `\\t`, `\\"`, `\\\\`; `'…'` with `''`), a yes/no (`true`, `false`), or a flow list (`[a, b]`; the `packs` field also
takes a comma list, `a, b`). Anything else (an indented line, a block scalar, a map, a list with dashes) is refused with the line.

**An archive is hostile until read**: entries are checked before any is opened (no link or other special entry, no absolute path, no
`..`, no encrypted entry, no duplicate path once normalised and folded to lower case), the bytes are counted as they are decompressed
(`knowledge/ingest.py: read_capped`, never the sizes in the headers), and the whole archive, the number of entries and the unpacked
total are capped. Nothing is written to a disk here (a run of a skill's scripts writes the stored files to a temporary folder:
`workspace/sandbox.py`, `skill_mount`).
"""

from __future__ import annotations

import base64
import hashlib
import io
import lzma
import re
import stat
import struct
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass, field

from ..knowledge import ingest

MAX_SKILLS = 30  # skills held in all
MAX_SKILL_BYTES = 1_000_000  # SKILL.md and the other files of one skill, together
MAX_SKILL_MD = 100_000  # bytes of SKILL.md
MAX_FILES = 50  # files of one skill, SKILL.md included
MAX_PATH = 200  # characters of a file's path inside the skill
MAX_ZIP = 1_400_000  # bytes of the archive itself (base64 makes it 1.9 MB, inside the 2 MB request)
MAX_ENTRIES = 1_000  # entries of an archive, directories and junk included: past this it is not read at all
MAX_DESCRIPTION = 1_024
MAX_HINT = 200
MAX_PROJECTS = 50  # projects one skill may be reserved for
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

# Words that are a command of the terminal client (`cli/chat.py`), of the Discord bot (`clara_discord/bot.py`) or of the console
# (`console.py`), and the words of this feature: a skill cannot take one, so that a client can add its commands later and no
# skill is ever mistaken for them. tests/test_skills.py fails when a command of those three is missing here.
RESERVED = frozenset(
    {
        # terminal client
        "quit", "exit", "new", "resume", "conversations", "facts", "remember", "forget", "remind", "reminders", "unremind",
        "tasks", "task", "notify", "model", "tools", "mode", "approvals", "allow", "refuse", "undo", "help", "dictate",
        # Discord bot
        "status",
        # console
        "alerts", "audit", "automations", "backup", "debug", "dictation", "fallbacks", "models", "provider", "restart", "school",
        "secrets", "sources", "stop", "tokens", "trust", "update", "upkeep", "usage", "vault",
        # this feature and the usual ones of other agents
        "skills", "skill", "clear", "compact", "config", "settings", "memory", "init", "login", "logout",
        # a command of the web chat's own (api/commands.py BUILTIN: a test keeps them all here)
        "goal",
    }
)  # fmt: skip
FIELDS = frozenset({"name", "description", "argument-hint", "disable-model-invocation", "packs"})
JUNK_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})
JUNK_FOLDERS = frozenset({"__macosx", ".git"})
SCRIPT_EXTENSIONS = frozenset(
    {"sh", "bash", "zsh", "fish", "py", "pyw", "js", "mjs", "cjs", "ts", "ps1", "bat", "cmd", "rb", "pl", "php", "lua", "exe"}
)  # fmt: skip
SCRIPT_NOTE = (
    "A script: it runs only in the sandbox, when Clara runs it through ws_run with this skill (a request, as any run)."
)
STORED_NOTE = "Stored with the skill; only a script of the skill can read it, at /skill in the sandbox."


class SkillFormatError(ValueError):
    """The frontmatter or the archive is not in the format this version reads."""


@dataclass(frozen=True)
class SkillFile:
    path: str
    data: bytes
    kind: str  # "text" or "binary"

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def note(self) -> str:
        return note_for(self.path, self.data[:2])

    @property
    def text(self) -> str | None:
        return self.data.decode("utf-8") if self.kind == "text" else None


@dataclass
class Parsed:
    name: str | None = None
    description: str = ""
    argument_hint: str = ""
    disable_model_invocation: bool = False
    packs: list[str] = field(default_factory=list)
    skill_md: str = ""
    body: str = ""
    files: list[SkillFile] = field(default_factory=list)  # the other files, SKILL.md not among them
    ignored: list[str] = field(default_factory=list)  # frontmatter fields this version does not use
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.skill_md.encode()) + sum(item.size for item in self.files)


def is_script(path: str, head: bytes = b"") -> bool:
    """A file that looks like a script: in `scripts/`, with a script's extension, or starting with a shebang."""
    return path.split("/")[0] == "scripts" or ingest.extension(path) in SCRIPT_EXTENSIONS or head.startswith(b"#!")


def note_for(path: str, head: bytes = b"") -> str:
    return SCRIPT_NOTE if is_script(path, head) else STORED_NOTE


def file_kind(path: str, data: bytes) -> str:
    """`text` when the bytes are UTF-8 text with no NUL; anything else is stored as bytes."""
    if ingest.extension(path) in ingest.BINARY_EXTENSIONS or b"\x00" in data:
        return "binary"
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    return "text"


# ----------------------------------------------------------------------
# The frontmatter
# ----------------------------------------------------------------------
Value = str | list[str]
ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/"}


def _quoted(text: str, start: int, number: int) -> tuple[str, int]:
    """The quoted scalar that begins at `text[start]` and the index after its closing quote."""
    quote = text[start]
    out: list[str] = []
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == quote:
            if quote == "'" and text[index + 1 : index + 2] == "'":
                out.append("'")
                index += 2
                continue
            return "".join(out), index + 1
        if char == "\\" and quote == '"':
            escaped = ESCAPES.get(text[index + 1 : index + 2])
            if escaped is None:
                raise SkillFormatError(f"Line {number}: the escape {text[index : index + 2]!r} is not supported.")
            out.append(escaped)
            index += 2
            continue
        out.append(char)
        index += 1
    raise SkillFormatError(f"Line {number}: a quote is not closed.")


def _flow_list(text: str, number: int) -> list[str]:
    if not text.endswith("]"):
        raise SkillFormatError(f"Line {number}: a list starts with [ and ends with ].")
    inner, items, index = text[1:-1], [], 0
    while True:
        while inner[index : index + 1] == " ":
            index += 1
        if index >= len(inner):
            break
        if inner[index] in "\"'":
            item, index = _quoted(inner, index, number)
        else:
            end = inner.find(",", index)
            end = len(inner) if end < 0 else end
            item = inner[index:end].strip()
            if not item or any(char in item for char in "[]{}\"'"):
                raise SkillFormatError(
                    f"Line {number}: a list item is empty or holds a bracket or a quote (use quotes)."
                )
            index = end
        items.append(item)
        while inner[index : index + 1] == " ":
            index += 1
        if index < len(inner):
            if inner[index] != ",":
                raise SkillFormatError(f"Line {number}: expected a comma between list items.")
            index += 1
    return items


def _value(text: str, number: int) -> Value:
    if not text:
        return ""
    if text[0] in "\"'":
        found, end = _quoted(text, 0, number)
        if text[end:].strip():
            raise SkillFormatError(f"Line {number}: there is text after the closing quote.")
        return found
    if text[0] == "[":
        return _flow_list(text, number)
    if text[0] in "{|>&*!%@`" or text.startswith("- "):
        raise SkillFormatError(
            f"Line {number}: this kind of YAML value is not supported (a map, a block text, a list with dashes): "
            "write the value on one line, or a list as [a, b]."
        )
    if " #" in text:
        raise SkillFormatError(f"Line {number}: ' #' starts a comment in YAML: put the value in quotes to keep the #.")
    return text


def split_frontmatter(text: str) -> tuple[dict[str, Value], str]:
    """The fields between the two `---` lines, and what follows them. SkillFormatError names the line that is wrong."""
    lines = text.split("\n")
    if lines[0].rstrip() != "---":
        raise SkillFormatError(
            "SKILL.md must start with a line of three dashes (---), then the frontmatter, then another --- line."
        )
    end = next((index for index in range(1, len(lines)) if lines[index].rstrip() == "---"), None)
    if end is None:
        raise SkillFormatError("The frontmatter is not closed: add a line of three dashes (---) after it.")
    fields: dict[str, Value] = {}
    for number, raw in enumerate(lines[1:end], start=2):
        line = raw.rstrip()
        if not line.strip() or line.startswith("#"):
            continue
        if line[0] in " \t":
            raise SkillFormatError(
                f"Line {number}: an indented line is not supported: write each `key: value` on one line."
            )
        key, colon, rest = line.partition(":")
        if not colon or not KEY.match(key):
            raise SkillFormatError(f"Line {number}: not a `key: value` line.")
        if key in fields:
            raise SkillFormatError(f"Line {number}: {key} is given twice.")
        fields[key] = _value(rest.strip(), number)
    return fields, "\n".join(lines[end + 1 :]).strip("\n").rstrip()


def _text(fields: dict[str, Value], key: str, errors: list[str], limit: int) -> str:
    value = fields.get(key, "")
    if isinstance(value, list):
        errors.append(f"{key} must be text, not a list.")
        return ""
    if len(value) > limit:
        errors.append(f"{key} is {len(value):,} characters: at most {limit:,}.")
    return value


def _packs(value: Value) -> list[str]:
    items = value if isinstance(value, list) else [part.strip() for part in value.split(",")]
    return list(dict.fromkeys(item for item in items if item))


def read_skill_md(text: str, parsed: Parsed, given_name: str | None = None, fallback: str | None = None) -> None:
    """Fill `parsed` from the text of a SKILL.md. Its name is the frontmatter's, else `given_name` (the one you typed when you
    added it), else `fallback` (the folder of an archive)."""
    from ..packs.registry import BY_ID  # here, not at the top: the packs import the agent, which imports this package

    errors = parsed.errors
    if "\x00" in text:
        errors.append("SKILL.md is not a text file (it holds a NUL character).")
        return
    text = text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")
    if len(text.encode()) > MAX_SKILL_MD:
        errors.append(f"SKILL.md is {len(text.encode()):,} bytes: at most {MAX_SKILL_MD:,}.")
        return
    parsed.skill_md = text
    try:
        fields, parsed.body = split_frontmatter(text)
    except SkillFormatError as error:
        errors.append(str(error))
        return
    if not parsed.body.strip():
        errors.append("SKILL.md has no instructions after the frontmatter.")
    name = fields.get("name")
    if isinstance(name, list):
        errors.append("name must be text, not a list.")
        name = None
    elif name and given_name and given_name != name:
        parsed.warnings.append(f"The name {given_name!r} you gave is not used: the frontmatter says {name!r}.")
    parsed.name = name or given_name or fallback or None
    if parsed.name is None:
        errors.append("The skill has no name: write `name:` in the frontmatter, or give a name when you add it.")
    elif not NAME.match(parsed.name):
        errors.append(
            f"The name {parsed.name[:60]!r} is not valid: 1 to 40 lower-case letters, digits or dashes, not starting with a dash."
        )
    elif parsed.name in RESERVED:
        errors.append(f"{parsed.name!r} is the name of a command of Clara's clients: choose another name.")
    parsed.description = _text(fields, "description", errors, MAX_DESCRIPTION).strip()
    if not parsed.description:
        errors.append("The frontmatter needs a description: what the skill is for, in a sentence.")
    parsed.argument_hint = _text(fields, "argument-hint", errors, MAX_HINT).strip()
    flag = fields.get("disable-model-invocation", "false")
    if isinstance(flag, list) or flag.lower() not in ("true", "false"):
        errors.append("disable-model-invocation must be true or false.")
    else:
        parsed.disable_model_invocation = flag.lower() == "true"
    parsed.packs = _packs(fields.get("packs", ""))
    for pack in parsed.packs:
        if pack not in BY_ID:
            errors.append(f"No such tool pack: {pack[:40]!r} (the packs are {', '.join(BY_ID)}).")
        elif BY_ID[pack].where == "automation":
            errors.append(f"The pack {pack} is for automation runs only: a skill is run from a chat.")
    parsed.ignored = [key for key in fields if key not in FIELDS]
    if "allowed-tools" in parsed.ignored:
        parsed.warnings.append(
            "allowed-tools is ignored: a skill grants no right, and the gate still decides every call by the conversation's mode."
        )
    others = [key for key in parsed.ignored if key != "allowed-tools"]
    if others:
        parsed.warnings.append(f"These fields are not used by Clara and were left out: {', '.join(others)}.")


# ----------------------------------------------------------------------
# The archive
# ----------------------------------------------------------------------
def _refuse_entry(info: zipfile.ZipInfo) -> str | None:
    """Why this entry is refused outright (a hostile archive), or None. It is judged by its header, before anything is read."""
    name = info.filename.replace("\\", "/")
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        return "the name holds a control character"
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return "an absolute path"
    if ".." in name.split("/"):
        return "a path may not go up (..)"
    kind = stat.S_IFMT(info.external_attr >> 16)  # 0 when the maker wrote permissions only (Python's writestr does)
    if kind == stat.S_IFLNK:
        return "a symbolic link"
    if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
        return "a special file (not a plain file or a folder)"
    if info.flag_bits & 0x1:
        return "an encrypted entry"
    return None


def _junk(path: str) -> bool:
    parts = [part.lower() for part in path.split("/")]
    return parts[-1] in JUNK_NAMES or any(part in JUNK_FOLDERS for part in parts[:-1])


def read_zip(data: bytes, given_name: str | None, parsed: Parsed) -> None:
    """Fill `parsed` from a zip of a skill's folder: `SKILL.md` at its top, or inside the one folder that holds everything."""
    errors = parsed.errors
    if len(data) > MAX_ZIP:
        errors.append(f"The archive is {len(data):,} bytes: at most {MAX_ZIP:,}.")
        return
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, OverflowError, struct.error, NotImplementedError):
        errors.append("This is not a .zip archive, or it is damaged.")
        return
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            errors.append(f"The archive has {len(infos):,} entries: at most {MAX_ENTRIES:,}.")
            return
        kept: dict[str, zipfile.ZipInfo] = {}
        seen: dict[str, str] = {}
        junk = 0
        for info in infos:
            why = _refuse_entry(info)
            if why is not None:
                errors.append(f"{info.filename[:80]!r}: {why}.")
                continue
            if info.is_dir():
                continue
            try:
                path = ingest.clean_path(info.filename)
            except ingest.IngestError as error:
                errors.append(str(error))
                continue
            if len(path) > MAX_PATH:
                errors.append(f"{path[:60]}…: the path is longer than {MAX_PATH} characters.")
            elif _junk(path):
                junk += 1
            elif (key := unicodedata.normalize("NFC", path).casefold()) in seen:
                errors.append(f"{path[:80]!r} and {seen[key][:80]!r} are the same path once normalised.")
            else:
                seen[key] = path
                kept[path] = info
            if len(errors) >= 10:
                errors.append("(More problems were left out.)")
                break
        if errors:
            return
        if junk:
            parsed.warnings.append(
                f"{junk} file{'s' if junk != 1 else ''} of the system (__MACOSX, .DS_Store, .git) left out."
            )
        roots = {path.split("/")[0] for path in kept}
        folder = roots.pop() if len(roots) == 1 and all("/" in path for path in kept) else ""
        prefix = f"{folder}/" if folder else ""
        if f"{prefix}SKILL.md" not in kept:
            errors.append(
                "The archive has no SKILL.md at its top, or in the one folder that holds everything (the name is case-sensitive)."
            )
            return
        if len(kept) > MAX_FILES:
            errors.append(f"The archive has {len(kept)} files: at most {MAX_FILES}.")
            return
        budget = MAX_SKILL_BYTES
        found: dict[str, bytes] = {}
        for path in sorted(
            kept, key=lambda item: item != f"{prefix}SKILL.md"
        ):  # SKILL.md first: its cap is the smaller
            is_md = path == f"{prefix}SKILL.md"
            limit = min(budget, MAX_SKILL_MD) if is_md else budget
            try:
                raw = ingest.read_capped(archive, kept[path], limit)
            except (
                zipfile.BadZipFile,
                OSError,
                RuntimeError,
                NotImplementedError,
                EOFError,
                ValueError,
                OverflowError,
                struct.error,
                zlib.error,
                lzma.LZMAError,
            ) as error:
                errors.append(f"{path[:80]}: could not be unpacked ({error}).")
                return
            if raw is None:  # more real bytes than the cap, whatever the header said
                errors.append(
                    f"SKILL.md unpacks to more than {MAX_SKILL_MD:,} bytes."
                    if is_md and limit == MAX_SKILL_MD
                    else f"The files unpack to more than {MAX_SKILL_BYTES:,} bytes in all ({path[:80]})."
                )
                return
            budget -= len(raw)
            found[path[len(prefix) :]] = raw
    try:
        text = found.pop("SKILL.md").decode("utf-8")
    except UnicodeDecodeError:
        errors.append("SKILL.md is not UTF-8 text.")
        return
    read_skill_md(text, parsed, given_name, folder)
    parsed.files = [SkillFile(path, raw, file_kind(path, raw)) for path, raw in sorted(found.items())]


def read_skill(*, skill_md: str | None = None, zip_base64: str | None = None, name: str | None = None) -> Parsed:
    """What a pasted SKILL.md or a base64 zip holds. Exactly one is given."""
    parsed = Parsed()
    given = (name or "").strip() or None
    if (skill_md is None) == (zip_base64 is None):
        parsed.errors.append(
            "Send either skill_md (the text of a SKILL.md) or zip_base64 (a zip of the skill's folder)."
        )
    elif skill_md is not None:
        read_skill_md(skill_md, parsed, given)
    else:
        try:
            raw = base64.b64decode(zip_base64 or "", validate=True)
        except ValueError:
            parsed.errors.append("zip_base64 is not base64.")
        else:
            read_zip(raw, given, parsed)
    return parsed
