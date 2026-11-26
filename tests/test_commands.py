"""`GET /v1/commands`: the one list the web box reads to complete, draw and explain a command (the skills of the conversation's project, and
the built-in commands)."""

from __future__ import annotations

import httpx

from clara.api.commands import BUILTIN
from clara.app import create_app
from clara.skills import RESERVED, read_skill


def skill(name: str, hint: str = "", description: str = "Does it"):
    extra = f'argument-hint: "{hint}"\n' if hint else ""
    return read_skill(skill_md=f"---\nname: {name}\ndescription: {description}\n{extra}---\nDo it.\n")


def hold(services, name: str, projects=(), **fields) -> None:
    services.skills.add(skill(name, **fields), scope="projects" if projects else "global", projects=list(projects), replace=False)


async def test_the_list_is_the_builtins_and_the_skills_of_the_project_by_name(client, services):
    project = (await client.post("/v1/projects", json={"name": "Thesis"})).json()["id"]
    other = (await client.post("/v1/projects", json={"name": "Other"})).json()["id"]
    hold(services, "weekly-report", hint="[week]", description="Summarise the week")
    hold(services, "mine", projects=[project])

    found = (await client.get("/v1/commands", params={"project": project})).json()["commands"]
    assert [row["name"] for row in found] == ["goal", "mine", "weekly-report"]
    assert set(found[0]) == {"name", "kind", "argument_hint", "description"}
    assert found[0]["kind"] == "builtin" and "<condition>" in found[0]["argument_hint"] and found[0]["description"]
    assert found[2] == {  # a skill row says where it is kept, as the Skills page does
        "name": "weekly-report", "kind": "skill", "argument_hint": "[week]", "description": "Summarise the week", "scope": "global", "projects": [],
    }
    assert found[1]["kind"] == "skill" and found[1]["argument_hint"] == ""
    assert found[1]["scope"] == "projects" and found[1]["projects"] == [project]

    for where in (other, "none"):  # a skill reserved for a project is a command nowhere else
        names = [row["name"] for row in (await client.get("/v1/commands", params={"project": where})).json()["commands"]]
        assert names == ["goal", "weekly-report"]


def test_every_builtin_command_is_a_reserved_name():
    assert {row["name"] for row in BUILTIN} <= RESERVED  # so that no skill can take the name of a command that runs


async def test_a_skill_cannot_be_added_under_the_name_of_a_builtin(client):
    for row in BUILTIN:
        text = skill(row["name"]).skill_md
        preview = (await client.post("/v1/skills/preview", json={"skill_md": text})).json()
        assert preview["can_add"] is False and any("command of Clara's clients" in e for e in preview["errors"])
        assert (await client.post("/v1/skills", json={"skill_md": text})).status_code == 422
    assert (await client.get("/v1/commands", params={"project": "none"})).json()["commands"][0]["kind"] == "builtin"


async def test_a_skill_stored_under_the_name_of_a_builtin_is_left_out(client, services):
    services.db.execute(
        "INSERT INTO skills (name, description, skill_md, body, scope, size, created_at) VALUES ('goal', 'x', 'x', 'x', 'global', 1, 'x')"
    )
    found = (await client.get("/v1/commands", params={"project": "none"})).json()["commands"]
    assert [(row["name"], row["kind"]) for row in found] == [("goal", "builtin")]


async def test_the_project_is_required_and_checked(client):
    assert (await client.get("/v1/commands")).status_code == 422
    assert (await client.get("/v1/commands", params={"project": "x"})).status_code == 422


async def test_the_route_needs_a_token(services):
    transport = httpx.ASGITransport(app=create_app(services))
    async with httpx.AsyncClient(transport=transport, base_url="http://clara.test") as anonymous:
        assert (await anonymous.get("/v1/commands", params={"project": "none"})).status_code == 401
