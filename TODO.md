# TODO

Planned work, not built yet. Each item says what the owner asked for; the notes under it are open questions and the
constraints the current design already sets, not decisions. When an item is built, name its pieces in
[FEATURES.md](FEATURES.md) (the docs test requires it), record the choice in [docs/DECISIONS.md](docs/DECISIONS.md) as a
dated entry, and remove it from here.

## A memory shared by all the conversations of a project or an automation

**Asked for:** each project and each automation (a schedule or a hook) gets a memory of its own, common to all its
conversations and runs. What Clara learns in one of them, the next one knows. Example: in the dedicated automation you tell
Clara to remove a certain type of mail; the next run of that automation already knows it and does it without being told again.

**Today:** `remember`, `recall_facts` and `forget` keep facts that belong to nobody in particular (`memory/facts.py` has no
project or automation scope), so what Clara learns is either shared by everything or kept in one conversation. A project has
instructions and files that are context for its conversations. A schedule can share one conversation between its runs, but
a schedule set to one conversation per run, and every run of a hook, start without what the earlier runs were told.

**To decide before building**

- The scope: a fact that belongs to a project, to a schedule, to a hook, or to none (as now). When a hook has a project,
  whether its runs read both memories.
- How a fact gets in: Clara saves it herself with `remember` (in a run of an automation it would go to that automation's memory
  by default), or only when you ask. And how it comes out: put in the prompt for every conversation of the scope, or only
  found by `recall_facts`. Prompts are never silently truncated, so the size needs a limit.
- Where you see and correct it: a list on the project's page and on the automation's page, to edit or delete a fact;
  `expires` and `replaces` already exist on facts. How it differs from a project's instructions (text you write yourself).
- What happens to it when the project or the automation is deleted: it goes with it, asked and approved like its conversations.
- Incognito and local-only conversations: they should neither read it nor write to it.
- It lives in the database, so backups carry it; a numbered migration adds the scope (migrations only append).

**Constraints already in the design**

- A note planted by untrusted content would steer every later run of the automation, not just one. Check how the tainted-turn
  rule treats `remember` today, and keep a scoped memory at least as strict: a fact saved after mail or a web page was read
  asks, or is marked as coming from untrusted content.
- A new tool argument, route, setting or table must be named in [FEATURES.md](FEATURES.md); the tool count line changes if a
  tool is added.
