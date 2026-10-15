<script lang="ts">
  import ApprovalCard from '../components/ApprovalCard.svelte';
  import ConversationRail from '../components/ConversationRail.svelte';
  import FileCard from '../components/FileCard.svelte';
  import QcmForm from '../components/QcmForm.svelte';
  import { api, fileToBase64, stream, zone } from '../lib/api';
  import { markdown } from '../lib/markdown';
  import { go, route, toast } from '../lib/state.svelte';

  type Tool = { name: string; arguments: any; result?: string; running?: boolean };
  type Message = {
    id?: number;
    role: 'user' | 'assistant';
    content: string;
    thinking?: string;
    tools: Tool[];
    approvals: any[];
    forms: any[];
    files: any[];
    notes: string[];
    versions?: number[];
    version?: number;
    streaming?: boolean;
    error?: string;
  };

  const blank = (role: Message['role'], content = ''): Message => ({ role, content, tools: [], approvals: [], forms: [], files: [], notes: [] });

  let conversationId = $derived(route.params.id ?? null);
  let conversation = $state<any>(null);
  let messages = $state<Message[]>([]);
  let draft = $state('');
  let attached = $state<File[]>([]);
  let busy = $state(false);
  let controller: AbortController | null = null;
  let models = $state<any>({ models: [], default: '' });
  let fresh = $state({ incognito: false, local_only: false, model: '' });
  let editing = $state<number | null>(null);
  let editText = $state('');
  let railRefresh = $state(0);
  let skipReload = '';
  let scroller: HTMLElement | undefined = $state();

  $effect(() => {
    const id = conversationId;
    if (id && id === skipReload) return;
    load(id);
  });

  async function load(id: string | null): Promise<void> {
    conversation = null;
    messages = [];
    models = await api.get('/v1/models').catch(() => ({ models: [], default: '' }));
    if (!id) return;
    try {
      conversation = await api.get(`/v1/conversations/${id}`);
      const history = (await api.get(`/v1/conversations/${id}/messages`, { calls: true })).messages;
      messages = group(history, conversation.approvals ?? []);
      scrollDown();
    } catch (error) {
      toast((error as Error).message, 'error');
      go('/chat');
    }
  }

  function group(history: any[], waiting: any[]): Message[] {
    const shown: Message[] = [];
    let answer: Message | null = null;
    for (const item of history) {
      if (item.role === 'user') {
        const message = blank('user', item.content);
        Object.assign(message, { id: item.id, versions: item.versions, version: item.version });
        shown.push(message);
        answer = null;
        continue;
      }
      if (!answer) {
        answer = blank('assistant');
        shown.push(answer);
      }
      if (item.role === 'assistant') {
        if (item.content) answer.content += (answer.content ? '\n\n' : '') + item.content;
        for (const call of item.tool_calls ?? []) answer.tools.push({ name: call.name, arguments: call.arguments });
        if (item.forms) answer.forms.push(...item.forms);
      } else if (item.role === 'tool') {
        const open = answer.tools.find((tool) => tool.result === undefined && tool.name === item.tool_name);
        if (open) open.result = item.content;
      }
    }
    if (answer && waiting.length) answer.approvals.push(...waiting);
    return shown;
  }

  function scrollDown(): void {
    queueMicrotask(() => scroller?.scrollTo({ top: scroller.scrollHeight }));
  }

  async function send(text = draft, forkOf: number | null = null): Promise<void> {
    if (busy || (!text.trim() && !attached.length)) return;
    busy = true;
    controller = new AbortController();
    const files = await Promise.all(attached.map(async (file) => ({ name: file.name, data: await fileToBase64(file) })));
    let id = conversationId;
    try {
      if (!id) {
        const made = await api.post('/v1/conversations', { incognito: fresh.incognito, local_only: fresh.local_only, model: fresh.model });
        id = made.id as string;
        skipReload = id;
        go('/chat/' + id);
        conversation = made;
      }
      if (forkOf !== null) {
        const index = messages.findIndex((message) => message.id === forkOf);
        if (index >= 0) messages = messages.slice(0, index);
      }
      const user = blank('user', text);
      if (files.length) user.notes.push('Attached: ' + files.map((file) => file.name).join(', '));
      const answer = blank('assistant');
      answer.streaming = true;
      messages.push(user, answer);
      const current = messages[messages.length - 1];
      draft = '';
      attached = [];
      scrollDown();
      const body = { message: text, conversation: id, attachments: files, timezone: zone, fork_of: forkOf ?? undefined };
      for await (const event of stream('POST', '/v1/chat/stream', body, controller.signal)) {
        switch (event.type) {
          case 'thinking':
            current.thinking = (current.thinking ?? '') + event.text;
            break;
          case 'token':
            current.content += event.text;
            break;
          case 'tool_start':
            current.tools.push({ name: event.name, arguments: event.arguments, running: true });
            break;
          case 'tool': {
            const tool = current.tools.find((item) => item.running && item.name === event.name);
            if (tool) Object.assign(tool, { running: false, result: event.result });
            else current.tools.push({ name: event.name, arguments: event.arguments, result: event.result });
            break;
          }
          case 'approval':
            current.approvals.push(event.approval);
            break;
          case 'qcm':
            current.forms.push(event.form);
            break;
          case 'file':
            current.files.push(event);
            break;
          case 'retrying':
          case 'warning':
            current.notes.push(event.message);
            break;
          case 'compacted':
            current.notes.push(`Summarised: ${Math.round(event.before)}% → ${Math.round(event.after)}% of the context.`);
            break;
          case 'discarded':
            current.notes.push(`The replaced answer had run: ${event.tools.join(', ')} (what they changed stays changed).`);
            break;
          case 'done':
            user.id = event.message_id ?? undefined;
            user.versions = event.versions?.length > 1 ? event.versions : undefined;
            user.version = user.versions ? user.versions.indexOf(event.message_id) + 1 : undefined;
            if (conversation) conversation.context = event.context;
            break;
          case 'error':
            current.error = event.message;
            break;
        }
        scrollDown();
      }
      current.streaming = false;
      messages = [...messages];
    } catch (error) {
      if ((error as Error).name !== 'AbortError') toast((error as Error).message, 'error');
    } finally {
      busy = false;
      controller = null;
      railRefresh++;
      if (id) conversation = await api.get(`/v1/conversations/${id}`).catch(() => conversation);
    }
  }

  function stop(): void {
    controller?.abort();
  }

  async function switchVersion(message: Message, step: number): Promise<void> {
    if (!message.versions || !message.version) return;
    const target = message.versions[message.version - 1 + step];
    if (target === undefined) return;
    await api.post(`/v1/conversations/${conversationId}/version`, { message_id: target });
    load(conversationId);
  }

  async function summarise(): Promise<void> {
    try {
      const done = await api.post(`/v1/conversations/${conversationId}/compact`, {});
      toast(`Summarised: ${Math.round(done.before)}% → ${Math.round(done.after)}%`, 'ok');
      load(conversationId);
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function setModel(model: string): Promise<void> {
    if (!conversationId) {
      fresh.model = model;
      return;
    }
    conversation = await api.patch(`/v1/conversations/${conversationId}`, { model });
  }

  function pick(event: Event): void {
    const input = event.target as HTMLInputElement;
    addFiles([...(input.files ?? [])]);
    input.value = '';
  }

  // The server's limits (FEATURES.md, section 16), checked here so a refusal says why before anything is sent
  const MAX_FILES = 4;
  const MAX_TOTAL = 34_000_000;
  const MB = 1_000_000;

  function tooBig(file: File): string {
    const animated = file.type === 'image/gif' || file.type === 'image/webp';
    const limit = file.type.startsWith('image/') ? (animated ? 30 * MB : 8 * MB) : 30 * MB;
    return file.size > limit ? `${file.name} is too big: ${limit / MB} MB at most.` : '';
  }

  function addFiles(files: File[]): void {
    const kept: File[] = [];
    for (const file of files) {
      const problem = tooBig(file);
      if (problem) toast(problem, 'error');
      else kept.push(file);
    }
    const next = [...attached, ...kept];
    if (next.length > MAX_FILES) toast(`${MAX_FILES} files at most per message.`, 'error');
    const chosen = next.slice(0, MAX_FILES);
    if (chosen.reduce((sum, file) => sum + file.size, 0) > MAX_TOTAL) {
      toast(`The files of one message may weigh ${MAX_TOTAL / MB} MB in all: send them in several messages.`, 'error');
      return;
    }
    attached = chosen;
  }

  function keydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send();
    }
  }

  function drop(event: DragEvent): void {
    event.preventDefault();
    addFiles([...(event.dataTransfer?.files ?? [])]);
  }

  function paste(event: ClipboardEvent): void {
    const files = [...(event.clipboardData?.files ?? [])];
    if (files.length) addFiles(files);
  }

  const percent = $derived(conversation?.context?.percent ?? 0);
</script>

<div class="chat">
  <ConversationRail current={conversationId} refresh={railRefresh} />
  <section class="main" ondragover={(event) => event.preventDefault()} ondrop={drop} aria-label="Conversation">
    <header class="row">
      <strong class="grow">{conversation?.title || (conversationId ? 'Conversation' : 'New conversation')}</strong>
      {#if conversation?.incognito}<span class="tag">🕶 incognito</span>{/if}
      {#if conversation?.local_only}<span class="tag">🏠 local only</span>{/if}
      {#if !conversationId}
        <label class="small row"><input type="checkbox" bind:checked={fresh.incognito} /> incognito</label>
        <label class="small row"><input type="checkbox" bind:checked={fresh.local_only} /> local only</label>
      {/if}
      <select value={conversationId ? conversation?.model ?? '' : fresh.model} onchange={(event) => setModel((event.target as HTMLSelectElement).value)}>
        <option value="">Default ({models.default || 'none'})</option>
        {#each models.models as model (model.ref)}<option value={model.ref}>{model.ref}</option>{/each}
      </select>
      {#if conversation?.context}
        <span class="meter" title={`${conversation.context.tokens} of ${conversation.context.window} tokens`}>
          <span class="fill" style:width={`${Math.min(100, percent)}%`}></span>
        </span>
        <span class="small muted">{percent}%</span>
        <button class="small" onclick={summarise} disabled={busy}>Summarise</button>
      {/if}
    </header>
    <div class="messages" bind:this={scroller}>
      {#if !messages.length}
        <div class="empty">Ask Clara anything. Notes she writes go to her workspace (notes/) and show here as cards.</div>
      {/if}
      {#each messages as message, index (index)}
        <article class={message.role}>
          {#if message.role === 'user'}
            {#if editing === message.id}
              <textarea rows="3" bind:value={editText}></textarea>
              <div class="row">
                <button class="primary small" onclick={() => { const id = editing; editing = null; send(editText, id); }}>Send edited</button>
                <button class="small" onclick={() => (editing = null)}>Cancel</button>
              </div>
            {:else}
              <div class="bubble markdown" use:markdown={message.content}></div>
              {#each message.notes as note}<div class="muted small">{note}</div>{/each}
              <div class="row tools-line small">
                {#if message.versions}
                  <button class="small" onclick={() => switchVersion(message, -1)} disabled={message.version === 1}>‹</button>
                  <span>{message.version}/{message.versions.length}</span>
                  <button class="small" onclick={() => switchVersion(message, 1)} disabled={message.version === message.versions.length}>›</button>
                {/if}
                {#if message.id && !busy}
                  <button class="small" onclick={() => send(message.content, message.id!)}>Ask again</button>
                  <button class="small" onclick={() => { editing = message.id!; editText = message.content; }}>Edit</button>
                {/if}
              </div>
            {/if}
          {:else}
            {#if message.thinking}
              <details class="thinking"><summary class="muted small">Thinking</summary><pre>{message.thinking}</pre></details>
            {/if}
            {#each message.tools as tool}
              <details class="tool">
                <summary class="small">{tool.running ? '⏳' : '🔧'} {tool.name}</summary>
                <pre>{JSON.stringify(tool.arguments, null, 2)}</pre>
                {#if tool.result !== undefined}<pre class="result">{tool.result}</pre>{/if}
              </details>
            {/each}
            {#each message.approvals as approval (approval.id)}<ApprovalCard {approval} />{/each}
            {#if message.content}<div class="markdown" use:markdown={message.content}></div>{/if}
            {#if message.streaming && !message.content}<div class="muted">…</div>{/if}
            {#each message.forms as form}<QcmForm {form} onAnswer={(text) => send(text)} />{/each}
            {#each message.files as card}<FileCard {card} />{/each}
            {#each message.notes as note}<div class="muted small">{note}</div>{/each}
            {#if message.error}<div class="bad">⚠ {message.error}</div>{/if}
          {/if}
        </article>
      {/each}
    </div>
    <footer>
      {#if attached.length}
        <div class="row small">
          {#each attached as file, index}
            <span class="tag">{file.name} <button class="small" onclick={() => attached.splice(index, 1)}>✕</button></span>
          {/each}
        </div>
      {/if}
      <div class="row composer">
        <label class="attach" title="Attach files (PDF, Word, text, pictures)">📎<input type="file" multiple onchange={pick} hidden /></label>
        <textarea class="grow" rows="2" placeholder="Message Clara (Enter to send, Shift+Enter for a new line)"
          bind:value={draft} onkeydown={keydown} onpaste={paste}></textarea>
        {#if busy}<button onclick={stop}>Stop</button>{:else}<button class="primary" onclick={() => send()}>Send</button>{/if}
      </div>
    </footer>
  </section>
</div>

<style>
  .chat {
    display: grid;
    grid-template-columns: 280px 1fr;
    height: 100vh;
  }
  .main {
    display: flex;
    flex-direction: column;
    min-width: 0;
    height: 100vh;
  }
  header {
    padding: 0.6rem 1rem;
    border-bottom: 1px solid var(--line);
    background: var(--panel);
  }
  .meter {
    width: 80px;
    height: 6px;
    background: var(--panel-2);
    border-radius: 3px;
    overflow: hidden;
    display: inline-block;
  }
  .fill {
    display: block;
    height: 100%;
    background: var(--accent);
  }
  .messages {
    flex: 1;
    overflow: auto;
    padding: 1rem;
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }
  article {
    max-width: 820px;
    width: 100%;
    margin: 0 auto;
  }
  article.user .bubble {
    background: var(--accent-soft);
    border-radius: 12px;
    padding: 0.6rem 0.9rem;
    margin-left: auto;
    max-width: 85%;
    width: fit-content;
  }
  article.user .tools-line {
    justify-content: flex-end;
    opacity: 0.6;
  }
  article.user:hover .tools-line {
    opacity: 1;
  }
  article.user textarea {
    width: 100%;
  }
  .tool,
  .thinking {
    border-left: 2px solid var(--line);
    padding-left: 0.6rem;
    margin: 0.2rem 0;
  }
  .tool pre {
    max-height: 300px;
    margin: 0.3rem 0;
  }
  .result {
    white-space: pre-wrap;
  }
  footer {
    border-top: 1px solid var(--line);
    padding: 0.6rem 1rem;
    background: var(--panel);
  }
  .composer {
    align-items: flex-end;
    flex-wrap: nowrap;
  }
  .attach {
    cursor: pointer;
    font-size: 1.2rem;
  }
  @media (max-width: 900px) {
    .chat {
      grid-template-columns: 1fr;
    }
    .chat :global(aside) {
      display: none;
    }
  }
</style>
