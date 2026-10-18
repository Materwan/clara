"""The daily recap: at the end of each local day Clara opens a check-in, waits for your answer, and only then writes the
day into the daily note of the vault.

* **Asking.** At `CLARA_RECAP_AT` (23:59, in the server's zone), or at the next start if the server was off then, a
  *check-in* conversation ("Bilan du 2026-10-18") is opened, in French: the day's digest (digest.py) is joined to its
  first message and Clara answers with what she saw, the tasks that may be done, and what else you did. The `checkin`
  event carries that message to the web site and to Discord (a thread under it in #general). Nothing is written yet.
* **Waiting.** You answer from either surface, in that conversation, as in any chat. Clara may ask one thing more, shows
  the recap, and writes it with `write_daily_recap` (one block of the daily note, replaced on each call). Tasks you
  confirm are marked done.
* **Reminding.** If you have not answered at `CLARA_RECAP_REMIND_AT` the next morning, a `checkin` event reminds you.
* **Closing.** If the next day's check-in comes and this one is still unanswered, Clara closes it first: she writes the
  recap from the digest alone, the uncertain points under « À confirmer ». The conversation stays open for a late answer.

One row of `recaps` per day says where it stands: asking, asked, written, closed, failed or skipped. A day that was
being asked when the server stopped is asked again at the next start. A failed attempt is retried twice, ten minutes
apart; then an alert is raised.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

from ..agent.estimate import CHARS_PER_TOKEN
from ..agent.turn import ChatRequest, ServerStopping
from ..timekeeping.dueloop import DueLoop
from ..timeutil import iso, parse_iso, resolve_local, utcnow, zone
from . import prompts
from .digest import DigestBuilder, french_date

if TYPE_CHECKING:
    from ..services import Services

log = logging.getLogger(__name__)

SURFACES = ("web", "discord")  # where a check-in is announced
MAX_ATTEMPTS = 3
RETRY_SECONDS = 600.0
WINDOW_SHARE = 0.35  # of the model's window, at most, for the digest
MAX_TEXT = 2_000  # what an event carries


class RecapError(ValueError):
    pass


@dataclass(frozen=True)
class Recap:
    day: str
    status: str
    conversation_id: str | None
    timezone: str
    attempts: int
    error: str
    note_path: str
    asked_at: str | None
    reminded_at: str | None
    finished_at: str | None


def _recap(row) -> Recap:
    return Recap(row["day"], row["status"], row["conversation_id"], row["timezone"], row["attempts"], row["error"],
                 row["note_path"], row["asked_at"], row["reminded_at"], row["finished_at"])


def _clock(text: str) -> time:
    hours, minutes = text.split(":")
    return time(int(hours), int(minutes))


def read_day(text: str, today: date) -> date:
    """A day as the console gives it: YYYY-MM-DD, or `today`, `yesterday`."""
    text = (text or "").strip().lower()
    if text in ("", "today"):
        return today
    if text == "yesterday":
        return today - timedelta(days=1)
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise RecapError(f"{text!r} is not a day: give YYYY-MM-DD, today or yesterday.") from None


class Recaps(DueLoop):
    name = "recaps"

    def __init__(self, services: Services, clock=utcnow):
        super().__init__(clock)
        self.s = services
        self.db = services.db
        self.events = services.events
        settings = services.settings
        self.enabled = settings.recap
        self.zone_name = settings.timezone
        self.at = _clock(settings.recap_at)
        self.remind_at = _clock(settings.recap_remind_at)
        self._running: dict[str, asyncio.Task] = {}
        self._turn = asyncio.Lock()  # one check-in is opened or closed at a time

    # ------------------------------------------------------------------
    # The rows
    # ------------------------------------------------------------------
    def row(self, day: str) -> Recap | None:
        found = self.db.one("SELECT * FROM recaps WHERE day = ?", (day,))
        return _recap(found) if found else None

    def for_conversation(self, conversation_id: str) -> Recap | None:
        if not conversation_id:
            return None
        found = self.db.one("SELECT * FROM recaps WHERE conversation_id = ?", (conversation_id,))
        return _recap(found) if found else None

    def is_checkin(self, conversation_id: str) -> bool:
        return self.for_conversation(conversation_id) is not None

    def listing(self, limit: int = 10) -> list[Recap]:
        return [_recap(row) for row in self.db.all("SELECT * FROM recaps ORDER BY day DESC LIMIT ?", (limit,))]

    def _stamp(self) -> str:
        return iso(self._clock())

    def _set(self, day: str, **fields) -> None:
        assignments = ", ".join(f"{name} = ?" for name in fields)
        self.db.execute(f"UPDATE recaps SET {assignments} WHERE day = ?", (*fields.values(), day))

    # ------------------------------------------------------------------
    # The clock
    # ------------------------------------------------------------------
    def trigger(self, day: date) -> datetime:
        return resolve_local(datetime.combine(day, self.at), zone(self.zone_name))

    def remind_instant(self, day: date) -> datetime:
        return resolve_local(datetime.combine(day + timedelta(days=1), self.remind_at), zone(self.zone_name))

    def due_day(self, now: datetime) -> date:
        """The latest day whose time to ask has come: today after CLARA_RECAP_AT, else yesterday."""
        today = now.astimezone(zone(self.zone_name)).date()
        return today if self.trigger(today) <= now else today - timedelta(days=1)

    def next_due(self) -> datetime | None:
        if not self.enabled:
            return None
        now = self._clock()
        day = self.due_day(now)
        soon = [self.trigger(day + timedelta(days=1))]
        if self.row(day.isoformat()) is None:
            soon.append(now)
        for recap in self.listing(5):
            if recap.status == "asked" and recap.reminded_at is None:
                soon.append(self.remind_instant(date.fromisoformat(recap.day)))
            elif recap.status == "failed" and recap.attempts < MAX_ATTEMPTS and recap.finished_at:
                soon.append(parse_iso(recap.finished_at) + timedelta(seconds=RETRY_SECONDS))
        return min(soon)

    @property
    def firing(self) -> bool:
        return bool(self._running)

    async def wait_running(self) -> None:
        await asyncio.gather(*self._running.values(), return_exceptions=True)

    def fire_due(self) -> int:
        if not self.enabled or self.stopping:
            return 0
        started = 0
        try:
            now = self._clock()
            day = self.due_day(now)
            if self.row(day.isoformat()) is None and self._spawn(day):
                started += 1
            for recap in self.listing(5):
                if recap.status == "asked" and recap.reminded_at is None and recap.asked_at:
                    due = self.remind_instant(date.fromisoformat(recap.day))
                    if due <= now:
                        self._set(recap.day, reminded_at=iso(now))
                        if parse_iso(recap.asked_at) < due:
                            self._remind(recap)
                elif (recap.status == "failed" and recap.attempts < MAX_ATTEMPTS and recap.finished_at
                      and parse_iso(recap.finished_at) + timedelta(seconds=RETRY_SECONDS) <= now):
                    started += self._spawn(date.fromisoformat(recap.day))
        except Exception:
            log.exception("recap: could not fire what is due")
        return started

    # ------------------------------------------------------------------
    # Asking
    # ------------------------------------------------------------------
    def _spawn(self, day: date) -> bool:
        key = day.isoformat()
        if key in self._running or self.s.vault is None:
            return False
        task = asyncio.create_task(self._ask(day), name=f"recap-{key}")
        self._running[key] = task
        task.add_done_callback(lambda _: self._running.pop(key, None))
        return True

    def _budget(self) -> int:
        window = self.s.router.choose("", False).window
        return max(5_000, min(self.s.settings.recap_digest_chars, int(window * CHARS_PER_TOKEN * WINDOW_SHARE)))

    async def _summarise(self, title: str, excerpts: str) -> str:
        choice = self.s.router.upkeep(self.s.router.choose("", False), False)
        messages = [{"role": "system", "content": prompts.SUMMARY},
                    {"role": "user", "content": f"Conversation «{title or 'sans titre'}» :\n\n{excerpts}"}]
        return (await self.s.agent.runner.complete(messages, choice, kind="recap", conversation_id=None)).strip()

    async def _ask(self, day: date) -> None:
        key = day.isoformat()
        async with self._turn:
            previous = self.row(key)
            attempts = (previous.attempts if previous else 0) + 1
            if previous is None:
                self.db.execute("INSERT INTO recaps (day, status, timezone, attempts, created_at) VALUES (?, 'asking', ?, ?, ?)",
                                (key, self.zone_name, attempts, self._stamp()))
            else:
                self._set(key, status="asking", attempts=attempts, error="", finished_at=None, conversation_id=None)
            conversation_id = ""
            try:
                await self._close_older(key)
                digest = await DigestBuilder(self.s).build(day, self.zone_name, budget=self._budget(),
                                                           summarise=self._summarise)
                conversation = self.s.conversations.create("server", title=prompts.ASKED_TITLE.format(date=key))
                conversation_id = conversation.id
                self.s.conversations.update(conversation_id, vault_open=True)
                self._set(key, conversation_id=conversation_id)
                reply = (await self.s.agent.answer(ChatRequest(
                    message=prompts.OPENING_MESSAGE.format(date=french_date(day)), prefix=digest.text, client="server",
                    conversation=conversation_id, instructions=prompts.OPENING, timezone=self.zone_name, no_tools=True,
                    quiet=True, mode="unattended", kind="recap"))).strip()
                if not reply:
                    raise RecapError("the model answered nothing")
            except ServerStopping:  # the next start asks again
                self._forget(key, conversation_id)
                return
            except Exception as error:
                log.warning("recap %s: could not ask (%s: %s)", key, type(error).__name__, error)
                self._forget(key, conversation_id, keep=attempts, error=f"{type(error).__name__}: {error}")
                if attempts >= MAX_ATTEMPTS:
                    self.s.alerts.raise_("recap", "The daily recap could not be started", f"{key}: {error}")
                return
            now = self._clock()
            self._set(key, status="asked", asked_at=iso(now))
            if now >= self.remind_instant(day):  # asked after the morning reminder's time: no reminder is due
                self._set(key, reminded_at=iso(now))
            self.s.alerts.clear("recap")
            self.events.publish("checkin", prompts.ASKED_TITLE.format(date=french_date(day)), reply[:MAX_TEXT],
                                payload={"day": key, "conversation": conversation_id, "stage": "asked"},
                                targets=SURFACES, source="recap", conversation_id=conversation_id)

    def _forget(self, key: str, conversation_id: str, keep: int = 0, error: str = "") -> None:
        """An attempt that did not end: its conversation (if it has no answer yet) goes, and the day is either free to
        be asked again (`keep` is 0) or marked failed."""
        if conversation_id and not self.db.value(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND role = 'assistant' AND content != ''",
                (conversation_id,), 0):
            self.s.conversations.delete(conversation_id)
            conversation_id = ""
        if keep:
            self._set(key, status="failed", error=error[:300], finished_at=self._stamp(), conversation_id=conversation_id or None)
        else:
            self.db.execute("DELETE FROM recaps WHERE day = ?", (key,))

    def recover(self) -> int:
        """At start: days that were being asked when the server stopped are freed, to be asked again."""
        rows = self.db.all("SELECT day, conversation_id FROM recaps WHERE status = 'asking'")
        for row in rows:
            self._forget(row["day"], row["conversation_id"] or "")
        return len(rows)

    def _remind(self, recap: Recap) -> None:
        day = date.fromisoformat(recap.day)
        self.events.publish("checkin", "Bilan en attente",
                            prompts.REMINDER.format(date=french_date(day), iso=recap.day)[:MAX_TEXT],
                            payload={"day": recap.day, "conversation": recap.conversation_id, "stage": "reminder"},
                            targets=SURFACES, source="recap", conversation_id=recap.conversation_id)

    # ------------------------------------------------------------------
    # Closing
    # ------------------------------------------------------------------
    async def _close_older(self, key: str) -> None:
        for row in self.db.all("SELECT day FROM recaps WHERE status = 'asked' AND day < ? ORDER BY day", (key,)):
            await self._close(row["day"])

    async def _close(self, key: str) -> None:
        """Write the recap of an unanswered day from its digest alone."""
        recap = self.row(key)
        if recap is None or recap.status != "asked" or not recap.conversation_id:
            return
        try:
            await self.s.agent.answer(ChatRequest(
                message=prompts.CLOSING_MESSAGE, client="server", conversation=recap.conversation_id,
                timezone=self.zone_name, mode="unattended", kind="recap", quiet=True))
        except ServerStopping:
            return
        except Exception as error:
            log.warning("recap %s: could not close (%s: %s)", key, type(error).__name__, error)
            self._set(key, error=f"closing failed: {type(error).__name__}: {error}"[:300])
            return
        closed = self.row(key)
        if closed is None or closed.status != "closed":
            self._set(key, error="closing: Clara wrote no recap")
            self.events.publish("notification", "Bilan sans réponse",
                                f"Je n'ai pas pu écrire le récap du {french_date(date.fromisoformat(key))} sans ta réponse.",
                                source="recap", targets=SURFACES, conversation_id=recap.conversation_id)
            return
        day = date.fromisoformat(key)
        self.events.publish("checkin", "Bilan clos sans réponse",
                            prompts.CLOSED_NOTICE.format(date=french_date(day), path=closed.note_path, iso=key)[:MAX_TEXT],
                            payload={"day": key, "conversation": closed.conversation_id, "stage": "closed"},
                            targets=SURFACES, source="recap", conversation_id=closed.conversation_id)

    def mark_written(self, key: str, path: str, closed: bool) -> None:
        """The recap is in the note. Written in a chat, it is announced to Discord (which removes the check-in's
        thread); closed from the digest, `_close` announces it."""
        self._set(key, status="closed" if closed else "written", note_path=path, finished_at=self._stamp(), error="")
        recap = self.row(key)
        if closed or recap is None:
            return
        self.events.publish("checkin", prompts.WRITTEN_TITLE,
                            prompts.WRITTEN_NOTICE.format(date=french_date(date.fromisoformat(key)), path=path),
                            payload={"day": key, "conversation": recap.conversation_id, "stage": "written", "path": path},
                            targets=("discord",), source="recap", conversation_id=recap.conversation_id)

    # ------------------------------------------------------------------
    # What Clara is told in a check-in conversation
    # ------------------------------------------------------------------
    def context_block(self, state) -> str:
        recap = self.for_conversation(state.conversation_id)
        if recap is None or self.s.vault is None:
            return ""
        day = date.fromisoformat(recap.day)
        template = {"closed": prompts.CLOSED, "written": prompts.WRITTEN}.get(recap.status, prompts.ASKED)
        return template.format(date=french_date(day), iso=recap.day, path=self.s.vault.daily_path(day))

    # ------------------------------------------------------------------
    # The console
    # ------------------------------------------------------------------
    def describe(self) -> str:
        head = (f"Daily recap: {'on' if self.enabled else 'off (CLARA_RECAP=false)'}, asked at {self.at.strftime('%H:%M')} "
                f"({self.zone_name}), reminded at {self.remind_at.strftime('%H:%M')} the next morning.")
        rows = [f"  {r.day}  {r.status:<8} {r.note_path or r.error or ''}".rstrip() for r in self.listing(7)]
        return "\n".join([head, *rows]) if rows else head + "\n  (nothing asked yet)"

    def ask_now(self, day: date) -> str:
        key = day.isoformat()
        if self.s.vault is None:
            raise RecapError("The daily recap needs the vault (CLARA_VAULT_PATH).")
        recap = self.row(key)
        if key in self._running or (recap and recap.status == "asking"):
            raise RecapError(f"{key} is being asked now.")
        if recap and recap.status not in ("failed", "skipped"):
            raise RecapError(f"{key} is already {recap.status}: {recap.note_path or 'answer it in its conversation'}.")
        self._spawn(day)
        return f"Asking for {key}: the check-in arrives on the web site and Discord."

    async def close_now(self, day: date) -> str:
        key = day.isoformat()
        recap = self.row(key)
        if recap is None or recap.status != "asked":
            raise RecapError(f"{key} is not waiting for an answer.")
        async with self._turn:
            await self._close(key)
        closed = self.row(key)
        return (f"Closed {key}: {closed.note_path}." if closed and closed.status == "closed"
                else f"Could not write the recap of {key}: {closed.error if closed else 'unknown'}.")

    def skip(self, day: date) -> str:
        key = day.isoformat()
        recap = self.row(key)
        if recap is None:
            self.db.execute("INSERT INTO recaps (day, status, timezone, finished_at, created_at) VALUES (?, 'skipped', ?, ?, ?)",
                            (key, self.zone_name, self._stamp(), self._stamp()))
        elif recap.status in ("asked", "failed"):
            self._set(key, status="skipped", finished_at=self._stamp())
        else:
            raise RecapError(f"{key} is {recap.status}: nothing to skip.")
        return f"{key} will not be recapped."
