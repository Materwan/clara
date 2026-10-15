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

## Confirmed from the plan

- One token per client (terminal, web, console, Discord bot, each script), not one shared token.
- Scripts have no network unless a run asks for it and you approve.
- Python 3.12 is the baseline (CI also runs 3.13).
- A new repository, versioned 1.0; nothing is migrated from the old server, which stays readable for a few weeks.

## Made while building (to confirm)

| Choice | Why | To change |
| --- | --- | --- |
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
