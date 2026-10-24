"""Signing the browser in: paste a token once, get a cookie (HttpOnly, Secure, SameSite=Strict)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from ..auth import COOKIE, Blocked, client_address
from ..auth.web import MAX_DAYS
from .common import ApiError, caller, services

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class WebLogin(BaseModel):
    token: str = Field(min_length=1, max_length=200)


@router.post("/web-login")
def web_login(body: WebLogin, request: Request, response: Response) -> dict:
    s = services(request)
    address = client_address(request.client.host if request.client else None, request.headers.get("x-forwarded-for"))
    try:
        s.limiter.check(address)
    except Blocked as error:
        raise ApiError(429, str(error), headers={"Retry-After": str(error.retry_after)}) from None
    if request.headers.get("x-clara-web") != "1":
        raise ApiError(400, "The web sign-in needs the X-Clara-Web header.")
    token = s.tokens.verify(body.token)
    if token is None:
        s.limiter.fail(address)
        s.traffic.record("auth_failure", path="/v1/auth/web-login", address=address)
        raise ApiError(401, "This token is not valid (or was revoked).")
    cookie = s.web_sessions.start(token)
    response.set_cookie(
        COOKIE, cookie, max_age=MAX_DAYS * 86400, httponly=True, secure=True, samesite="strict", path="/"
    )
    return {"token": token.name, "client": token.client, "approvals": token.approvals}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    who = caller(request)
    if who.web_session is not None:
        services(request).web_sessions.end(who.web_session)
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict:
    who = caller(request)
    return {
        "token": who.token.name,
        "client": who.client,
        "approvals": who.token.approvals,
        "web": who.web_session is not None,
    }
