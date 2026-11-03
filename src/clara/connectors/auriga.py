"""Auriga (EPITA's schedule and grades), read only, through the REST API its own web page uses.

The page is an Angular app over `https://auriga.epita.fr/api/`; a call carries `Authorization: Bearer <access token>` and
`x-scope: frontend`. The access token lives 60 seconds. It comes from Keycloak (realm `npionisepita`, the public client
`np-front`): the owner uses "Se connecter avec Microsoft", so a password grant is out, and the sign-in is the page's own:
authorization code with PKCE, redirected to `https://auriga.epita.fr/clara-callback` (a plain 404 page of the site, where the
code stays visible in the address bar). The owner signs in once in a browser and pastes that address back
(`code_from_address`); Clara swaps the code for tokens and keeps the **refresh token** in the secrets store
(`auriga_refresh_token`). The scope asks for `offline_access`, so that the refresh token outlives the Keycloak session
(until it has been idle for a month, Keycloak's default). Each refresh may give a new refresh token: it is kept.

When Keycloak refuses the refresh token (`invalid_grant`), nothing but a new sign-in helps: the error says `reconnect`.

This client only reads: the planning, and the two saved queries that the page uses for the grades. It sends no write.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .school import Event, SchoolError, line

SITE = "https://auriga.epita.fr"
API = f"{SITE}/api"
REALM = "https://ionisepita-auth.np-auriga.nfrance.net/auth/realms/npionisepita"
AUTH_URL = f"{REALM}/protocol/openid-connect/auth"
TOKEN_URL = f"{REALM}/protocol/openid-connect/token"
CLIENT_ID = "np-front"
REDIRECT = f"{SITE}/clara-callback"
SCOPE = "openid profile email offline_access"
TIMEOUT = httpx.Timeout(20.0, read=60.0)
SYNTHESE = (1144, 1198)  # "Mes notes (synthèse)": the menu entry and its saved query
EVAL = (1036, 945)  # "Mes notes (éval)"
PAGE = 100
MAX_PAGES = 12


class AurigaError(SchoolError):
    pass


# ----------------------------------------------------------------------
# Signing in
# ----------------------------------------------------------------------
def authorize_url(state: str, challenge: str) -> str:
    query = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "response_mode": "query",
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urlencode(query)}"


def code_from_address(text: str, state: str) -> str:
    """The code in the address the owner pasted (the page they land on after signing in), checked against the sign-in
    that was started."""
    found = str(text or "").strip().strip("<>\"' ")
    parts = urlsplit(found)
    site = urlsplit(SITE)
    if parts.scheme != site.scheme or parts.hostname != site.hostname:
        raise AurigaError(
            f"Paste the whole address of the page you land on after signing in: it starts with {SITE}/clara-callback"
        )
    values = {**parse_qs(parts.fragment), **parse_qs(parts.query)}  # Keycloak answers in the query (or the fragment)
    if values.get("error"):
        detail = (values.get("error_description") or values["error"])[0]
        raise AurigaError(f"Auriga's sign-in said: {line(detail, 200)}")
    if values.get("state", [""])[0] != state:
        raise AurigaError("This address belongs to another sign-in (or an older one): start the sign-in again.")
    code = values.get("code", [""])[0]
    if not code:
        raise AurigaError("There is no code in this address: sign in again and paste the address you land on.")
    return code


def claims(token: str) -> dict:
    """What a JWT says, read without checking it (a label for the owner, never a decision): the tokens come straight from
    Keycloak's token endpoint, over TLS."""
    try:
        payload = token.split(".")[1]
        found = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError, binascii.Error):
        return {}
    return found if isinstance(found, dict) else {}


def account_of(tokens: dict) -> str:
    said = claims(str(tokens.get("id_token") or ""))
    return line(said.get("email") or said.get("preferred_username") or said.get("name") or "", 80)


def lasting(refresh_token: str) -> bool:
    """True for an offline token (kept until it has been idle for a month), not a session one (an hour, ended with the
    Keycloak session)."""
    return str(claims(refresh_token).get("typ", "")).lower() == "offline"


class AurigaAuth:
    """Keycloak's token endpoint (like GoogleAuth for Google): the code swap at the end of a sign-in, and the renewal."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport

    async def token(self, data: dict, lapses: bool = False) -> dict:
        """`lapses`: a refused grant means the refresh token is over (a new sign-in is needed)."""
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as http:
                response = await http.post(TOKEN_URL, data={"client_id": CLIENT_ID, **data})
        except httpx.HTTPError as error:
            raise AurigaError(f"Auriga's sign-in service could not be reached: {error}") from None
        try:
            found = response.json()
        except ValueError:
            found = {}
        found = found if isinstance(found, dict) else {}
        if response.is_error:
            if found.get("error") == "invalid_grant":
                if lapses:
                    raise AurigaError(
                        "Auriga no longer accepts the sign-in (it ended or was revoked): sign in again on the "
                        "Connections page, or with `clara auriga connect`.",
                        True,
                    )
                raise AurigaError(
                    "Auriga refused this sign-in code (used already, or too old): start the sign-in again."
                )
            detail = found.get("error_description") or found.get("error") or response.status_code
            raise AurigaError(f"Auriga's sign-in service refused: {line(detail, 200)}")
        return found

    async def exchange(self, code: str, verifier: str) -> dict:
        return await self.token(
            {"grant_type": "authorization_code", "code": code, "code_verifier": verifier, "redirect_uri": REDIRECT}
        )

    async def renew(self, refresh_token: str) -> dict:
        return await self.token({"grant_type": "refresh_token", "refresh_token": refresh_token}, lapses=True)


# ----------------------------------------------------------------------
# What the site says, kept small
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Grade:
    code: str  # the component code, such as 2627_I_INF_…: it begins with the school year
    title: str
    value: str  # the average or the mark, as Auriga writes it ("" when there is none yet)
    coef: str = ""
    kind: str = ""


@dataclass(frozen=True)
class Grades:
    lines: list[Grade]
    notes: tuple[str, ...] = ()  # what the person must know about these lines (a filter not set, lines not read)


def caption(value: Any) -> str:
    """A name Auriga writes as text or as {fr, en}."""
    if isinstance(value, dict):
        for key in ("fr", "en", "caption", "name", "label"):
            found = value.get(key)
            if found:
                return line(caption(found), 200)
        return ""
    return line(value, 200)


def label_of(value: Any) -> str:
    """The name of a thing Auriga describes as an object (a room, a teacher, a kind of activity)."""
    if isinstance(value, dict):
        for key in ("caption", "name", "fullName", "label", "code"):
            if value.get(key):
                return caption(value[key])
        first, last = caption(value.get("firstName")), caption(value.get("lastName"))
        return f"{first} {last}".strip()
    return line(value, 120)


def moment(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def rows(value: Any) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


class Auriga:
    def __init__(
        self,
        refresh_token: str,
        on_refresh: Callable[[str], None] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.refresh_token = refresh_token
        self.on_refresh = on_refresh  # told each new refresh token, to keep it
        self._transport = transport
        self._access = ""
        self._expires = 0.0
        self._lock = asyncio.Lock()

    async def access_token(self) -> str:
        async with self._lock:
            if self._access and self._expires > time.monotonic() + 10:
                return self._access
            found = await AurigaAuth(transport=self._transport).renew(self.refresh_token)
            if not found.get("access_token"):
                raise AurigaError("Auriga's sign-in service gave no access token.")
            self._access = str(found["access_token"])
            self._expires = time.monotonic() + int(found.get("expires_in") or 60)
            renewed = found.get("refresh_token")
            if renewed and renewed != self.refresh_token:
                self.refresh_token = str(renewed)
                if self.on_refresh is not None:
                    self.on_refresh(self.refresh_token)
            return self._access

    def _open(self) -> httpx.AsyncClient:
        """One connection for the calls that follow each other (the pages of one answer), so that a TLS handshake is not
        made for each. It is not kept between tool calls: nothing is left open to close."""
        return httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport)

    async def request(self, method: str, path: str, http: httpx.AsyncClient | None = None, **options: Any) -> Any:
        response: httpx.Response | None = None
        for attempt in (1, 2):
            headers = {
                "Authorization": f"Bearer {await self.access_token()}",
                "x-scope": "frontend",
                "Accept": "application/json",
                "User-Agent": "clara",
            }
            try:
                if http is None:
                    async with self._open() as own:
                        response = await own.request(method, f"{API}{path}", headers=headers, **options)
                else:
                    response = await http.request(method, f"{API}{path}", headers=headers, **options)
            except httpx.HTTPError as error:
                raise AurigaError(f"Auriga could not be reached: {error}") from None
            if response.status_code == 401 and attempt == 1:  # the access token ended early: one more, fresh
                self._access = ""
                continue
            break
        assert response is not None
        if response.is_error:
            status = response.status_code
            raise AurigaError(
                "Auriga refused the access it was given (HTTP 401)."
                if status == 401
                else "Auriga says this account may not read that (HTTP 403)."
                if status == 403
                else "Auriga cannot find that (HTTP 404): the page may have changed."
                if status == 404
                else f"Auriga answered HTTP {status}."
            )
        try:
            return response.json()
        except ValueError:
            raise AurigaError("Auriga did not answer with data (the site may be down for maintenance).") from None

    async def me(self) -> None:
        """Proves the sign-in reaches the API."""
        await self.request("GET", "/me")

    # ------------------------------------------------------------------
    # The planning
    # ------------------------------------------------------------------
    async def planning(self, first: date, last: date) -> list[Event]:
        """The classes and exams between two dates, inclusive."""
        params = [("days", str(day)) for day in range(1, 8)]
        params += [("startDate", first.isoformat()), ("endDate", last.isoformat())]
        found = await self.request("GET", "/plannings/me", params=params)
        events: list[Event] = []
        for item in rows(found.get("interventions") if isinstance(found, dict) else None):
            start, end = moment(item.get("startDateTime")), moment(item.get("endDateTime"))
            if start is None:
                continue
            units = [
                caption((unit.get("pedagogicalUnit") or {}).get("caption"))
                for unit in rows(item.get("interventionPedagogicalUnits"))
            ]
            rooms = [label_of(entry.get("resource")) for entry in rows(item.get("interventionResources"))]
            teachers = [
                label_of(entry.get("instructor") or entry) for entry in rows(item.get("interventionInstructors"))
            ]
            title = " / ".join(dict.fromkeys(unit for unit in units if unit)) or line(item.get("name") or "(untitled)")
            details = tuple(
                part
                for part in (
                    ", ".join(dict.fromkeys(room for room in rooms if room)),
                    ", ".join(dict.fromkeys(name for name in teachers[:3] if name)),
                )
                if part
            )
            exam = bool(item.get("isExam"))
            events.append(
                Event(
                    start,
                    end,
                    "auriga",
                    label_of(item.get("activityType")) or "class",
                    title,
                    details,
                    ("exam",) if exam else (),
                )
            )
        return events

    # ------------------------------------------------------------------
    # The grades
    # ------------------------------------------------------------------
    async def grades(self, kind: str, year: str) -> Grades:
        """The grades of a school year (`2627`). `synthese`: the average of each component; `eval`: each mark, with its coef.
        The saved query of the page is fetched and posted back, so that no copy of it lives here."""
        entry, query = SYNTHESE if kind == "synthese" else EVAL
        notes: list[str] = []
        grades: list[Grade] = []
        async with self._open() as http:
            found = await self.request("GET", f"/menuEntries/{entry}/query/{query}", http)
            definition = found.get("queryDefinition") if isinstance(found, dict) else None
            if not isinstance(definition, dict):
                raise AurigaError(
                    f"Auriga's saved grades query (menu entry {entry}, query {query}) is not what Clara expects: the page "
                    "may have changed."
                )
            body = {**definition, "queryId": query}
            if kind == "synthese":  # the one global filter is the year; the lines are checked below in any case
                try:
                    body["searchResultGlobal"]["filters"]["conditions"][0]["value"] = [year]
                except (KeyError, IndexError, TypeError):
                    notes.append(
                        "The year could not be set on Auriga's saved query (the page may have changed): Auriga was asked "
                        "for the year the query holds, so the lines of this year may be missing."
                    )
            for number in range(1, MAX_PAGES + 1):
                page = await self.request(
                    "POST",
                    f"/menuEntries/{entry}/searchResult",
                    http,
                    params={"size": PAGE, "page": number, "sort": "id", "disableWarnings": "true"},
                    json=body,
                )
                page = page if isinstance(page, dict) else {}
                content = page.get("content")
                for cells in (content.get("lines") if isinstance(content, dict) else None) or []:
                    grade = self._grade(kind, cells)
                    if grade is not None and grade.code.startswith(year):
                        grades.append(grade)
                total = page.get("totalPages")
                if number >= (total if isinstance(total, int) else 1):
                    break
            else:  # the last page allowed was read and Auriga has more
                every = page.get("totalElements")
                notes.append(
                    f"Only the first {MAX_PAGES * PAGE} lines"
                    + (f" of {every}" if isinstance(every, int) and every > MAX_PAGES * PAGE else "")
                    + " Auriga sent were read: lines may be missing."
                )
        return Grades(sorted(grades, key=lambda grade: (grade.code, grade.title)), tuple(notes))

    @staticmethod
    def _grade(kind: str, cells: Any) -> Grade | None:
        """A line of a result: `[id, average, code, title, "Moy. (finale)"]` or `[id, mark, coef, code, type]`. The places
        were read from one account's types and counts: a line that does not fit is left out."""
        if not isinstance(cells, list) or len(cells) < 5:
            return None

        def text(value: Any) -> str:
            return "" if value is None else caption(value) if isinstance(value, dict) else line(value, 60)

        if kind == "synthese":
            return Grade(text(cells[2]), caption(cells[3]), text(cells[1]), "", text(cells[4]))
        return Grade(text(cells[3]), "", text(cells[1]), text(cells[2]), text(cells[4]))


async def sign_in(address: str, state: str, verifier: str) -> tuple[str, str, bool]:
    """What the owner pasted, proved: the code in the address is swapped for tokens and the API must answer with them. Returns
    the refresh token to keep, the account, and whether the token is an offline one (`lasting`)."""
    tokens = await AurigaAuth().exchange(code_from_address(address, state), verifier)
    refresh = str(tokens.get("refresh_token") or "")
    if not refresh:
        raise AurigaError("Auriga gave no refresh token.")
    client = Auriga(refresh)  # a refresh may give a newer refresh token: the client keeps the latest
    await client.me()
    return client.refresh_token, account_of(tokens) or "Auriga", lasting(client.refresh_token)
