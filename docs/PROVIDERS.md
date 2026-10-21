# Model providers

Which providers Clara can use, what they receive, and what to check in their terms. **The terms change: every line
marked "to verify" was written on 14 October 2026 from general knowledge, not from a fresh reading of the terms.**
Read the provider's current terms before enabling it, and write the date you checked here.

What every provider receives for a conversation: the persona, your facts (none in incognito), the conversation or its
summary, project content, tool results, and attached files and pictures. See [DATA-MAP.md](DATA-MAP.md).

| Provider | Id in Clara | Where it runs | Key (secret) | Enabled at first |
| --- | --- | --- | --- | --- |
| Ollama on this machine | `local` (or the ids of `CLARA_OLLAMA_HOSTS`) | Your machine | — | Yes |
| Ollama on another of your machines | e.g. `pc` | That machine, over the tailnet | — | Yes |
| Ollama cloud | `cloud` | Ollama's servers | `ollama_api_key` | No |
| Google Gemini API | `gemini` | Google | `gemini_api_key` | No |
| DeepSeek | `deepseek` | DeepSeek | `deepseek_api_key` | No |
| Mistral | `mistral` | Mistral AI | `mistral_api_key` | No |
| OpenCode Go | `opencode-go` | OpenCode's gateway, then the model's maker | `opencode_go_api_key` | No |

## Local Ollama hosts

Nothing leaves your machines. Ollama has no authentication: on another machine, bind it to its tailnet address
(`OLLAMA_HOST=100.x.y.z:11434`) and allow only the Clara machine in a Tailscale ACL. The context window asked of local
models is `CLARA_OLLAMA_CONTEXT` (32,768): a bigger one needs more memory on that machine.

## Hosted providers: what to check

| Provider | To verify (14 October 2026) | Checked on |
| --- | --- | --- |
| Ollama cloud | Whether prompts are logged or kept, and for how long; where the servers are | — |
| Google Gemini API | The Gemini API terms distinguish unpaid and paid use: content sent under unpaid use may be used by Google to improve its products and seen by human reviewers; paid use is not. Which applies depends on the billing of the key's project | — |
| DeepSeek | Where data is stored and processed (DeepSeek is a company based in China) and whether prompts may be used for training | — |
| Mistral | Whether API inputs may be used for training on your plan, and how to opt out; data location (EU) | — |
| OpenCode Go | Terms differ **per model**, and the list of models changes. From OpenCode's Go page on 16 October 2026: most models are not used for training and keep nothing; some keep requests 30 days; Gemini 4 Argon Contributor and the Muse Spark Contributor models may use prompts for training. Re-read OpenCode's Go page before enabling | — |

## OpenCode Go

A subscription gateway to models from several makers. `opencode-go` lists Go's own models (`CLARA_OPENCODE_GO_HOST`,
read with your key) and asks them there. That includes the `-free` models that Go itself lists (`beam-free`,
`longcat-2.5-preview-free`). The free models that only OpenCode Zen lists are not offered: on 18 October 2026 OpenCode
answered every one of them with a `FreeTierError` (HTTP 403, "the free tier can only be used from within OpenCode"),
without a key and with a stored one, so they cannot be used from Clara.

OpenCode serves its models over four protocols and its lists do not say which; Clara speaks Chat Completions only.
**Claude, GPT, Grok, Gemini, MiniMax, Qwen and Muse Spark models fail when asked** (they are on Anthropic Messages,
Responses or Google's protocol). Clara therefore keeps a table of OpenCode's models (`src/clara/llm/opencode_models.py`,
written from [models.dev](https://models.dev) by `scripts/refresh_opencode_models.py`; run it when OpenCode adds
models): the models that do not work are never in the pickers, and the Models page leaves them out unless you pick
**All** or **Don't work**, where each gives its reason. The table also says which models read pictures: a picture is
refused for a text-only model such as `beam-free` before it is sent (OpenCode would answer HTTP 400
`invalid_value`, "supports text input only"). It comes from models.dev, not from a test of each model; a model it does not
have is offered and marked *unverified*. The "Contributor" models use your prompts to train future models.
If OpenCode answers a `FreeTierError` (HTTP 403) for a model, Clara reports it as it is, not as a refused key, and
raises no key alert for it. Clara identifies itself as `clara/<version>` and does not pass as OpenCode.

The gateway refuses a request without an `x-opencode-session` header, so Clara sends the conversation's id (a steady
random one for side jobs). Go's model list refuses a wrong key (HTTP 401, `Invalid credential`) when one is sent, as Clara
does, so **`/provider check` proves the key is accepted** (read on 18 October 2026; the same list answers with no key
at all). The window assumed for its models is `CLARA_OPENCODE_GO_CONTEXT` (131,072) as the lists do not say.

The models of `opencode-go` whose names end in `-free` are offered in the pickers without being chosen, as long as the
lists show them, and come last in them. `/models hide opencode-go:<model>` removes one for good (and `show` brings it back); other models of
the provider appear only once you `show` them.

## Google Drive (not a model provider)

A Google Cloud OAuth client whose consent screen is in **Testing** publishing status gets refresh tokens that expire
after 7 days: Drive would then ask to be reconnected every week. Publish the app (as an internal or external app with
only the `drive.file` scope), or accept reconnecting. To verify in Google's current documentation.

## Gmail and Outlook (not model providers)

Gmail's scopes (`gmail.readonly`, `gmail.compose`) are *restricted*: the same 7-day rule applies to a consent screen in
**Testing**, and an app *In production* without Google's verification shows an « unverified app » warning (fine for your
own account). Outlook's refresh tokens last 90 days after their last use and are renewed at each use; a client secret made
in Azure expires after 24 months at most. Both mail providers' terms are those of the accounts you connect. To verify in
Google's and Microsoft's current documentation.
