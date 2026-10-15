"""The workspace for you: browse it, read and download files, put files in, delete them, see the history and the
script runs, undo a change. What you do here is committed too, under your token's name. Your own actions are not gated:
the gate is for Clara's tools."""

from __future__ import annotations

import base64
import binascii
import mimetypes

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..workspace.paths import PathError, clean
from ..workspace.store import Workspace, WorkspaceError, looks_binary
from .common import ApiError, caller, services

router = APIRouter(prefix="/v1/workspace", tags=["workspace"])


def _workspace(request: Request) -> Workspace:
    workspace = services(request).workspace
    if workspace is None:
        raise ApiError(404, "The workspace is not available.")
    return workspace


def _problem(error: Exception) -> ApiError:
    text = str(error)
    return ApiError(404 if "does not exist" in text else 422, text)


@router.get("")
def status(request: Request) -> dict:
    s = services(request)
    workspace = _workspace(request)
    return {"root": str(workspace.root), "size": workspace.size_bytes(), "max_size": workspace.max_bytes,
            "max_file": workspace.max_file_bytes, "run_ready": bool(s.sandbox and s.sandbox.ready),
            "run_problem": s.sandbox.problem if s.sandbox else "no sandbox"}


@router.get("/tree")
def tree(request: Request, path: str = Query("", max_length=400), depth: int = Query(3, ge=1, le=10)) -> dict:
    try:
        return {"path": clean(path), "entries": _workspace(request).listing(path, depth)}
    except (WorkspaceError, PathError) as error:
        raise _problem(error) from None


@router.get("/file")
def read(request: Request, path: str = Query(min_length=1, max_length=400), raw: bool = False) -> Response:
    workspace = _workspace(request)
    try:
        data = workspace.read_bytes(path)
    except (WorkspaceError, PathError) as error:
        raise _problem(error) from None
    name = clean(path)
    if raw:
        media = mimetypes.guess_type(name)[0] or "application/octet-stream"
        safe = "".join(char if char.isalnum() or char in "._- " else "_" for char in name.rsplit("/", 1)[-1])[:100]
        # always a download, never rendered: a file Clara wrote must not run as a page of this site
        return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{safe}"',
                                                         "Content-Security-Policy": "sandbox"})
    if looks_binary(data):
        return JSONResponse({"path": name, "binary": True, "size": len(data)})
    return JSONResponse({"path": name, "binary": False, "size": len(data), "content": data.decode("utf-8", "replace")})


class Upload(BaseModel):
    path: str = Field(min_length=1, max_length=400)
    data: str = Field(description="base64")
    overwrite: bool = False


@router.post("/upload")
async def upload(body: Upload, request: Request) -> dict:
    workspace = _workspace(request)
    try:
        data = base64.b64decode(body.data, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(422, "The file is not base64.") from None
    try:
        change = await workspace.write(body.path, data, overwrite=body.overwrite,
                                       message=f"upload by {caller(request).name}: {clean(body.path)}")
    except (WorkspaceError, PathError) as error:
        raise _problem(error) from None
    return {"path": change.path, "action": change.action, "size": change.size, "commit": change.commit}


class TextFile(BaseModel):
    path: str = Field(min_length=1, max_length=400)
    content: str = Field(max_length=10_000_000)


@router.put("/file")
async def write(body: TextFile, request: Request) -> dict:
    try:
        change = await _workspace(request).write(body.path, body.content, overwrite=True,
                                                 message=f"edit by {caller(request).name}: {clean(body.path)}")
    except (WorkspaceError, PathError) as error:
        raise _problem(error) from None
    return {"path": change.path, "action": change.action, "size": change.size, "commit": change.commit}


@router.delete("/file")
async def delete(request: Request, path: str = Query(min_length=1, max_length=400)) -> dict:
    try:
        change = await _workspace(request).delete(path, message=f"delete by {caller(request).name}: {clean(path)}")
    except (WorkspaceError, PathError) as error:
        raise _problem(error) from None
    return {"path": change.path, "commit": change.commit}


@router.get("/log")
async def log(request: Request, path: str = Query("", max_length=400), limit: int = Query(50, ge=1, le=200)) -> dict:
    try:
        return {"commits": await _workspace(request).log(path, limit)}
    except (WorkspaceError, PathError) as error:
        raise _problem(error) from None


@router.get("/commits/{commit}")
async def show(commit: str, request: Request) -> dict:
    try:
        return {"commit": commit, "show": await _workspace(request).show(commit)}
    except WorkspaceError as error:
        raise ApiError(422, str(error)) from None


class Undo(BaseModel):
    commit: str | None = Field(default=None, max_length=40)


@router.post("/undo")
async def undo(body: Undo, request: Request) -> dict:
    try:
        return {"message": f"Reverted {await _workspace(request).undo(body.commit)}"}
    except (ValueError, WorkspaceError) as error:
        raise ApiError(409, str(error)) from None


@router.get("/runs")
def runs(request: Request, limit: int = Query(50, ge=1, le=500)) -> dict:
    sandbox = services(request).sandbox
    return {"runs": sandbox.runs(limit) if sandbox else []}
