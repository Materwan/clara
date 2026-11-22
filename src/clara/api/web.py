"""The web site: the Svelte single-page app, built at release time into `clara/web/dist` (the machine needs no Node).

Every page gets a strict Content-Security-Policy: scripts, styles, fonts and connections only from the site itself.
Built files have hashed names and are cached for a year; `index.html` is never cached, so an update shows at once.
"""

from __future__ import annotations

import mimetypes
from importlib import resources
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

router = APIRouter(include_in_schema=False)

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; font-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"
)
MISSING = """<!doctype html><meta charset="utf-8"><title>Clara</title>
<p>The web site is not built in this tree. Build it with <code>scripts/build-web.sh</code> (releases ship it built).</p>"""


def dist() -> Path:
    return Path(str(resources.files("clara").joinpath("web/dist")))


def _file(path: Path, cache: str) -> Response:
    media, _ = mimetypes.guess_type(path.name)
    return FileResponse(
        path,
        media_type=media or "application/octet-stream",
        headers={"Cache-Control": cache, "Content-Security-Policy": CSP},
    )


@router.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@router.get("/{path:path}")
def site(path: str, request: Request) -> Response:
    if path.startswith("v1/"):
        return JSONResponse({"error": {"code": "not_found", "message": "No such route."}}, 404)
    root = dist()
    if not (root / "index.html").exists():
        return HTMLResponse(MISSING, headers={"Content-Security-Policy": CSP})
    if path:
        candidate = (root / path).resolve()
        if candidate.is_relative_to(root.resolve()) and candidate.is_file():
            immutable = "/assets/" in f"/{path}"
            return _file(candidate, "public, max-age=31536000, immutable" if immutable else "no-cache")
    return _file(root / "index.html", "no-cache")
