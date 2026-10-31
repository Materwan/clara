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

## Deferred tool calls

The owner asked for a system of automatic tool call execution: Clara decides now that a tool call runs later, not now.
Example: she trashes a mail (`mail_trash`) in 2 days instead of now.

What the owner chose:

- **Approval when scheduled.** The owner approves the exact call and its date once. The rules are checked again when it
  is due, and a rule that refuses still stops it.
- **Any tool.** The action class and the rules decide the call at the due time, whatever the tool.
- **The server runs it, with the saved arguments.** No model turn at the due time. If the mail is already gone, the
  error is reported to the owner.
- **Booked by Clara in a chat only.** Like the Automations tool, the request is read first. The owner can cancel a booked
  call at any time.

Open questions, not decided:

- A call due in unattended mode that a rule now asks about: does it fail and notify, or is it held? Unattended runs
  never wait for an approval.
- Where are booked calls listed: on the Tasks & reminders page, the Automation page, or both?
- What is stored for a booked call, and where does its result appear (the conversation that booked it, or a notice)?
- Is there a limit on the number of booked calls, and on how far ahead a call may be due?

Constraints the current design already sets:

- A pending approval expires after 24 hours (`gate/approvals.py`), so a call due later needs an approval that lives until
  its due time.
- A change allowed for one number must not do more at the due time. A limit change already works this way: it compares
  the number the owner allowed with the number it finds (`packs/automations.py`, `_told`, `_update`), and a deferred call
  should do the same.
- A call booked in a turn that read outside content (mail, a web page) is tainted, so it asks when booked. The server
  runs the saved call later with no conversation behind it, so the approval must be enough by itself.
- Due times survive a restart and fire once: the time services share one loop (`timekeeping/dueloop.py`), and the
  firing transaction is a pattern the hooks use (`triggers/`).

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
