"""Projects: files given once (uploads, folders, zips, GitHub snapshots) that the project's conversations use.

    GET    /v1/projects                         the projects
    POST   /v1/projects                         {name, description?, instructions?}
    GET    /v1/projects/{id}                    one project, its repositories and its files (paths and sizes)
    PATCH  /v1/projects/{id}                    {name?, description?, instructions?, pinned?}
    DELETE /v1/projects/{id}                    its files go; its conversations stay, in no project
    POST   /v1/projects/{id}/files              {files: [{path, data (base64)}]}: text, code, PDF, .docx, .zip
    GET    /v1/projects/{id}/file?path=         a file's text
    DELETE /v1/projects/{id}/files?path=&folder= a file, or all the files of a folder
    POST   /v1/projects/{id}/github             {repo, ref?}: a snapshot of a repository's text files
    POST   /v1/projects/{id}/sources/{sid}/sync download it again
    DELETE /v1/projects/{id}/sources/{sid}      remove it and its files
"""

from __future__ import annotations

import asyncio
import base64
import binascii

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ..knowledge.github_snapshot import GitHubError, check_ref, parse_repo
from ..knowledge.ingest import ExtractedFile, Skipped, expand, unpack
from ..knowledge.projects import (
    MAX_DESCRIPTION,
    MAX_INSTRUCTIONS,
    MAX_NAME,
    Added,
    Project,
    ProjectError,
    Projects,
    Source,
)
from ..llm import NoModel
from .common import ApiError, services

router = APIRouter(prefix="/v1/projects", tags=["projects"])

MAX_FILES_PER_UPLOAD = 1_000
SKIPPED_SHOWN = 200


def _window(request: Request) -> int:
    s = services(request)
    try:
        return s.router.choose().window
    except NoModel:
        return s.settings.ollama_context


def describe(project: Project, projects: Projects, window: int) -> dict:
    tokens = projects.weight(project.id) if project.files else 0
    return {
        "id": project.id, "name": project.name, "description": project.description,
        "instructions": project.instructions, "created_at": project.created_at, "updated_at": project.updated_at,
        "pinned": project.pinned, "files": project.files, "size": project.size, "conversations": project.conversations,
        "context": {"tokens": tokens, "window": window, "percent": round(100 * tokens / window, 1) if window else 0,
                    "inline": tokens <= projects.inline_budget(window)},
        "limits": {"size": projects.max_bytes, "files": projects.max_files},
    }


def describe_source(source: Source) -> dict:
    return {"id": source.id, "kind": source.kind, "repo": source.repo, "ref": source.ref, "folder": source.folder,
            "commit": source.commit_sha, "synced_at": source.synced_at, "files": source.files, "size": source.size,
            "skipped": source.skipped, "problem": source.problem}


def describe_added(result: Added) -> dict:
    return {"added": result.added, "replaced": result.replaced,
            "skipped": [{"path": s.path, "reason": s.reason} for s in result.skipped[:SKIPPED_SHOWN]],
            "skipped_count": len(result.skipped)}


def _project(request: Request, project_id: int) -> Project:
    project = services(request).projects.get(project_id)
    if project is None:
        raise ApiError(404, f"No project {project_id}.")
    return project


def _details(request: Request, project_id: int) -> dict:
    projects = services(request).projects
    project = _project(request, project_id)
    return {
        **describe(project, projects, _window(request)),
        "sources": [describe_source(s) for s in projects.sources(project.id)],
        "file_list": [{"path": f.path, "kind": f.kind, "size": f.size, "source": f.source_id, "added_at": f.added_at}
                      for f in projects.files(project.id)],
    }


@router.get("")
def listing(request: Request) -> dict:
    projects = services(request).projects
    window = _window(request)
    return {"projects": [describe(project, projects, window) for project in projects.listing()]}


class NewProject(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)
    description: str = Field(default="", max_length=MAX_DESCRIPTION)
    instructions: str = Field(default="", max_length=MAX_INSTRUCTIONS)


@router.post("", status_code=201)
def create(body: NewProject, request: Request) -> dict:
    try:
        project = services(request).projects.create(body.name, body.description, body.instructions)
    except ProjectError as error:
        raise ApiError(422, str(error)) from None
    return _details(request, project.id)


@router.get("/{project_id}")
def get(project_id: int, request: Request) -> dict:
    return _details(request, project_id)


class ProjectChange(BaseModel):
    name: str | None = Field(default=None, max_length=MAX_NAME)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    instructions: str | None = Field(default=None, max_length=MAX_INSTRUCTIONS)
    pinned: bool | None = None


@router.patch("/{project_id}")
def change(project_id: int, body: ProjectChange, request: Request) -> dict:
    _project(request, project_id)
    projects = services(request).projects
    try:
        if body.name is not None or body.description is not None or body.instructions is not None:
            projects.update(project_id, body.name, body.description, body.instructions)
        if body.pinned is not None:
            projects.pin(project_id, body.pinned)
    except ProjectError as error:
        raise ApiError(422, str(error)) from None
    return _details(request, project_id)


@router.delete("/{project_id}")
def delete(project_id: int, request: Request) -> dict:
    _project(request, project_id)
    return {"deleted": True, "conversations_moved": services(request).projects.delete(project_id)}


class UploadedFile(BaseModel):
    path: str = Field(min_length=1, max_length=400)
    data: str


class Upload(BaseModel):
    files: list[UploadedFile] = Field(min_length=1, max_length=MAX_FILES_PER_UPLOAD)


def _read_upload(files: list[UploadedFile]) -> tuple[list[ExtractedFile], list[Skipped]]:
    extracted: list[ExtractedFile] = []
    skipped: list[Skipped] = []
    for item in files:
        try:
            data = base64.b64decode(item.data, validate=True)
        except (binascii.Error, ValueError):
            skipped.append(Skipped(item.path, "not sent as base64"))
            continue
        found, left_out = expand(item.path, data)
        extracted += found
        skipped += left_out
    return extracted, skipped


@router.post("/{project_id}/files")
async def upload(project_id: int, body: Upload, request: Request) -> dict:
    _project(request, project_id)
    projects = services(request).projects
    extracted, skipped = await asyncio.to_thread(_read_upload, body.files)
    result = await asyncio.to_thread(projects.add, project_id, extracted)
    result = Added(result.added, result.replaced, skipped + result.skipped)
    return {**describe_added(result), "project": _details(request, project_id)}


@router.get("/{project_id}/file")
def read_file(project_id: int, request: Request, path: str = Query(min_length=1, max_length=400)) -> dict:
    found = services(request).projects.file(project_id, path)
    if found is None:
        raise ApiError(404, "No such file in the project.")
    info, content = found
    return {"path": info.path, "kind": info.kind, "size": info.size, "source": info.source_id, "content": content}


@router.delete("/{project_id}/files")
def remove_files(project_id: int, request: Request, path: str = Query(max_length=400), folder: bool = False) -> dict:
    _project(request, project_id)
    if not path.strip("/") and not folder:
        raise ApiError(422, "Which file?")
    removed = services(request).projects.remove(project_id, path, folder)
    if not removed:
        raise ApiError(404, "No such file in the project.")
    return {"removed": removed, "project": _details(request, project_id)}


class Repository(BaseModel):
    repo: str = Field(min_length=3, max_length=300)
    ref: str = Field(default="", max_length=200)


async def _sync(request: Request, source: Source) -> Added:
    s = services(request)
    try:
        snapshot = await s.github_snapshots.snapshot(source.repo, source.ref, s.secrets.get("github_token") or "")
        files, skipped = await asyncio.to_thread(unpack, snapshot.archive, source.folder, True)
    except (GitHubError, ValueError) as error:
        s.projects.source_failed(source, str(error))
        raise ApiError(502 if isinstance(error, GitHubError) else 422, str(error)) from None
    return await asyncio.to_thread(s.projects.replace_source_files, source, files, skipped, snapshot.commit_sha)


@router.post("/{project_id}/github")
async def add_repository(project_id: int, body: Repository, request: Request) -> dict:
    _project(request, project_id)
    projects = services(request).projects
    try:
        repo, ref = parse_repo(body.repo)
        ref = check_ref(body.ref) or ref
    except GitHubError as error:
        raise ApiError(422, str(error)) from None
    try:
        source = projects.add_source(project_id, repo, ref, repo.split("/", 1)[1])
    except ProjectError as error:
        raise ApiError(409, str(error)) from None
    try:
        result = await _sync(request, source)
    except ApiError:
        projects.remove_source(source)
        raise
    return {**describe_added(result), "project": _details(request, project_id)}


def _source(request: Request, project_id: int, source_id: int) -> Source:
    source = services(request).projects.source(project_id, source_id)
    if source is None:
        raise ApiError(404, "No such repository in the project.")
    return source


@router.post("/{project_id}/sources/{source_id}/sync")
async def sync(project_id: int, source_id: int, request: Request) -> dict:
    result = await _sync(request, _source(request, project_id, source_id))
    return {**describe_added(result), "project": _details(request, project_id)}


@router.delete("/{project_id}/sources/{source_id}")
def remove_repository(project_id: int, source_id: int, request: Request) -> dict:
    removed = services(request).projects.remove_source(_source(request, project_id, source_id))
    return {"removed": removed, "project": _details(request, project_id)}
