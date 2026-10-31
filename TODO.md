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
