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
