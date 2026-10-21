"""The daily note, as a schedule: every day at 23:59 Clara reads the day with the packs' tools, asks you one question on the
web site and Discord, waits for your answer, then writes the day into a block of the daily note of the vault.

It is made once at start (when there is a vault), as an ordinary schedule: its prompt, time and packs are yours to
change on the Schedules page, or to delete (it is not made again). The days of the earlier, built-in recap (table
`recaps_legacy`) become runs of it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, time
from typing import TYPE_CHECKING

from ..timeutil import iso, now_iso, resolve_local, zone

if TYPE_CHECKING:
    from ..services import Services

log = logging.getLogger(__name__)

NAME = "Bilan du jour"
AT = "23:59"
SEED_KEY = "daily_note_schedule"
PACKS = ["second_brain", "day_conversations", "day_activity", "files_created", "ask_person", "discord_thread"]
LEGACY = {"asked": "waiting", "written": "ok", "closed": "closed", "failed": "failed", "skipped": "skipped"}

PROMPT = """\
Tu fais le bilan de la journée de la personne, dans sa note du jour du vault (le second cerveau). Réponds toujours en français, en la tutoyant.

1. Lis la journée avec tes outils, sans rien écrire encore : `day_conversations` (ce qui a été dit, avec le contexte des conversations commencées avant), `day_activity` (tâches, rappels, tâches planifiées, tes actions) et `files_created` (les fichiers du jour ; lis un fichier avec `read_created_file`, de préférence en mode diff, seulement s'il t'est nécessaire). Pour approfondir une conversation : `read_conversation`.
2. Pose UNE seule question à la personne avec `ask_person` : chaleureuse, sans titre, de 500 à 1 500 caractères. Quelques puces courtes sur ce que tu as vu de sa journée, pour qu'elle confirme ou corrige ; les tâches encore ouvertes qui semblent faites (par leur titre) ; ce qu'elle a fait d'autre en dehors de vos discussions (sport, courses, appels, rendez-vous, lectures, sorties…). Dis que tu attends sa réponse avant d'écrire le récap dans la note du jour. N'invente rien : appuie-toi sur ce que tu as lu. Puis termine ton tour : tu n'écris rien avant la réponse.
3. Quand la personne répond, prends en compte ce qu'elle confirme ou corrige ; pose au plus une question de suivi dans le chat si un point reste flou. Dès que tu as assez d'éléments, montre un aperçu du récap dans le chat, puis écris-le avec `vault_daily_block` (bloc `recap`, titre « Récap (Clara) »). Si elle corrige ensuite, rappelle `vault_daily_block` avec le récap complet corrigé. Marque une tâche faite avec `update_task` (status done) seulement si elle l'a confirmée. Termine avec `finish_run` (une à trois phrases : où est le récap).
4. Contenu du récap (markdown, sans titre de niveau 1 ou 2) : **Discussions** (une puce par sujet, avec ce qui a été décidé ou produit), **Fait hors de Clara** (ce que la personne a dit avoir fait), **Fichiers** (ce qui a été créé ou modifié et vaut d'être noté), **Tâches terminées** (cases cochées `- [x]`), **À confirmer** (ce qui n'est pas confirmé). Omets une rubrique vide. Une puce = une phrase. Lie les notes du vault avec `[[Nom de la note]]` (celles que `day_conversations` ou `files_created` donnent, ou trouvées avec `vault_search`) : seulement des notes qui existent, par leur nom, sans chemin ni extension.
5. Si le message commence par « [No answer received] », personne n'a répondu : écris le récap à partir de ce que tu as lu, tout ce qui n'est pas confirmé sous « À confirmer », ne marque aucune tâche comme faite, puis appelle `finish_run`.
"""


def _legacy(s: Services, schedule_id: int, packs: list[str]) -> int:
    """The days of the earlier recap table become runs of the schedule; the check-in conversations keep working."""
    if not s.db.one("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'recaps_legacy'"):
        return 0
    moved = 0
    unreadable: list[str] = []
    for row in s.db.all("SELECT * FROM recaps_legacy ORDER BY day"):
        status = LEGACY.get(row["status"])
        if status is None:
            log.info("recap of %s not moved: it was only being asked (status %s)", row["day"], row["status"])
            continue
        try:
            due = iso(
                resolve_local(
                    datetime.combine(datetime.fromisoformat(row["day"]).date(), time(23, 59)), zone(row["timezone"])
                )
            )
        except ValueError:
            unreadable.append(f"{row['day']!r} ({row['timezone']!r})")
            continue
        s.db.execute(
            "INSERT INTO schedule_runs (schedule_id, due_at, started_at, finished_at, status, summary, conversation_id,"
            " waiting_since) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                schedule_id,
                due,
                row["asked_at"],
                row["finished_at"],
                status,
                row["note_path"] or row["error"],
                row["conversation_id"],
                row["asked_at"] if status == "waiting" else None,
            ),
        )
        if row["conversation_id"] and s.conversations.get(row["conversation_id"]):
            s.conversations.update(row["conversation_id"], packs=packs)
        moved += 1
    if unreadable:  # the table is the only copy of these days: it stays, so nothing is lost without a trace
        log.warning(
            "%d earlier recap(s) could not be moved (unreadable day or time zone: %s); the table recaps_legacy is kept",
            len(unreadable),
            ", ".join(unreadable),
        )
        return moved
    s.db.execute("DROP TABLE recaps_legacy")
    return moved


def seed(s: Services) -> int | None:
    """Make the daily-note schedule, once, when there is a vault. Returns its id, or None when nothing was done."""
    if s.vault is None or s.db.one("SELECT 1 FROM settings WHERE key = ?", (SEED_KEY,)):
        return None
    tz = s.settings.timezone
    today = s.schedules.local_now(tz).date()
    schedule = s.schedules.create(
        NAME,
        PROMPT,
        f"{today.isoformat()}T{AT}",
        timezone=tz,
        repeat="daily",
        packs=PACKS,
        per_run=True,
        missed="latest",
        overlap="replace",
    )
    moved = _legacy(s, schedule.id, PACKS)
    s.db.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)", (SEED_KEY, json.dumps(schedule.id), now_iso())
    )
    log.info(
        "made the schedule “%s” (every day at %s)%s",
        NAME,
        AT,
        f"; {moved} earlier day(s) moved into it" if moved else "",
    )
    return schedule.id
