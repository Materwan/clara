# TODO

Planned work, not built yet. Each item says what the owner asked for; the notes under it are open questions and the
constraints the current design already sets, not decisions. When an item is built, name its pieces in
[FEATURES.md](FEATURES.md) (the docs test requires it), record the choice in [docs/DECISIONS.md](docs/DECISIONS.md) as a
dated entry, and remove it from here.

## The firewall: what is left

The firewall against tainted conversations is built (`docs/DECISIONS.md`, « A firewall against tainted conversations »).
What it leaves, not decided:

- GitHub, Drive, folder files and the events of GitHub hooks keep the taint flag only: they are not read by the quarantined reader.
- The reader is on by default. Its cost (one call for a page, a mail or an attachment, one for a list) is in the usage log under the
  kind `reader`; there is no per-day cap.
- A script's reading of a marked file is not followed (`docs/THREAT-MODEL.md`).
- The raw text of a page or a mail is not shown to you in the chat: only the report is. A card that opens the raw text, for you and not for
  the model, would need a client-side change.

## Web site speed and failed loads

The owner asked to make the web site faster and to fix two failures:

1. Some conversations take a long time to load, or fail to fetch.
2. Deleting a conversation, or moving it into a project, can take up to 30 seconds. While it runs, the site does not
   respond: reload does not work, and opening another conversation does not work either.

Open questions, not decided:

- Where does the time go: the server, the database, the network, or the page waiting on a request? Nothing is measured
  yet.
- Which conversations are slow: the long ones (many messages, large tool results), or all of them?
- Should a delete or a move answer at once and finish in the background, or stay synchronous and become fast?

Constraints the current design already sets, and candidate causes to check first (not findings):

- There is one SQLite connection, shared by the event loop and the worker threads behind a lock. A long write holds
  the lock, so every other call on the connection waits (`db/connection.py`).
- Calls made directly from async code run on the event loop. A blocking call there would stall every request, which
  would also explain a reload that hangs (`CLAUDE.md`, « Persistence »).
