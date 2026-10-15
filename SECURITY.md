# Security

Clara is a personal agent: one person, one machine, reachable only over that person's tailnet. Its defences are
described in [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md), and what it stores in [docs/DATA-MAP.md](docs/DATA-MAP.md).

## Reporting a problem

Please report a vulnerability privately, through GitHub's **Report a vulnerability** button on this repository
(Security → Advisories), not in a public issue. Say what you found, how to reproduce it, and what it lets someone do.
You should get an answer within a week. There is no bug bounty.

In scope: anything that lets someone other than the owner reach Clara or its data, lets content Clara reads (a web
page, an issue, a file) make her act outside her rules, leaks a secret or a token, escapes the script sandbox, or
lets a release or an update run code that was not built by this repository's workflow.

Out of scope: attacks that need the owner's own tokens, devices, machine or tailnet admin; denial of service from
inside the tailnet; what a model provider does with prompts it is sent (see [docs/PROVIDERS.md](docs/PROVIDERS.md)).

## Supported versions

Only the latest release is supported. Updates are installed by choice (`/update`), never automatically.

## If you are the owner and something leaked

| What leaked | Do |
| --- | --- |
| A client token | `clara token revoke <name>` (or the Admin page): refused at once, no restart. Make a new one. |
| The secrets key | `clara secrets rotate --new-key-file <file>` with a new key, then update the systemd credential (docs/OPERATIONS.md). Then change every secret the store held (provider keys, GitHub token, Google client secret; disconnect and reconnect Drive). |
| A provider key, the GitHub token | Revoke it at the provider or on GitHub, then `clara secrets set <name>` or the Connections page. |
| Google access | Disconnect Drive on the Connections page (the token is revoked at Google), and check https://myaccount.google.com/permissions. |
| A backup | Backups hold the database: facts, conversations, encrypted secrets (useless without the key, which is never in a backup). Rotate the key if you think both leaked. |
| The Discord account | Revoke the `discord` token; destructive actions could not be approved from Discord anyway (its scope is `safe`). |
