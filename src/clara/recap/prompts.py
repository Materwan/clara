"""What Clara is told, and tells, for the daily check-in. The person asked for French, whatever the day's language.

The instructions avoid pronouns for the person ("tu" to speak to them, "la personne" to speak of them).
"""

from __future__ import annotations

OPENING = (
    "C'est la fin de la journée de la personne. Tu ouvres son bilan du jour avec UN seul message, en français, en la "
    "tutoyant, chaleureux et sans titre ni emphase (500 à 1 500 caractères). Le digest de la journée est joint au "
    "message qui te demande ce bilan.\n"
    "1. Dis en quelques puces courtes ce que tu as vu de sa journée (conversations, tâches terminées, notes "
    "écrites ou modifiées) pour que la personne confirme ou corrige. N'invente rien : appuie-toi sur le digest.\n"
    "2. Si le digest liste des tâches encore ouvertes qui semblent faites, demande lesquelles le sont vraiment (par "
    "leur titre).\n"
    "3. Demande ce qui a été fait d'autre en dehors de vos discussions (sport, courses, appels, rendez-vous, lectures, "
    "sorties…). Si le digest est vide, dis-le simplement et pose cette question.\n"
    "4. Termine en disant que tu attends la réponse avant d'écrire le récap dans la note du jour.\n"
    "Ne pose pas d'autre question. Pas d'outil pour ce message."
)

ASKED = (
    "## Bilan du jour — {date}\n"
    "Cette conversation est le bilan de fin de journée du {iso}. Le digest de la journée est joint à son premier "
    "message. Tu attends les réponses de la personne avant d'écrire quoi que ce soit.\n"
    "- Réponds en français, en tutoyant. Prends en compte ce qui est confirmé ou corrigé ; pose au plus une question de "
    "suivi si un point reste flou.\n"
    "- Dès que tu as assez d'éléments : montre un aperçu du récap dans le chat, puis écris-le avec `write_daily_recap` "
    "(il remplace le bloc « Récap (Clara) » de la note du jour, {path}). Ne l'écris pas avant d'avoir une réponse.\n"
    "- Contenu du récap (markdown, sans titre de niveau 1 ou 2) : **Discussions** (une puce par sujet, avec ce qui a été "
    "décidé ou produit), **Fait hors de Clara** (ce que la personne a dit avoir fait), **Tâches terminées** (cases "
    "cochées `- [x]`), **À confirmer** (ce qui n'est pas confirmé). Omets une rubrique vide. Une puce = une phrase.\n"
    "- Liens : ajoute `[[Nom de la note]]` vers les notes du vault liées (celles du digest, ou trouvées avec "
    "`vault_search`). N'écris que des notes qui existent, par leur nom, sans chemin ni extension.\n"
    "- Tâches : quand la personne confirme qu'une tâche est faite, marque-la avec `update_task` (status done) ; ne marque "
    "jamais une tâche que la personne n'a pas confirmée.\n"
    "- N'invente rien : ce que la personne ne confirme pas va sous « À confirmer ».\n"
    "- Une fois écrit, dis-le en une phrase avec le nom de la note. Si la personne corrige ou complète, rappelle "
    "`write_daily_recap` avec le récap complet corrigé."
)

WRITTEN = (
    "## Bilan du jour — {date}\n"
    "Le récap de cette journée est écrit dans {path}. Si la personne corrige ou complète quelque chose, rappelle "
    "`write_daily_recap` avec le récap complet corrigé (il remplace le précédent) ; marque une tâche faite avec "
    "`update_task` seulement si la personne l'a confirmée. Sinon, réponds simplement."
)

CLOSED = (
    "## Bilan du jour — {date}\n"
    "La personne n'a pas répondu à temps : le récap de cette journée a été écrit dans {path} à partir du seul digest, "
    "avec les points incertains sous « À confirmer ». Si la personne répond maintenant, mets le récap à jour "
    "avec `write_daily_recap` (récap complet), en tenant compte de la réponse, et marque une tâche faite avec "
    "`update_task` seulement si la personne l'a confirmée."
)

OPENING_MESSAGE = "Bilan de la journée du {date}"

CLOSING_MESSAGE = (
    "[Aucune réponse reçue] Une nouvelle journée a commencé et la personne n'a pas répondu à ce bilan. Écris maintenant "
    "le récap de la journée avec `write_daily_recap`, à partir du seul digest : rien de ce que tu y as lu n'a été "
    "confirmé, donc mets sous « À confirmer » ce qui est incertain. Ne marque aucune tâche comme faite. Puis réponds en "
    "une phrase."
)

SUMMARY = (
    "Résume les extraits de conversation ci-dessous en 5 à 8 lignes factuelles, en français : ce qui a été demandé, "
    "décidé, produit ou fait (notes, fichiers, tâches), et ce qui reste en suspens. Garde les noms propres, les chiffres "
    "et les titres de notes. Pas d'introduction ni de conclusion."
)

ASKED_TITLE = "Bilan du {date}"
WRITTEN_TITLE = "Récap écrit"
WRITTEN_NOTICE = "Le récap du {date} est écrit dans {path}."
REMINDER = "Le bilan du {date} attend toujours ta réponse : réponds dans la conversation « Bilan du {iso} » pour que j'écrive la note du jour."
CLOSED_NOTICE = (
    "Tu n'as pas répondu au bilan du {date} : j'ai écrit le récap à partir de ce que j'avais vu ({path}). Tu peux encore "
    "répondre dans la conversation « Bilan du {iso} » pour le corriger."
)
