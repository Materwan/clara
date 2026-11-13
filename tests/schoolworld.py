"""In-memory stand-ins for Moodle, Auriga and Auriga's Keycloak, for httpx.MockTransport.

They answer the way the real sites were seen to answer (docs: TODO notes of 2026-11-01/02, now in docs/DECISIONS.md), closely
enough to run the real clients, and record what they were asked so that a test can check it: above all the Moodle functions
called (never a write one), and the hosts a token was sent to.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx

MOODLE = "moodle.epita.fr"
AURIGA = "auriga.epita.fr"
KEYCLOAK = "ionisepita-auth.np-auriga.nfrance.net"
TOKEN = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def jwt(**claims) -> str:
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part(claims)}.sig"


def make_pdf(text: str, pages: int = 1) -> bytes:
    """A PDF whose pages all say `text` (no parentheses in it), small enough to write by hand and real enough for pypdf."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{3 + n} 0 R' for n in range(pages))}] /Count {pages} >>".encode(),
        *(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {3 + pages} 0 R "
            f"/Resources << /Font << /F1 {4 + pages} 0 R >> >> >>".encode()
            for _ in range(pages)
        ),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    table = b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    return out + f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n" + table + (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{len(out)}\n%%EOF\n".encode()
    )


def make_docx(body: bytes) -> bytes:
    """A Word file whose text part is `body` (the content of `<w:body>`, for example b"<w:p><w:r><w:t>Hello</w:t></w:r></w:p>")."""
    xml = b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + b"</w:body></w:document>"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def link(token: str = TOKEN, site: str = "0" * 32, private: str = "") -> str:
    """What the Moodle mobile launch page puts in its link."""
    raw = ":::".join(part for part in (site, token, private) if part)
    return "moodlemobile://token=" + base64.b64encode(raw.encode()).decode()


def stamp(days: float = 0, hours: float = 0) -> int:
    return int((datetime.now(UTC) + timedelta(days=days, hours=hours)).timestamp())


class World:
    def __init__(self) -> None:
        # Moodle
        self.token = TOKEN
        self.moodle_dead = False  # every call answers invalidtoken
        self.moodle_calls: list[str] = []  # each web-service function asked for
        self.moodle_hosts: list[str] = []  # each host a request went to
        self.moodle_params: list[dict] = []
        # As the real site answers: most courses have no progress (`hasprogress` false and a progress of 0), a name may hold
        # `{mlang}` blocks and entities, and a course the owner hid from the overview is in none of the Timeline classes
        self.courses = [
            {"id": 11, "fullname": "Algorithmique avancée", "shortname": "ALGO", "startdate": stamp(-60), "enddate": stamp(100), "progress": 40, "hasprogress": True, "hidden": False},
            {"id": 12, "fullname": "{mlang en}Systems &amp; networks{mlang}{mlang fr}Systèmes &amp; réseaux{mlang}", "shortname": "SYS", "startdate": stamp(-60), "enddate": stamp(100), "progress": 0, "hasprogress": False, "hidden": False},
            {"id": 13, "fullname": "Algèbre", "shortname": "ALG1", "startdate": stamp(-400), "enddate": stamp(-200), "progress": 0, "hasprogress": False, "hidden": False},
            {"id": 15, "fullname": "Ancien cours", "shortname": "OLD", "startdate": stamp(-900), "enddate": stamp(-700), "progress": 0, "hasprogress": False, "hidden": True},
        ]
        self.past = {13}
        self.raw: dict[str, object] = {}  # a Moodle function, or an Auriga path, -> what to answer instead (JSON value, or bytes)
        self.events: list[dict] = []
        self.quizzes: list[dict] = []
        self.contents: dict[int, list[dict]] = {}
        self.grade_items: dict[int, list[dict]] = {}
        self.files: dict[str, bytes] = {}  # the path after /webservice/pluginfile.php/ -> bytes
        self.file_types: dict[str, str] = {}  # the same paths -> the Content-Type to answer (none: no header, as a bare body)
        self.trickle = 0.0  # seconds between two bytes of a document: a body that never seems to end
        self.files_dead = False  # the documents answer invalidtoken (the token lapsed meanwhile)
        self.file_redirect = ""  # answer a download with a redirect to this address
        # Auriga and Keycloak
        self.refresh_n = 0
        self.refresh_exp = stamp(hours=24)  # the end of the sign-in: absolute, a rotation does not extend it
        self.rotate = False  # each refresh gives a new refresh token
        self.offline = True  # the refresh token is an offline one
        self.keycloak_dead = False  # invalid_grant for a refresh
        self.codes = {"code-1"}
        self.keycloak_calls: list[dict] = []
        self.access = 0
        self.api_calls: list[str] = []
        self.api_401 = 0  # answer this many API calls with 401
        self.interventions: list[dict] = []
        self.synthese: list[list] = []
        self.evals: list[list] = []
        self.posted: list[dict] = []  # the bodies posted to searchResult
        self.broken_filter = False  # the saved synthèse query has no condition on the year any more
        self.no_total_pages = False  # a result with no `totalPages` (the real site always sends it)

    @property
    def refresh(self) -> str:
        """The refresh token Keycloak accepts now: a JWT that says whether it is an offline one."""
        return jwt(typ="Offline" if self.offline else "Refresh", n=self.refresh_n, exp=self.refresh_exp)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # ------------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == MOODLE:
            self.moodle_hosts.append(host)
            return self.moodle(request)
        if host == KEYCLOAK:
            return self.keycloak(request)
        if host == AURIGA:
            return self.auriga(request)
        self.moodle_hosts.append(host)  # a token sent anywhere else is recorded, for a test to refuse
        return httpx.Response(404)

    # ------------------------------------------------------------------
    # Moodle
    # ------------------------------------------------------------------
    def moodle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/webservice/pluginfile.php/"):
            token = request.url.params.get("token")
            if token != self.token or self.moodle_dead or self.files_dead:
                return httpx.Response(200, json={"exception": "webservice_access_exception", "errorcode": "invalidtoken", "message": "Invalid token"})
            if self.file_redirect:
                return httpx.Response(302, headers={"location": self.file_redirect})
            path = request.url.path.removeprefix("/webservice/pluginfile.php/")
            data = self.files.get(path)
            if data is None:
                return httpx.Response(404)
            headers = {"content-type": self.file_types[path]} if path in self.file_types else {}
            if self.trickle:
                return httpx.Response(200, content=self.slowly(data), headers=headers)
            return httpx.Response(200, content=data, headers={"content-length": str(len(data)), **headers})
        assert request.url.path == "/webservice/rest/server.php", request.url.path
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        function = form["wsfunction"]
        self.moodle_calls.append(function)
        self.moodle_params.append(form)
        if form.get("wstoken") != self.token or self.moodle_dead:
            return httpx.Response(200, json={"exception": "webservice_access_exception", "errorcode": "invalidtoken", "message": "Invalid token - token not found"})
        if function in self.raw:
            return self.answer(self.raw[function])
        match function:
            case "core_webservice_get_site_info":
                return httpx.Response(200, json={"userid": 7, "fullname": "Ada Student", "username": "ada", "userprivateaccesskey": "PRIVATE-KEY"})
            case "core_enrol_get_users_courses":
                return httpx.Response(200, json=self.courses)
            case "core_course_get_enrolled_courses_by_timeline_classification":
                wanted = form["classification"]
                rows = [c for c in self.courses if not c["hidden"] and (c["id"] in self.past) == (wanted == "past")] if wanted != "future" else []
                return httpx.Response(200, json={"courses": rows, "nextoffset": len(rows)})
            case "core_calendar_get_action_events_by_timesort":
                low, high = int(form["timesortfrom"]), int(form["timesortto"])
                rows = [e for e in self.events if low <= e["timesort"] <= high]
                return httpx.Response(200, json={"events": rows, "firstid": 1, "lastid": len(rows)})
            case "mod_quiz_get_quizzes_by_courses":
                asked = {int(v) for k, v in form.items() if k.startswith("courseids[")}
                return httpx.Response(200, json={"quizzes": [q for q in self.quizzes if q["course"] in asked], "warnings": []})
            case "core_course_get_contents":
                return httpx.Response(200, json=self.contents.get(int(form["courseid"]), []))
            case "gradereport_user_get_grade_items":
                items = self.grade_items.get(int(form["courseid"]), [])
                return httpx.Response(200, json={"usergrades": [{"courseid": int(form["courseid"]), "userid": 7, "gradeitems": items}], "warnings": []})
        return httpx.Response(200, json={"exception": "webservice_access_exception", "errorcode": "accessexception", "message": f"{function} is not allowed"})

    async def slowly(self, data: bytes):
        for at in range(len(data)):
            await asyncio.sleep(self.trickle)
            yield data[at : at + 1]

    @staticmethod
    def answer(value: object) -> httpx.Response:
        return httpx.Response(200, content=value) if isinstance(value, bytes) else httpx.Response(200, json=value)

    # ------------------------------------------------------------------
    # Keycloak
    # ------------------------------------------------------------------
    def keycloak(self, request: httpx.Request) -> httpx.Response:
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        self.keycloak_calls.append(form)
        assert form["client_id"] == "np-front"
        if form["grant_type"] == "authorization_code":
            if form["code"] not in self.codes or not form.get("code_verifier"):
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Code not valid"})
            self.codes.discard(form["code"])
            return httpx.Response(200, json=self.tokens())
        if form["grant_type"] == "refresh_token":
            if self.keycloak_dead or form["refresh_token"] != self.refresh:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token is not active"})
            if self.rotate:
                self.refresh_n += 1
            return httpx.Response(200, json=self.tokens())
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    def tokens(self) -> dict:
        self.access += 1
        return {
            "access_token": f"access-{self.access}",
            "expires_in": 60,
            "refresh_expires_in": max(0, self.refresh_exp - stamp()),
            "refresh_token": self.refresh,
            "id_token": jwt(email="ada@epita.fr"),
            "token_type": "Bearer",
            "scope": "openid offline_access",
        }

    # ------------------------------------------------------------------
    # Auriga
    # ------------------------------------------------------------------
    def auriga(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.api_calls.append(f"{request.method} {path}")
        assert request.headers.get("x-scope") == "frontend"
        if self.api_401 > 0:
            self.api_401 -= 1
            return httpx.Response(401)
        if request.headers.get("authorization") != f"Bearer access-{self.access}":
            return httpx.Response(401)
        if path in self.raw:
            return self.answer(self.raw[path])
        if path == "/api/me":
            return httpx.Response(200, json={"id": 1})
        if path == "/api/plannings/me":
            first, last = request.url.params["startDate"], request.url.params["endDate"]
            days = request.url.params.get_list("days")
            assert days == [str(n) for n in range(1, 8)]
            kept = [i for i in self.interventions if first <= i["startDateTime"][:10] <= last]
            return httpx.Response(200, json={"interventions": kept, "unavailabilities": []})
        if path in ("/api/menuEntries/1144/query/1198", "/api/menuEntries/1036/query/945"):
            query = int(path.rsplit("/", 1)[1])
            return httpx.Response(200, json={"queryDefinition": {"joinIdenticalLines": False, "searchResultDefinition": {"columns": []}, "searchResultGlobal": {"filters": {"conditions": [] if self.broken_filter else [{"field": "code", "operator": "like", "value": ["2526"]}]}}}, "queryId": query})
        if path in ("/api/menuEntries/1144/searchResult", "/api/menuEntries/1036/searchResult"):
            body = json.loads(request.content)
            self.posted.append(body)
            lines = self.synthese if "1144" in path else self.evals
            size, page = int(request.url.params["size"]), int(request.url.params["page"])
            chunk = lines[(page - 1) * size : page * size]
            answer = {"currentPage": page, "pageSize": size, "numberOfElements": len(chunk), "totalPages": max(1, -(-len(lines) // size)), "totalElements": len(lines), "content": {"columns": [], "lines": chunk}, "dataviz": None}
            if self.no_total_pages:
                del answer["totalPages"]
            return httpx.Response(200, json=answer)
        return httpx.Response(404)


ACTIVITY_TYPES = {"CM": "Cours magistral", "TD": "Travaux dirigés", "TP": "Travaux pratiques", "EX": "Examen"}


def intervention(start: str, end: str, caption: str | None, kind: str = "CM", room: str = "B101", exam: bool = False,
                 teacher: tuple[str, str] | None = ("Grace", "Hopper"), activity: str = "") -> dict:
    """A planning entry as the real site gives it: the activity type is an object (its code and its long caption), `isExam` is
    false even for an `EX` activity, a teacher is a `person` with a current first and last name, and an entry may have no
    pedagogical unit at all (`caption` None): its words are then in the activity."""
    units = [{"pedagogicalUnit": {"code": "2627_I_INF_X", "caption": {"fr": caption, "en": caption}}}] if caption else []
    return {
        "startDateTime": start,
        "endDateTime": end,
        "activityType": {"code": kind, "caption": {"fr": ACTIVITY_TYPES.get(kind, kind), "en": kind}},
        "activity": {"caption": {"fr": activity}} if activity else None,
        "isExam": exam,
        "interventionPedagogicalUnits": units,
        "interventionResources": [{"resource": {"caption": room}}],
        "interventionInstructors": [{"person": {"currentFirstName": teacher[0], "currentLastName": teacher[1]}}] if teacher else [],
    }


def synthese(n: int, final: str | None = "12.5", pre: str | None = None, year: str = "2627", title: object = None) -> list:
    """A line of "Mes notes (synthèse)" as the real site gives it, five cells whatever the columns are: the internal id, the
    average before the RATT retake (a string, null on most lines), the component code, its title, and the final average (a
    string, null on a line with none yet). The query's `content.columns` has four entries: the code and the title are one column."""
    return [n, pre, f"{year}_I_INF_{n:03d}", title or {"fr": f"Matière {n}", "en": f"Course {n}"}, final]


def connect_school(services, world: World, sites=("moodle", "auriga")) -> None:
    """Both sites as connected (the secrets and the connections), as the sign-in leaves them."""
    if "moodle" in sites:
        services.secrets.set("moodle_token", world.token)
        services.connections.set("moodle", "Ada Student")
    if "auriga" in sites:
        services.secrets.set("auriga_refresh_token", world.refresh)
        services.connections.set("auriga", "ada@epita.fr")
