# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One person: the owner, who runs Clara on their own always-on Linux machine and reaches it only from their own devices over Tailscale. There are no accounts, roles or other users, and nobody else needs to understand the site without the owner's help (confirmed). The web client is designed around four situations, all confirmed as first-order:

- **Desk, long sessions:** chatting with Clara, reading long answers, working in projects, notes and files for an hour or more on a large screen.
- **Phone, quick checks:** over the tailnet on a phone: ask a question, answer an approval, glance at what she did. A mobile app is a non-goal, so the site must hold up on a phone.
- **Supervising her:** approvals, rules, automation (schedules, hooks), alerts, logs and usage: keeping an agent that acts on the owner's behalf under control.
- **Setting up connections:** connecting GitHub, Google Drive, Gmail, Discord and model providers; occasional but high-stakes configuration.

The interface language is English (confirmed). Clara herself answers in whatever language the owner writes in; the owner writes French to her, and the daily-note schedule prompt is French.

## Product Purpose

Clara is a personal AI agent server: she chats, remembers what matters about the owner, keeps reminders, tasks and scheduled jobs, reads and writes files in her own git-backed workspace, runs scripts in isolated rootless-Podman containers, and works with GitHub, Google Drive, Gmail, an Obsidian vault and the web. The web site is one of several clients (terminal chat, admin console, Discord bot, HTTP API) over the same API and the same list of conversations. It is where the owner talks to Clara most, and where they inspect and steer what she is allowed to do.

Success is that one person can delegate real work to an agent and stay in control of it: every action visibly passes through rules the owner set (allowed, asked, or refused), and nothing she does is a surprise.

## Positioning

An agent built for exactly one person, on that person's own machine, with no accounts, credits or public exposure. Every tool call passes a rule gate with approvals, an audit log and "tainted turn" protection against content that tries to instruct her; every change she makes to files is a git commit the owner can undo. A hosted assistant or multi-user product cannot truthfully claim that perimeter or that accountability.

## Operating Context

- Served by `clara-server` on 127.0.0.1, exposed only inside the tailnet by `tailscale serve` (HTTPS); signed in with a pasted web token that becomes a cookie. The site's CSP allows scripts, styles, fonts and connections from itself only; the Drive picker is the single exception.
- Svelte 5 + TypeScript in `web/`, built in a pinned Node container into `src/clara/web/dist` and shipped in the release; the server machine needs no Node.
- Approvals can be answered from the web site, another client, or Discord buttons; unattended runs (reminders, tasks, schedules, hooks) go through the same gate.
- Conversations come from every client (web, terminal, Discord, console) and share one list; incognito and local-only conversations have privacy rules that the interface must respect.
- Clara's pages: Chat, Projects, Library, Tasks, Automation (schedules and hooks), and under Settings: Memory, Connections, Rules & approvals, Models, Usage, Admin.

## Capabilities and Constraints

- The exact list of tools, routes, settings and limits is [FEATURES.md](FEATURES.md); it is tested against the code, so a new tool, route, setting or event kind must be named there. Why things are built the way they are is [docs/DECISIONS.md](docs/DECISIONS.md); what is deliberately left out is [docs/NON-GOALS.md](docs/NON-GOALS.md). Read both before changing a behaviour they name.
- Single user, Linux with systemd and rootless Podman only; no plug-ins or arbitrary MCP servers; no webhooks (sources are polled); no Funnel or public URL; no mobile app; no spending caps.
- Vocabulary the interface already uses and the owner knows: rule gate, approvals, rules (allow / ask / refuse), tainted turn, packs, incognito, local-only, hooks, schedules, workspace, vault, projects, tokens.
- No inline `style=` attributes and no external fonts, scripts or `data:` assets (CSP); colours, sizes and spaces come from tokens ([docs/WEB-DESIGN.md](docs/WEB-DESIGN.md)).
- Needs a browser from 2024 or later (`light-dark()`); light and dark themes follow the system and can be forced.
- Still open: whether the site ever gains a second interface language. English only for now.

## Brand Commitments

- **Name and persona:** "Clara", one fixed persona (`src/clara/defaults/persona.md`): warm, direct and a bit witty; never cold, never clownish; correct and useful first, humour second. No mood or relationship score changes her tone.
- **Mark:** Clara's portrait (`web/src/assets/clara.png`) is the logo, the favicon and the avatar beside her answers; its ring colour is the one live signal on the site.
- **Existing visual identity:** the owner chose and recorded the current site design in [docs/DECISIONS.md](docs/DECISIONS.md) and [docs/WEB-DESIGN.md](docs/WEB-DESIGN.md) ("Cyanotype"). It is the incumbent, not a decision to reopen here; DESIGN.md has not been written.

## Evidence on Hand

- A running Svelte site with all pages above, a tested rule gate, and design-system documentation in `docs/WEB-DESIGN.md`.
- Real product docs: `README.md`, `FEATURES.md`, `docs/`, `CHANGELOG.md`, `clara-rebuild-plan.md`.
- There are no testimonials, users, customers, public pages, usage numbers or press, and none should be invented: this is a private tool and the source is "all rights reserved".

## Product Principles

1. **One person, fully trusted, fully in control.** Design for a single owner who knows the system; never for onboarding strangers, selling, or sharing.
2. **No action without a visible rule.** What Clara did, what waits for the owner, and what was refused must be unmistakable and quick to answer, on a phone as much as at a desk.
3. **Plain words for consequences.** A rule, a permission, a connection or a secret says exactly what it allows and what it risks; high-stakes setup is never decorative or ambiguous.
4. **Calm for hours, fast for seconds.** The chat and working pages are for long reading and writing; the approval and status paths are for a glance. Both must work without ceremony.
5. **Everything is explained by the code and the docs.** The interface does not claim, promise or simplify beyond what FEATURES.md states.

## Accessibility & Inclusion

The site already holds itself to a documented standard in `docs/WEB-DESIGN.md`: text contrast 4.5:1 and control borders and marks 3:1 in both themes, every control named, state carried by ARIA attributes, keyboard-complete (skip link, focus kept in drawers and dialogs), 24 px minimum targets (36 px on touch), reduced-motion and forced-colours support, and text sized in rem. New work keeps all of it (WCAG 2.2 AA at minimum).
