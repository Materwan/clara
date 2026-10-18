# Decisions

Why Clara 1.0 is built the way it is. The plan ([clara-rebuild-plan.md](../clara-rebuild-plan.md)) set the direction;
the questions below were answered by the owner before the rewrite (October 2026); the last section lists the choices
made while building, to be confirmed or changed.

## Answered by the owner

| Question | Decision |
| --- | --- |
| Scope of the rewrite | All six phases of the plan |
| How releases are made | GitHub Releases built by CI (`release.yml`); the repository is public; CI pushes the sandbox image to ghcr.io and each release pins it by digest |
| Containers for scripts | Rootless Podman only; no Docker fallback (rootful Docker is refused) |
| Service layout | A system unit and a dedicated `clara` user |
| Python tooling | uv with a committed `uv.lock` |
| Web site | Svelte 5 with TypeScript, built in a pinned Node image (no Node on the machine) |
| Web site design | The first site's design (Cyanotype: Prussian-blue rail in both themes, Hanken Grotesk and Epilogue, Clara's portrait as the mark, the favicon and beside answers, line icons), on the 1.0 layout, with the conversation list moved into the rail |
| Web site navigation | The pages you work in stay in the rail, the list of conversations under them on every page (the logo and New chat stay put, the rest of the rail scrolls as one); Memory, Connections, Rules & approvals, Models, Usage and Admin sit under one **Settings** entry with a tab bar; waiting approvals show as a button at the foot of the rail, alerts as a count on Settings |
| Web site readability | Two steps under the first site's sizes (body 13 px, answers and what you write 14 px, small text 11.5 px; titles 16 px), muted text darker (lighter in dark theme): about 6:1 against the page instead of 5:1 |
| Checkboxes | Drawn, not the browser's: a 16 px box with a 2 px border that fills violet with a white tick, the same in both themes |
| Theme | Auto / Light / Dark switch in the rail, remembered in the browser; each colour is written once as `light-dark()` (browsers from 2024), so the two themes cannot drift apart |
| Daily recap: how Clara gets the day | A digest made in code and joined to the check-in's first message (no tools for the model to call, no tainted turn, the day cut in the server's zone) |
| Daily recap: which conversations | Normal, project, scheduled and reminder ones, with the earlier context of those begun before; never incognito or local-only ones |
| Daily recap: which other sources | Tasks, reminders and scheduled runs; the vault's changes of the day (git); what Clara did through the gate. Not GitHub or Drive |
| Daily recap: asking | At 23:59 if the server runs, else at the next start; on the web site and in a Discord thread under the question in `#general`. Clara writes nothing before you answer |
| Daily recap: finishing | Clara judges when she has enough, shows a preview and writes; a correction rewrites the block. Unanswered: a reminder the next morning, and the next check-in first closes it from the digest alone (the uncertain points under « À confirmer ») |
| Daily recap: the Discord thread | Deleted by the bot once the recap is written (a line in `#general` says where the note is); the conversation stays in Clara for corrections. Archived instead when the bot may not delete |
| Daily recap: the note | One block « Récap (Clara) » between two comments, replaced on each write; what you wrote is never touched; French, whatever the day's language |
| Daily recap: a long day | Hybrid: short excerpts, and the longest conversations summarised by the side-jobs model when the day does not fit |
| Daily recap: tasks | Marked done only after you confirm them in the check-in |
| Web site tokens | One scale each for type (9 steps, in rem), space (a 2px grid), shape, motion and stacking; no literal colour, size or space outside the token block ([WEB-DESIGN.md](WEB-DESIGN.md)) |
| Web site questions | A dialog (a native `<dialog>`: inert page, Tab trapped, Escape cancels), never the browser's `confirm()` or `prompt()`: they ignore the theme, cannot mask a secret, and a browser may switch them off |
| Web site accessibility | Every control named, state carried by ARIA attributes, text 4.5:1 and marks 3:1 in both themes, targets of 24 px at least (36 px on touch), a skip link, focus kept in drawers and dialogs |
| Chat transcript | The first site's: tool calls folded into one line per run, line icons instead of emoji, the context ring |
| Discord bot token | The bot's own systemd credential, never in Clara's database |
| Licence | None: all rights reserved |
| "Remember nothing" | Incognito conversations: no facts read or written, deleted 24 hours after their last message (`CLARA_INCOGNITO_HOURS`) |
| Versions of answers (edit, ask again) | Kept |
| Markdown files Clara writes | Merged into the workspace, folder `notes/` (git history, undo) |
| The vault | Seven tools behind `open_second_brain`, opened per conversation |
| Google Drive | `drive.file` scope and the Google Picker |
| Facts from tainted turns | Kept, shown to the model tagged `[unconfirmed]` until you confirm |
| Tainted-turn rule | On by default |
| Discord and approvals | Discord may answer approvals except destructive ones |
| Client-side tools protocol | Kept in the API (`clara-chat --local-tools` uses it) |
| Reminder wording | Written by the model at fire time, the stored text as the fallback |
| Remote backups | restic |
| Conversations across clients | One shared list |
| Local-only flag | Per conversation |
| Discord bot features | Approval buttons, the event relay, files and pictures, slash commands |
| Discord layout | A private server with you and the bot only; four text channels found by id: `#general` (chat), `#approval`, `#notification`, `#alerts`; no direct messages; plain messages in `#general` (the privileged Message Content intent); reminders, tasks, schedules and update news go to `#notification` |
| Discord bot start | The server starts it as a child process when `CLARA_DISCORD_AUTOSTART=true` (the bot keeps its own process and token); `clara-discord.service` remains the other way, never both |

## Confirmed from the plan

- One token per client (terminal, web, console, Discord bot, each script), not one shared token.
- Scripts have no network unless a run asks for it and you approve.
- Python 3.12 is the baseline (CI also runs 3.13).
- A new repository, versioned 1.0; nothing is migrated from the old server, which stays readable for a few weeks.

## Made while building (to confirm)

| Choice | Why | To change |
| --- | --- | --- |
| The daily check-in is a normal chat conversation, tracked in the `recaps` table (not a new conversation kind) | It shows in the list on every client and can be answered anywhere; a new kind would mean rebuilding the `conversations` table (its `CHECK`) | — |
| The daily recap is off until `CLARA_RECAP=true`; `/recap now` works whenever there is a vault | A daily message you did not ask for would surprise an installation that never heard of it | The setting |
| The catch-up after a server that was off asks for yesterday only, and the first start with the recap on asks for yesterday too | Older days would arrive all at once; a day with no conversation can still be recapped (« what did you do? ») | `/recap skip <day>` |
| `write_daily_recap` is bound to the check-in's day and takes only the text | The model cannot write another day's note by mistake | — |
| The opening message is written by the model with no tools, from the digest | Fast, cheap, and it cannot change anything before you answered | — |
| A streamed chat waits 10 minutes (`CLARA_APPROVAL_WAIT`) for an approval, then goes on without the action; the request stays valid 24 h, and a later answer runs it and starts a short follow-up turn | You often answer within a minute; a turn should not hang for a day | The setting |
| `POST /v1/chat` (not streamed) never waits: the request is pushed to your clients | A script calling the API cannot answer an approval itself | — |
| A chat request not answered in 60 s is pushed to your other clients too | You may have walked away from the screen | — |
| Tokens look like `clara_<id>_<secret>` | The id finds the row without a scan; the prefix lets secret scanners (Clara's own, GitHub's) recognise a leaked token | — |
| Approval scopes per token: `all` (web, terminal, console), `safe` (Discord: not destructive), `none` (scripts) | The plan's Discord risk, and scripts must never approve their own requests | `/tokens approvals` |
| Secret-looking content leaving the machine raises to `ask`; secrets are never stored as facts | The review's item 10 | — |
| Forgotten facts leave a tombstone (their folded text) | Otherwise the model saves the same fact again at once | — |
| `search_history` searches earlier conversations (FTS5) | The review's item 28 | — |
| The answer budget counts model time only, not time waiting for you or for tools | Waiting for an approval should not eat the budget | `CLARA_ANSWER_BUDGET` |
| A model silent past the first-token timeout hands over to the next fallback; a spent budget does not | A stalled provider is the commonest outage; nothing was said, so nothing repeats | — |
| Updates go through `clara-launcher`: the server exits 75 for an update, 76 for a restart; the launcher switches the `current` link and rolls back | The new code is checked before the old one stops, and something outside the server can undo a failed start | — |
| Rule specificity: fields set, then literal characters in the target, then in the tool, then strictness | Predictable and explainable in the test box | — |
| Local-only conversations also hide the web, GitHub and Drive tools, and the gate refuses anything leaving the machine (vault changes too when the vault pushes) | "Local only" must hold for tool calls, not only for the model | — |
| A message carries 4 files and 34 MB in all | Base64-encoded, that fits the 48 MB request limit (the review's item 51) | — |
| The tool count, routes, settings and alerts in FEATURES.md are checked by a test | The review's item 50 | — |
| Release signing is optional but, once `CLARA_RELEASE_PUBKEY` is set, required | A public repository; a GitHub account takeover should not be enough to ship code to the machine | `scripts/release_signing.py new` |
| GitHub Actions are pinned by major version, updated by Dependabot pull requests | Commit-SHA pinning is stronger; it can be switched to when the workflows settle | `.github/` |
