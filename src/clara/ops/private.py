"""Files only the service user may read.

At start the data folder is made `rwx------` and its files `rw-------`, whatever the umask of whoever made them. Then
the server **checks** (and refuses to start otherwise, unless CLARA_CHECK_PERMISSIONS=false) that the `.env` file, the
data folder and the secret key file cannot be read by anyone but the user it runs as.
"""

from __future__ import annotations

import contextlib
import os
import stat
from pathlib import Path

DIR_MODE = 0o700
FILE_MODE = 0o600


class PermissionProblem(RuntimeError):
    pass


def private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    with contextlib.suppress(OSError):
        os.chmod(path, DIR_MODE)


def harden_tree(root: Path, skip: tuple[Path, ...] = ()) -> None:
    """The folder and everything in it private; links are left alone, and so are the folders in `skip`."""
    if not root.is_dir():
        return
    private_dir(root)
    skipped = {path.resolve() for path in skip}
    for folder, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if Path(folder, name).resolve() not in skipped]
        for name in dirs:
            path = Path(folder, name)
            if not path.is_symlink():
                with contextlib.suppress(OSError):
                    os.chmod(path, DIR_MODE)
        for name in files:
            path = Path(folder, name)
            if not path.is_symlink():
                with contextlib.suppress(OSError):
                    os.chmod(path, FILE_MODE)


def write_private_text(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, FILE_MODE)


def problem_with(path: Path, what: str) -> str | None:
    """Why `path` is not private to this user (None: it is)."""
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    except OSError as error:
        return f"{what} {path} cannot be checked: {error.strerror}"
    if info.st_uid != os.getuid():
        return f"{what} {path} belongs to uid {info.st_uid}, not to the user Clara runs as ({os.getuid()})"
    loose = info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    if loose:
        return f"{what} {path} can be read or changed by others (mode {stat.filemode(info.st_mode)}): chmod go= {path}"
    return None


def check_private(env_file: Path | None, data_dir: Path, key_file: Path | None) -> list[str]:
    problems = []
    for path, what in ((env_file, "The settings file"), (data_dir, "The data folder"), (key_file, "The secret key file")):
        if path is None:
            continue
        # systemd hands credentials in a folder of its own, private to the service: nothing to check there
        if what == "The secret key file" and os.environ.get("CREDENTIALS_DIRECTORY") and \
                str(path).startswith(os.environ["CREDENTIALS_DIRECTORY"]):
            continue
        found = problem_with(path, what)
        if found:
            problems.append(found)
    if key_file is not None:
        with contextlib.suppress(ValueError, OSError):
            if key_file.resolve().is_relative_to(data_dir.resolve()):
                problems.append(f"The secret key file {key_file} is inside the data folder: keep it elsewhere "
                                "(a systemd credential), or a copy of the data folder carries its own key.")
    return problems
