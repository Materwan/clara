# Operations

How to install, configure, run, update, back up and restore Clara. Commands run as root unless they start with
`sudo -u clara`. `clara` below means `/opt/clara/current/.venv/bin/clara`; add it to your PATH or use the full path.
Every command that reads Clara's settings needs them in its environment: as the service user,

```bash
alias clara='sudo -u clara env CLARA_ENV_FILE=/etc/clara/clara.env CLARA_DATA_DIR=/var/lib/clara \
  CLARA_SECRET_KEY="$(sudo systemd-creds decrypt --name=clara-secret-key /etc/clara/secret-key.cred -)" \
  /opt/clara/current/.venv/bin/clara'
```

## 1. Install

1. Install the prerequisites: `git`, `podman` (rootless: `uidmap`, `slirp4netns` or `passt`), `systemd`,
   `tailscale` (logged in), and uv in `/usr/local/bin`
   (`curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh`).
2. Download a release (`clara-<tag>.tar.gz`, `SHA256SUMS`, and `SHA256SUMS.sig` when releases are signed), check it
   (`sha256sum -c --ignore-missing SHA256SUMS`), unpack it and run its installer:

   ```bash
   mkdir clara && tar -xzf clara-v1.0.0.tar.gz -C clara
   sudo clara/deploy/install.sh clara-v1.0.0.tar.gz
   ```

   It says each step as it runs, and ends with `clara doctor`. Keep the three tokens it prints (console, terminal,
   web): they are shown once. It also prints how to read the secrets key: **copy the key somewhere safe, apart from
   the backups** (a password manager). Without it, the encrypted secrets in a backup cannot be read.
3. Edit `/etc/clara/clara.env`: `CLARA_TIMEZONE`, `CLARA_OLLAMA_HOSTS`, `CLARA_RELEASE_REPO` (and
   `CLARA_RELEASE_PUBKEY` once releases are signed). Every setting: [FEATURES.md](../FEATURES.md#14-settings).
4. The sandbox image, under the name Clara looks for until the first update pins the release's own:
   `sudo -u clara podman pull <the image the release names in manifest.json>`, then
   `sudo -u clara podman tag <that image> localhost/clara-sandbox:latest` (for a source install, instead
   `sudo -u clara podman build -t localhost/clara-sandbox:latest sandbox/`). Then `clara doctor` until nothing says
   FAIL.
5. `systemctl enable --now clara`. `journalctl -u clara -f` shows the start; Clara serves
   `https://<machine>.<tailnet>.ts.net` (port 443 by default).

From a source tree (development machines): `sudo deploy/install.sh --from-source .` after `scripts/build-web.sh`.

## 2. First configuration

- **Sign in:** open the address, paste the web token. In a terminal: `clara-chat --save-token` (terminal token),
  `clara-admin --save-token` (console token), with `--url https://<machine>.<tailnet>.ts.net`.
- **Models:** on the Models page (or `/provider`, `/models`, `/model`), check the local hosts, choose the default
  model, the side-jobs model and the fallbacks. For a hosted provider: `clara secrets set gemini_api_key` (the value
  is read hidden), then enable it, after reading [PROVIDERS.md](PROVIDERS.md).
- **Rules:** read the defaults on Rules & approvals; add rules for what you want allowed without asking (for example
  `allow ws_delete scratch/**`).
- **More tokens:** one per script, `clara token create backup-script script` (scripts cannot answer approvals).

## 3. Optional parts

### Web search (Brave, SearXNG)

Brave first: create a key at <https://api-dashboard.search.brave.com> (a card is requested; the plan page lists the
free monthly credit) and set it on the **Admin** page, **Secrets** tab (which links to the dashboard), or with `/secrets set brave_api_key <key>`. SearXNG is the
fallback, and the only search when there is no key. Scraped engines block an address that searches in bursts, which is
why the API comes first.

SearXNG: follow the comments at the top of `deploy/searxng/searxng.container` (a rootless quadlet of the `clara` user,
listening on 127.0.0.1:8888, with `deploy/searxng/settings.yml` and a random `secret_key`). `clara doctor` checks it.

### GitHub

Create a **fine-grained personal access token** on GitHub limited to the repositories Clara should reach, with the
permissions she needs (Contents, Issues, Pull requests: read and write; Metadata: read). Paste it on the Connections
page, then add each repository there: Clara reaches no other, and every change asks first by default. For hooks on pull
requests and pushes, read access to Contents and Pull requests is what the poll uses.

### Google Drive

1. In Google Cloud: a project; enable the **Google Drive API** and the **Google Picker API**; an OAuth consent screen
   with the scope `.../auth/drive.file` only; an **OAuth client** of type *Web application* with the authorised
   redirect URI `https://<machine>.<tailnet>.ts.net/v1/connections/google/callback`; an **API key** restricted to the
   Picker API and to that address. A consent screen left in *Testing* status gets refresh tokens that expire after 7
   days ([PROVIDERS.md](PROVIDERS.md)).
2. In `/etc/clara/clara.env`: `CLARA_GOOGLE_CLIENT_ID`, `CLARA_GOOGLE_PROJECT_NUMBER`, and `CLARA_PUBLIC_URL` if the
   Tailscale address is not the one you use. Secrets: `clara secrets set google_client_secret`,
   `clara secrets set google_picker_api_key`. Restart.
3. On the Connections page: **Connect Google Drive**, consent, check the address shown, **Attach it**. Clara makes
   her own "Clara" folder. **Open files to Clara…** opens the Picker for files and folders you want her to see.

### Gmail

Gmail uses Drive's Google Cloud project, OAuth client and redirect address (`.../v1/connections/google/callback`), with
a token of its own.

1. In the same project: enable the **Gmail API** (APIs & Services → Library). On the OAuth consent screen (Google Auth
   Platform → Data Access) add the scopes `.../auth/gmail.readonly`, `.../auth/gmail.compose` and `.../auth/gmail.modify`,
   and nothing else (Clara cannot archive, delete a message for good or change settings: the token has no scope for it,
   and `gmail.modify` cannot delete a message for good). An app that was connected before needs this scope added, and
   then Gmail connected again. The scope picker lists only
   the scopes of enabled APIs; if one is missing, paste its full URL in the *Manually add scopes* box, then *Add to
   table*, *Update* and *Save*.
2. These are Google's *restricted* scopes. An app *In production* that Google has not verified shows an « unverified
   app » warning at consent (fine for your own account) and its tokens do not expire; an app in *Testing* gets tokens
   that end after 7 days ([PROVIDERS.md](PROVIDERS.md)). Google's verification for restricted scopes asks for a security
   assessment: it is not worth it for one person.
3. Nothing new to set if Drive works (`CLARA_GOOGLE_CLIENT_ID`, the secret `google_client_secret`). On the Connections
   page: **Connect Gmail**, consent, check the address shown, **Attach it**. A Gmail API that is not turned on is told
   at that moment, before anything is stored.
4. Disconnecting revokes the Gmail token at Google. Google may count Drive's and Gmail's tokens as one grant: if Drive
   then asks to be reconnected after you disconnected Gmail, that is why (to verify with a real account).

### Mail in daily use

- **Approving an email** needs the web site or the console. Discord cannot, because sending is a `destructive` action
  and the Discord token's scope is `safe`; if you want to answer from Discord, `/tokens approvals <the bot's token> all`
  gives it that right (and to every other destructive action).
- **Letting a schedule send** is a rule you write: `allow mail_send mail:to:you@example.com` with the situation
  `unattended`. It still asks after a turn read untrusted content (an inbox read is one).
- **New-mail notifications:** the Connections page's *New email* box (or `mail_notify` on the Admin page). It looks at the
  inbox every `CLARA_MAIL_POLL_SECONDS`; a mailbox that refuses raises the `gmail` alert once.

### Hooks (Clara acts when a mail arrives, or a pull request or a push is made)

A hook is a rule you write: *when a mail matches this, run Clara with this prompt and these tools*. Nothing is on until you make
one, and the model cannot make one. On the web site, make and watch them in **Automation → Hooks**; the console does the same: The mail poll runs every `CLARA_MAIL_POLL_SECONDS` (120) and looks only while a hook (or the
notifications) listens. GitHub cannot call Clara, so a hook on `github.pull_request` or `github.push` is found by a poll too, every
`CLARA_GITHUB_POLL_SECONDS` (120), of the repositories added on the Connections page and only for the kinds a hook waits for.
A hook's run is untrusted, so each change it makes on GitHub asks you first; to see what it wants, open the approvals.

```text
/triggers add invoices '{"on": "mail.received",
    "filter": {"from": {"contains": ["@billing.example"]}, "subject": {"contains": ["invoice", "facture"]}},
    "prompt": "An invoice arrived. Read it, add a task with the amount and the due date, and tell me in one sentence.",
    "tools": ["mail_read", "add_task", "notify"], "max_per_hour": 10}'
/triggers test invoices          # the latest stored event: does it match, what would Clara receive, with which tools
/triggers runs invoices          # what it did, each with a conversation you can open
/triggers off invoices           # stops it, and drops what waited
```

The same through `POST /v1/triggers` (fields in FEATURES.md, section 5). Good habits:

- **Give the fewest tools.** With none listed a run can only search and read mail and notify. List what the job needs and no more;
  a hostile mail can steer a run only within them.
- **Test before trusting.** `/triggers test` runs nothing. To see the first real event, make the hook with `"action": "notify"` and a
  text like `"{from}: {subject}"`, which uses no model.
- **A backlog is yours to decide.** After a stop, `catch_up` is `all` by default: every mail found late gets a run, at most
  `max_per_hour` an hour, oldest first. For a long outage prefer `"catch_up": "digest"` (one run for all) and `"max_age_hours"`.
- **Sending.** An email-triggered run is tainted, so a send always asks you; answer it on the web site or the console (it is
  `destructive`, which Discord cannot approve). Add `mail_send` to the hook's tools only for a hook that needs to reply.
- **Where the runs are.** Each run is a conversation in the project « Hook: <name> ». Switch that project's *in history* on to see
  them in the web history; put files or instructions in the project to give every run the same context.
- **Cost.** A run is a model call (kind `hook` in `/usage`). The filter is cheap and uses no model, so make it as narrow as you can.

### The Obsidian vault

```bash
sudo -u clara git clone https://github.com/<you>/<vault>.git /var/lib/clara-home/vault
sudo -u clara git -C /var/lib/clara-home/vault config user.name Clara
sudo -u clara git -C /var/lib/clara-home/vault config user.email clara@localhost
```

Set `CLARA_VAULT_PATH=/var/lib/clara-home/vault`; for pushing over HTTPS, store a token limited to that repository:
`clara secrets set vault_git_token`. Restart. Clara pulls every 30 s and pushes a few seconds after her changes; a
failed sync raises the `vault_push` alert. Clara never force-pushes: a conflict stops the sync until you resolve it
in the clone.

### Discord

The bot lives in a **private Discord server with you and the bot only**, in four text channels. It has no
direct-message function.

1. In Discord: create the server and four text channels, named as you like (the bot finds them by id, not by name):
   `general` (your chat with Clara), `approval`, `notification`, `alerts`. In Discord's settings, Advanced, switch on
   Developer Mode; then right-click your name and each channel, *Copy ID*.
2. On the Discord developer portal: a new application and its bot. On the Bot page, turn **Public Bot** off (nobody
   else can invite it) and turn on **Message Content Intent** (it lets the bot read what you write in `#general`;
   no other privileged intent is needed). Invite it to your server with an OAuth2 URL of scopes `bot` and
   `applications.commands` and the permissions View Channels, Send Messages, Attach Files, Read Message History, Create Public Threads, Send Messages in Threads, Manage Threads, Manage Channels (the mirror makes the categories and forums) and Create Posts (the last three are for the questions of scheduled runs, see *Special tools and the daily note*).
3. A Clara token for it: `clara token create discord discord` (its approval scope is `safe`).
4. Two files that only the `clara` user can read, one with the bot's token, one with the Clara token:

   ```bash
   printf '%s' '<discord bot token>' | sudo install -m 600 -o clara -g clara /dev/stdin /etc/clara/discord-token
   printf '%s' '<the clara token>' | sudo install -m 600 -o clara -g clara /dev/stdin /etc/clara/discord-clara-token
   ```

5. Start it with the server (the usual way). In `/etc/clara/clara.env`, then restart Clara:

   ```
   CLARA_DISCORD_AUTOSTART=true
   CLARA_DISCORD_USER_ID=<your numeric Discord id>
   CLARA_DISCORD_CHANNEL_GENERAL=<id of #general>
   CLARA_DISCORD_CHANNEL_NOTIFICATION=<id of #notification>
   CLARA_DISCORD_CHANNEL_APPROVAL=<id of #approval>
   CLARA_DISCORD_CHANNEL_ALERTS=<id of #alerts>
   DISCORD_BOT_TOKEN_FILE=/etc/clara/discord-token
   CLARA_TOKEN_FILE=/etc/clara/discord-clara-token
   ```

   Clara then starts the bot with herself, starts it again if it crashes, and stops it when she stops (the server's
   log shows its lines). A wrong id, a refused token or a missing intent stops the retries and raises the `discord`
   alert; the log says which. Its silence for 5 minutes raises the same alert.

**Or as its own service**, with its own systemd credentials and hardening, instead of step 5 (never both: two bots
would answer twice). Put the ids in `/etc/clara/discord.env` (`CLARA_DISCORD_USER_ID`, the four
`CLARA_DISCORD_CHANNEL_*`, `CLARA_TIMEZONE=Europe/Paris`), then:

```bash
printf '%s' '<discord bot token>' | systemd-creds encrypt --name=discord-token - /etc/clara/discord-token.cred
printf '%s' '<the clara token>' | systemd-creds encrypt --name=clara-token - /etc/clara/discord-clara-token.cred
systemctl enable --now clara-discord
```

and `CLARA_DISCORD_EXPECTED=true` in `/etc/clara/clara.env`, so its silence raises an alert.

What goes where: your chat and Clara's answers in `#general` (a scheduled run's question too, with a thread for your answer); approval requests, with buttons, in `#approval`
(`#general` says when one waits); reminders, task reminders, schedule summaries, notifications and update news in
`#notification`; alerts in `#alerts`. Keep the server to yourself: anyone in it reads those channels.

**Choosing the channels and checking the bot:** the Admin page's **Discord** tab shows whether the bot is connected (its
last heartbeat, its name, whether the server started it), and for each of the four channels the name Discord gives it,
the rights the bot lacks there (a missing *Manage Threads* shows at once, not in a log) and what it receives now. *Send a
test message* posts a short message in that channel and tells whether Discord accepted it (the reason when it did not);
it needs the bot connected. Below, each of twelve kinds of message (reminders, tasks, scheduled tasks, hook results,
hook failures, hook notices, new mail, Clara's own notices, answer ready, other notifications, updates, alerts) has a
selector: `#notification`, `#general`, `#alerts`, or off on Discord. Nothing changes until you choose; a change applies to
the next message. A common use: send *Hook failures* to `#alerts`, or switch *New mail* off on Discord and keep it on the
web site. Approval requests and the questions of scheduled runs cannot be moved, and alerts cannot be switched off. The
setting is `discord_routes`, which the Settings tab also shows as JSON.


**Conversations in Discord (the mirror).** Once the bot is in your server, run the live check once, with the bot's token in
the environment (not in a file of the repository):

```bash
DISCORD_TOKEN=<the bot's token> DISCORD_GUILD_ID=<the server's id> uv run python scripts/discord_live_check.py
```

It makes a category, a forum, a thread and a message, edits, archives and unarchives the thread, then removes them, and
prints one line per step: a `FAIL` names the right the bot lacks. Forum channels need the server's **Community** features
switched on (Server settings). Discord's limits are 500 channels and 50 categories per server: when one is reached the
mirror stops, `#alerts` says so and the Admin page offers **Resume the mirror** once you have removed some.

### Special tools and the daily note

Special tools (**packs**) are switched on per conversation in the web chat's **"+" menu** (or `/tools` on Discord and in
`clara-chat`), and per schedule on the Automation page. The ones that read a day back need nothing more; **Second brain**
and **Daily notes** need the vault (above); **Discord thread** needs the bot connected (below).

When there is a vault, the first start makes the schedule **"Bilan du jour"**: every day at 23:59 (in `CLARA_TIMEZONE`)
Clara reads the day, asks you one question on the web site and in a Discord thread, waits for your answer, then writes the
day into the « Récap (Clara) » block of the daily note. You edit it on the Automation page (time, prompt, packs, model, what
to do when the server was off or when the last run is not over), pause it, or delete it (it is not made again).

1. The vault's `_clara/schema.json` says where daily notes go (`types.daily.folder` and `daily_format`, or `daily.folder`
   and `daily.format`); a missing note is made from `_templates/daily.md`.
2. In Discord, give the bot *Create Public Threads*, *Send Messages in Threads* and *Manage Threads* in `#general` (the bot
   logs what it lacks at start). The last one lets it delete the thread when the run ends; without it the thread is
   archived instead. Without the first two the run's conversation becomes the current one instead of a thread.
3. Try it without waiting for midnight: **Run now** on the Automation page. Answer in the thread (Discord) or in the run's
   conversation (web; **Answer** on the page). Clara shows the recap and writes it; open the daily note to see the block.
   **Close it** writes an unanswered one from what Clara read.

A run missed because the server was off is run once at the next start for its due time (`latest`). If the model is down the
run fails and is recorded as such; **Run now** tries again.

### Remote backups (restic)

Install restic; on another tailnet machine, a place for the repository (an SFTP account, or `rest-server`).

```bash
clara secrets set restic_password            # a long random password: keep a copy apart, like the key
# in /etc/clara/clara.env: CLARA_RESTIC_REPOSITORY=sftp:backup@other-machine.<tailnet>.ts.net:/srv/restic/clara
clara backup init-remote
clara backup now
```

## 4. Every day

- **Status:** `/status` in `clara-admin`, or the Admin page; `systemctl status clara`; `journalctl -u clara`.
- **Logs:** the Admin page's **Logs** tab streams the server's log and the traffic records live (last 500 lines kept in
  memory, filtered by source and level); `journalctl -u clara` keeps the full history.
- **Alerts** arrive in every client and in Discord's `#alerts`; `/alerts` lists them, `/alerts clear <key>` clears one. The
  list is in [FEATURES.md](../FEATURES.md#13-operations).
- **Approvals:** answer in the chat, on the Rules & approvals page, with `/approvals` and `/allow`… in a terminal, or
  with the Discord buttons in `#approval`.
- **Something odd in a conversation:** `/debug on` records full prompts and answers for one hour in
  `/var/lib/clara/logs/debug-content.jsonl` (deleted at the end).

## 5. Updates

1. `/update list` (or Admin → updates) shows the releases and their notes. Read the notes: migrations and new settings
   are listed there.
2. `/update v1.1.0`. Clara downloads, checks sums (and the signature when `CLARA_RELEASE_PUBKEY` is set), unpacks,
   builds the virtualenv, runs a preflight against a copy of the database, pulls the pinned sandbox image, takes a
   backup, and restarts into the new release. If it does not come up within 60 s, the launcher puts the old release
   and the old database back, and you get the `update` alert with the reason.
3. Old releases stay in `/opt/clara/releases/`, and so do the folders of updates that were refused after unpacking or rolled back
   (with `.staging/`); remove those you no longer want (never the one `current` points to).

### Signing releases (recommended)

```bash
uv run python scripts/release_signing.py new
```

Put the private key in the repository's Actions secret `CLARA_RELEASE_SIGNING_KEY`, the public key in the Actions
variable `CLARA_RELEASE_PUBKEY` (the workflow checks its own signature) and in `/etc/clara/clara.env` as
`CLARA_RELEASE_PUBKEY`. From then on Clara refuses an unsigned or wrongly signed release.

## 6. Backups and the restore drill

- Nightly at `CLARA_BACKUP_TIME` in `/var/lib/clara/backups/daily/` (14 kept), Sundays also in `weekly/` (8 kept);
  before each migration and update; `clara backup now` or Admin → backups for a manual one.
- **Monthly drill** (Clara reminds you): `clara restore --verify /var/lib/clara/backups/daily/<newest>.db` checks
  integrity and counts rows. Once in a while, restore the remote copy on another machine:
  `restic -r <repository> restore latest --target /tmp/drill`, then `clara restore --verify` on the file found there.

### Restoring for real

On the same machine (a broken database), or on a new machine after `deploy/install.sh`:

1. `systemctl stop clara`.
2. On a new machine, put **your saved secrets key** back:
   `printf '%s' '<key>' | systemd-creds encrypt --name=clara-secret-key - /etc/clara/secret-key.cred`.
3. `clara restore <backup.db>`: it verifies the file, keeps the current database as
   `backups/manual/before-restore.db`, and puts the backup in place.
4. The workspace: from the remote copy, `git clone /tmp/drill/.../workspace.bundle /var/lib/clara/workspace` (as
   `clara`).
5. `systemctl start clara`, then `clara doctor`.

## 7. Keys and tokens

- **A token leaked or a device lost:** `clara token revoke <name>` or Admin → tokens. Refused at once.
- **Rotating the secrets key:**

  ```bash
  clara secrets new-key > /root/new.key && chmod 600 /root/new.key
  systemctl stop clara
  clara secrets rotate --new-key-file /root/new.key
  systemd-creds encrypt --name=clara-secret-key /root/new.key /etc/clara/secret-key.cred
  shred -u /root/new.key && systemctl start clara
  ```

  Save the new key apart. Older backups still need the old key.

## 8. Maintenance

- `clara purge` (server stopped) vacuums the database after large deletions.
- `clara migrate` applies migrations by hand (the server does it at start, after a backup).
- Disk: the `disk` alert fires at 85%. The workspace is limited to 5 GB, the traffic log to 7 days.

## 9. Uninstall

```bash
systemctl disable --now clara clara-discord
sudo systemctl --user -M clara@ disable --now searxng 2>/dev/null
tailscale serve reset                       # if Clara's mapping is still there
rm -rf /opt/clara /etc/clara /etc/systemd/system/clara*.service
# the data, if you are sure: rm -rf /var/lib/clara /var/lib/clara-home /var/lib/clara-discord
userdel clara
```
