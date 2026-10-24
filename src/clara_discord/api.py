"""The bot's side of Clara's HTTP API (the bot is a client like any other, with its own token)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx


class ClaraError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Clara:
    def __init__(self, url: str, token: str):
        self.url = url
        self.headers = {"Authorization": f"Bearer {token}", "X-Clara-Client": "discord"}
        self.http = httpx.AsyncClient(base_url=url, headers=self.headers, timeout=httpx.Timeout(10.0, read=120.0))

    async def close(self) -> None:
        await self.http.aclose()

    async def call(self, method: str, path: str, **options) -> dict:
        try:
            response = await self.http.request(method, path, **options)
        except httpx.HTTPError as error:
            raise ClaraError(0, f"Clara cannot be reached: {error}") from None
        if response.is_error:
            try:
                message = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = response.text[:300]
            raise ClaraError(response.status_code, message)
        return response.json() if response.content else {}

    async def file(self, path: str) -> bytes:
        response = await self.http.get("/v1/workspace/file", params={"path": path, "raw": "true"})
        if response.is_error:
            raise ClaraError(response.status_code, "cannot read the file")
        return response.content

    async def stream(
        self, method: str, path: str, body: dict | None = None, read: float = 900.0
    ) -> AsyncIterator[dict]:
        timeout = httpx.Timeout(10.0, read=read)
        try:
            async with (
                httpx.AsyncClient(base_url=self.url, headers=self.headers, timeout=timeout) as http,
                http.stream(method, path, json=body) as response,
            ):
                if response.is_error:
                    await response.aread()
                    try:
                        message = response.json()["error"]["message"]
                    except (ValueError, KeyError, TypeError):
                        message = response.text[:300]
                    raise ClaraError(response.status_code, message)
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        yield json.loads(line[6:])
        except httpx.HTTPError as error:
            raise ClaraError(0, f"Clara cannot be reached: {error}") from None
