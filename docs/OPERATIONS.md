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
4. The sandbox image: `sudo -u clara podman pull <the image the release names in manifest.json>` (or, for a source
   install, `sudo -u clara podman build -t localhost/clara-sandbox:latest sandbox/`). Then `clara doctor` until
   nothing says FAIL.
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

### Web search (SearXNG)

Follow the comments at the top of `deploy/searxng/searxng.container` (a rootless quadlet of the `clara` user,
listening on 127.0.0.1:8888, with `deploy/searxng/settings.yml` and a random `secret_key`). `clara doctor` checks it.

### GitHub

Create a **fine-grained personal access token** on GitHub limited to the repositories Clara should reach, with the
permissions she needs (Contents, Issues, Pull requests: read and write; Metadata: read). Paste it on the Connections
page, then add each repository there: Clara reaches no other, and every change asks first by default.

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

1. On the Discord developer portal: a new application and its bot. No privileged intent is needed (direct messages
   carry their content without it). Invite nothing to any server: open a DM with the bot from your account (share a
   server with it once, or use its install link for user DMs).
2. A Clara token for it: `clara token create discord discord` (its approval scope is `safe`).
3. Credentials and settings:

   ```bash
   printf '%s' '<discord bot token>' | systemd-creds encrypt --name=discord-token - /etc/clara/discord-token.cred
   printf '%s' '<the clara token>' | systemd-creds encrypt --name=clara-token - /etc/clara/discord-clara-token.cred
   echo 'CLARA_DISCORD_USER_ID=<your numeric Discord id>' > /etc/clara/discord.env
   echo 'CLARA_TIMEZONE=Europe/Paris' >> /etc/clara/discord.env
   systemctl enable --now clara-discord
   ```

4. In `/etc/clara/clara.env`: `CLARA_DISCORD_EXPECTED=true`, so its silence raises an alert.

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
- **Alerts** arrive in every client and as Discord DMs; `/alerts` lists them, `/alerts clear <key>` clears one. The
  list is in [FEATURES.md](../FEATURES.md#13-operations).
- **Approvals:** answer in the chat, on the Rules & approvals page, with `/approvals` and `/allow`… in a terminal, or
  with the Discord buttons.
- **Something odd in a conversation:** `/debug on` records full prompts and answers for one hour in
  `/var/lib/clara/logs/debug-content.jsonl` (deleted at the end).

## 5. Updates

1. `/update list` (or Admin → updates) shows the releases and their notes. Read the notes: migrations and new settings
   are listed there.
2. `/update v1.1.0`. Clara downloads, checks sums (and the signature when `CLARA_RELEASE_PUBKEY` is set), unpacks,
   builds the virtualenv, runs a preflight against a copy of the database, pulls the pinned sandbox image, takes a
   backup, and restarts into the new release. If it does not come up within 60 s, the launcher puts the old release
   and the old database back, and you get the `update` alert with the reason.
3. Old releases stay in `/opt/clara/releases/`; remove those you no longer want (never the one `current` points to).

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
