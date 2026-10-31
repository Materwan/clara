# TODO

Planned work, not built yet. Each item says what the owner asked for; the notes under it are open questions and the
constraints the current design already sets, not decisions. When an item is built, name its pieces in
[FEATURES.md](FEATURES.md) (the docs test requires it), record the choice in [docs/DECISIONS.md](docs/DECISIONS.md) as a
dated entry, and remove it from here.

## A firewall against tainted conversations

The owner asked for a firewall against prompt injection from untrusted sources (web pages, search results, mail and
attachments): a conversation that has read such content must not be steered by it. Two steps, in this order:

1. **Make the taint rule hold.** Search queries and fetched addresses are checked as content leaving the machine, so
   private text cannot ride out in a URL or a query.
2. **A quarantined reader.** Web and mail content is read by a separate model call with no tools and no memory. The
   conversation gets only a typed result (a summary or named fields), never the raw text. Each source has its own
   result shape.

GitHub, Drive, folder files and hook events keep the taint flag only, for now.

Open questions, not decided:

- Does `/tainted off` still lift the rule? Today it does; the owner did not say.
- Which model runs the quarantined reader, and what does it add to the cost of a turn?
- How is private text in an address detected? The secret check is pattern based (`secret_kinds`); a search query is free text.
- Do unattended runs and hooks get the quarantined reader too? A hook's run starts tainted from its first word
  (`triggers/setup.py`).
- What do the typed results look like for a mail, a page and a search?

Constraints the current design already sets:

- The last auto level of a chat suspends the tainted-turn rule and the secret check. That is the owner's choice
  (`docs/DECISIONS.md`, « Auto levels »), and the firewall does not override it.
- The model has no tool that reads, edits or approves a rule, and a raise only makes a call stricter (`gate/engine.py`).
- Facts saved in a tainted turn are stored unconfirmed (`memory/facts.py`).
- Unattended runs never wait for an approval, and a hook's run cannot change GitHub without asking
  (`docs/DECISIONS.md`).
- A search query can carry text out to the engines: a known residual risk (`docs/THREAT-MODEL.md`).

## Automatic deletion of automation conversations

The owner asked for a rule that deletes the conversations automations make, after a number of days or hours, or after a
number of conversations. Chosen so far:

- **Limit:** both, whichever comes first: an age limit (days or hours since the last message) and a count limit (the
  latest N conversations of each schedule or hook).
- **Scope:** the conversation each hook run makes, and each conversation a schedule makes when it runs per run. A
  schedule's shared conversation is not in scope. Reminders and tasks are not in scope either: they post into the chat
  they were set from.
- **Kept whatever the limit:** a run waiting for an answer, a pinned conversation, an archived one.
- **Default:** off until a limit is set.

Open questions, not decided:

- The default numbers of days, hours and conversations.
- Whether a conversation shown in history is deleted too (the owner did not choose to keep those).
- Whether the limits are runtime knobs (changed on the Admin page) or start-up settings (`CLARA_*`).
- Whether a deleted run's facts go too: a schedule's or hook's facts are scoped to it and kept by today's design.
- What « last message » means for a run that waited and was then answered.
- Whether a count limit counts conversations already deleted by the age limit.

Constraints the current design already sets:

- Incognito conversations are deleted 24 hours after their last message (`CLARA_INCOGNITO_HOURS`, FEATURES.md).
- Deleting a conversation removes its messages and files; `secure_delete` overwrites them, and backups keep them for 14
  days and 8 weeks (`docs/REVIEW-RESPONSE.md`, row 14).
- A run that waits is a normal chat conversation (`docs/DECISIONS.md`).
- Retention already runs hourly in `ops/upkeep.py`, for usage, audit and events.
- Messages and facts are kept until you delete them, by choice (`docs/REVIEW-RESPONSE.md`, row 31).
