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
| OpenCode Go | Terms differ **per model**, and the list of models changes. From OpenCode's Go page on 16 October 2026: most models are not used for training and keep nothing; some keep requests 30 days; Gemini 4 Argon Contributor and the Muse Spark Contributor models may use prompts for training. The free models of Zen have their own terms (several are used to improve the model, some are trial-only and logged: send nothing private). Re-read OpenCode's Go and Zen pages before enabling | — |

## OpenCode Go

A subscription gateway to models from several makers, with the free models of OpenCode Zen added. `opencode-go` lists
the union of two lists: Go's own (`CLARA_OPENCODE_GO_HOST`, read with your key) and the models of Zen's list
(`CLARA_OPENCODE_ZEN_HOST`, read **without** a key) whose names end in `-free`. A model in both is asked at Go. A
model only in Zen's list is asked at Zen, with the same key; if Zen's list cannot be read, Go's models are listed
alone, and Zen's free models are not.

OpenCode serves its models over four protocols; Clara speaks Chat Completions only, and the list is not filtered by
protocol: **Claude, GPT, Grok, Gemini, MiniMax, Qwen and Muse Spark models are listed but fail when asked** (they are on
Anthropic Messages, Responses or Google's protocol). The "Contributor" models use your prompts to train future models.
The free models of Zen may be limited by OpenCode to use from OpenCode itself: it then answers a `FreeTierError`
(HTTP 403), which Clara shows as it is. Clara identifies itself as `clara/<version>` and does not pass as OpenCode.

The gateway refuses a request without an `x-opencode-session` header, so Clara sends the conversation's id (a steady
random one for side jobs). Go's model list is public: **`/provider check` proves the address answers, not that the key is
accepted**; a wrong key shows at the first answer. The window assumed for its models is `CLARA_OPENCODE_GO_CONTEXT`
(131,072) as the lists do not say.

The models of `opencode-go` whose names end in `-free` are offered in the pickers without being chosen, as long as the
lists show them, and come last in them. `/models hide opencode-go:<model>` removes one for good (and `show` brings it back); other models of
the provider appear only once you `show` them.

## Google Drive (not a model provider)

A Google Cloud OAuth client whose consent screen is in **Testing** publishing status gets refresh tokens that expire
after 7 days: Drive would then ask to be reconnected every week. Publish the app (as an internal or external app with
only the `drive.file` scope), or accept reconnecting. To verify in Google's current documentation.
