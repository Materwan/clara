<script lang="ts">
  import ApprovalCard from '../components/ApprovalCard.svelte';
  import Checkbox from '../components/Checkbox.svelte';
  import FileCard from '../components/FileCard.svelte';
  import Icon from '../components/Icon.svelte';
  import Mark from '../components/Mark.svelte';
  import PlusMenu from '../components/PlusMenu.svelte';
  import QcmForm from '../components/QcmForm.svelte';
  import { api, fileToBase64, stream, zone } from '../lib/api';
  import { markdown } from '../lib/markdown';
  import { conversations, go, route, toast, ui } from '../lib/state.svelte';
  import { type Call, note, summary } from '../lib/tools';

  type Shown = Call & { open?: boolean };
  // Clara's reply in the order she made it: text, runs of tool calls (calls with no text between them), requests, forms
  type Part =
    | { kind: 'text'; text: string }
    | { kind: 'run'; calls: Shown[]; open: boolean }
    | { kind: 'approval'; approval: any }
    | { kind: 'form'; form: any };
  type Message = {
    id?: number;
    role: 'user' | 'assistant';
    content: string; // what you wrote (a reply's text is in its parts)
    attachments: string[];
    parts: Part[];
    thinking?: string;
    thinkingOpen?: boolean;
    files: any[];
    notes: string[];
    versions?: number[];
    version?: number;
    streaming?: boolean;
    error?: string;
  };

  const blank = (role: Message['role'], content = ''): Message => ({ role, content, attachments: [], parts: [], files: [], notes: [] });
  const SILENT = new Set(['qcm']); // the form is its own card

  let conversationId = $derived(route.params.id ?? null);
  let conversation = $state<any>(null);
  let messages = $state<Message[]>([]);
  let draft = $state('');
  let attached = $state<File[]>([]);
  let busy = $state(false);
  let dragging = $state(false);
  let controller: AbortController | null = null;
  let models = $state<any>({ models: [], default: '' });
  let fresh = $state({ incognito: false, local_only: false, model: '', packs: ['web'] as string[] });
  /** The special tools you can switch on in a chat (the "+" menu), and the projects to choose from. */
  let packList = $state<any[]>([]);
  let projectList = $state<any[]>([]);
  /** A project chosen in the menu for a conversation not started yet (undefined: the one it was started from). */
  let chosenProject = $state<number | null | undefined>(undefined);
  /** The project a conversation belongs to: its own, or for a new one the project it was started from (#/chat?project=). */
  let projectId = $derived(conversationId ? (conversation?.project ?? null)
    : (chosenProject !== undefined ? chosenProject : Number(route.query.get('project')) || null));
  let activePacks = $derived<string[]>(conversationId ? (conversation?.packs ?? ['web']) : fresh.packs);
  let projectName = $state('');
  let editing = $state<number | null>(null);
  let editText = $state('');
  let skipReload = '';
  let scroller: HTMLElement | undefined = $state();
  let picker: HTMLInputElement | undefined = $state();

  $effect(() => {
    const id = conversationId;
    if (id && id === skipReload) return;
    load(id);
  });

  $effect(() => {
    const id = projectId;
    projectName = '';
    if (id) api.get(`/v1/projects/${id}`).then((project) => { if (projectId === id) projectName = project.name; }).catch(() => {});
  });

  async function load(id: string | null): Promise<void> {
    conversation = null;
    messages = [];
    models = await api.get('/v1/models').catch(() => ({ models: [], default: '' }));
    packList = (await api.get('/v1/packs', { where: 'chat' }).catch(() => ({ packs: [] }))).packs;
    projectList = (await api.get('/v1/projects').catch(() => ({ projects: [] }))).projects;
    if (!id) chosenProject = undefined;
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

  function addText(message: Message, text: string): void {
    const last = message.parts.at(-1);
    if (last?.kind === 'text') last.text += text;
    else message.parts.push({ kind: 'text', text });
  }

  function addCall(message: Message, call: Shown): void {
    if (SILENT.has(call.name)) return;
    const last = message.parts.at(-1);
    if (last?.kind === 'run') last.calls.push(call);
    else message.parts.push({ kind: 'run', calls: [call], open: false });
  }

  /** The first call of that name still waiting for its result (several calls of a round may run at once). */
  function openCall(message: Message, name: string, running: boolean): Shown | undefined {
    for (const part of message.parts) {
      if (part.kind !== 'run') continue;
      const call = part.calls.find((item) => item.name === name && (running ? item.running : item.result === undefined));
      if (call) return call;
    }
    return undefined;
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
        if (item.thinking) answer.thinking = (answer.thinking ? `${answer.thinking}\n\n` : '') + item.thinking;
        if (item.content) addText(answer, (answer.parts.at(-1)?.kind === 'text' ? '\n\n' : '') + item.content);
        for (const call of item.tool_calls ?? []) addCall(answer, { name: call.name, arguments: call.arguments });
        for (const form of item.forms ?? []) answer.parts.push({ kind: 'form', form });
      } else if (item.role === 'tool') {
        const open = openCall(answer, item.tool_name, false);
        if (open) open.result = item.content;
      }
    }
    if (answer) for (const approval of waiting) answer.parts.push({ kind: 'approval', approval });
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
        const made = await api.post('/v1/conversations', { incognito: fresh.incognito, local_only: fresh.local_only, model: fresh.model, project: projectId ?? undefined, packs: fresh.packs });
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
      user.attachments = files.map((file) => file.name);
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
            addText(current, event.text);
            break;
          case 'tool_start':
            addCall(current, { name: event.name, arguments: event.arguments, running: true });
            break;
          case 'tool': {
            const call = openCall(current, event.name, true);
            if (call) Object.assign(call, { running: false, result: event.result });
            else addCall(current, { name: event.name, arguments: event.arguments, result: event.result });
            break;
          }
          case 'approval':
            current.parts.push({ kind: 'approval', approval: event.approval });
            break;
          case 'qcm':
            current.parts.push({ kind: 'form', form: event.form });
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
      const last = messages.at(-1);
      if (last) last.streaming = false;
      busy = false;
      controller = null;
      conversations.changed++;
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

  /** Switch a special tool on or off: saved with the conversation, or kept for the one about to start. */
  async function togglePack(id: string): Promise<void> {
    const next = activePacks.includes(id) ? activePacks.filter((item) => item !== id) : [...activePacks, id];
    if (!conversationId) {
      fresh.packs = next;
      return;
    }
    try {
      conversation = { ...conversation, ...(await api.patch(`/v1/conversations/${conversationId}`, { packs: next })) };
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function chooseProject(id: number | null): Promise<void> {
    if (!conversationId) {
      chosenProject = id;
      return;
    }
    try {
      conversation = { ...conversation, ...(await api.patch(`/v1/conversations/${conversationId}`, { project: id ?? 0 })) };
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  const packLabel = (id: string): string => packList.find((pack) => pack.id === id)?.label ?? id;

  function keydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send();
    }
  }

  function drop(event: DragEvent): void {
    event.preventDefault();
    dragging = false;
    addFiles([...(event.dataTransfer?.files ?? [])]);
  }

  function paste(event: ClipboardEvent): void {
    const files = [...(event.clipboardData?.files ?? [])];
    if (files.length) addFiles(files);
  }

  /** The box you write in grows with what you write, up to its CSS max-height. */
  function autosize(node: HTMLTextAreaElement, _value: string) {
    const fit = () => {
      node.style.height = 'auto';
      node.style.height = `${node.scrollHeight}px`;
    };
    fit();
    return { update: () => queueMicrotask(fit) };
  }

  function greeting(): string {
    const hour = new Date().getHours();
    return hour < 5 ? 'Hello' : hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  }

  const percent = $derived(conversation?.context?.percent ?? 0);
</script>

{#snippet callDetail(call: Shown)}
  <div class="call-detail">
    <div><div class="call-key">Given</div><pre>{JSON.stringify(call.arguments, null, 2)}</pre></div>
    <div><div class="call-key">Returned</div>
      {#if call.result !== undefined}<pre>{call.result}</pre>{:else}<p class="small muted">{call.running ? 'Still running…' : 'Not kept.'}</p>{/if}</div>
  </div>
{/snippet}

{#snippet callLine(call: Shown)}
  {@const said = note(call)}
  <span class="run-summary"><b>{said.label}</b>{said.detail ? ` ${said.detail}` : ''}</span>
  {#if call.running}<span class="spin" aria-hidden="true"></span>{/if}
{/snippet}

<section class="chat" aria-label="Conversation">
  <header class="page-head chat-head">
    <button class="ghost icon-btn open-rail" aria-label="Open navigation" onclick={() => (ui.rail = true)}><Icon name="menu" /></button>
    <h1 class="title">{conversation?.title || (conversationId ? 'Conversation' : 'New conversation')}</h1>
    {#if projectName}<a class="flag" href={'#/projects/' + projectId} title="Open the project"><Icon name="folder" size={16} />{projectName}</a>{/if}
    {#if conversation?.incognito}<span class="flag"><Icon name="incognito" size={16} />Incognito</span>{/if}
    {#if conversation?.local_only}<span class="flag"><Icon name="home" size={16} />Local only</span>{/if}
    {#if !conversationId}
      <label class="flag" title="No facts read or written; deleted 24 hours after its last message"><Checkbox bind:checked={fresh.incognito} />Incognito</label>
      <label class="flag" title="Local models only; nothing leaves your machines"><Checkbox bind:checked={fresh.local_only} />Local only</label>
    {/if}
    {#if conversation?.context}
      <span class="context" class:hot={percent >= 80} title={`${conversation.context.tokens.toLocaleString()} of ${conversation.context.window.toLocaleString()} tokens`}>
        <svg class="ring" viewBox="0 0 36 36" width="22" height="22" aria-hidden="true">
          <circle class="track" cx="18" cy="18" r="15" pathLength="100" />
          <circle class="fill" cx="18" cy="18" r="15" pathLength="100" stroke-dasharray={`${Math.max(0, Math.min(100, percent))} 100`} />
        </svg>
        <span>{percent}%<span class="label">{' of the context'}</span></span>
      </span>
      <button class="ghost icon-btn" title="Summarise the conversation to free the context" aria-label="Summarise" onclick={summarise} disabled={busy}>
        <Icon name="compress" size={18} />
      </button>
    {/if}
  </header>

  <div class="messages" bind:this={scroller}>
    <div class="messages-inner">
      {#if !messages.length}
        <div class="welcome">
          <Mark size={56} />
          <h2 class="greeting">{greeting()}</h2>
          <p>Ask Clara anything. The notes she writes go to her workspace (notes/) and show here as cards.</p>
        </div>
      {/if}
      {#each messages as message, index (index)}
        {#if message.role === 'user'}
          <div class="msg user">
            <div class="body">
              {#if editing === message.id}
                <div class="edit">
                  <textarea rows="3" bind:value={editText} aria-label="Your question"></textarea>
                  <div class="row">
                    <button class="small" onclick={() => (editing = null)}>Cancel</button>
                    <button class="primary small" onclick={() => { const id = editing; editing = null; send(editText, id); }}>Send edited</button>
                  </div>
                </div>
              {:else}
                <div class="bubble markdown" use:markdown={message.content}></div>
                {#if message.attachments.length}
                  <div class="chips notes">{#each message.attachments as name}<span class="chip"><Icon name="clip" size={14} /><span class="name">{name}</span></span>{/each}</div>
                {/if}
                {#if message.versions || (message.id && !busy)}
                  <div class="msg-actions">
                    {#if message.versions}
                      <button class="ghost" aria-label="Previous version" onclick={() => switchVersion(message, -1)} disabled={message.version === 1}><Icon name="left" size={16} /></button>
                      <span>{message.version}/{message.versions.length}</span>
                      <button class="ghost" aria-label="Next version" onclick={() => switchVersion(message, 1)} disabled={message.version === message.versions.length}><Icon name="chevron" size={16} /></button>
                    {/if}
                    {#if message.id && !busy}
                      <button class="ghost" title="Ask again" aria-label="Ask again" onclick={() => send(message.content, message.id!)}><Icon name="refresh" size={16} /></button>
                      <button class="ghost" title="Edit" aria-label="Edit" onclick={() => { editing = message.id!; editText = message.content; }}><Icon name="edit" size={16} /></button>
                    {/if}
                  </div>
                {/if}
              {/if}
            </div>
          </div>
        {:else}
          <div class="msg assistant" class:live={message.streaming}>
            <div class="who"><Mark size={30} /></div>
            <div class="body">
              <div class="parts">
                {#if message.thinking}
                  <div class="run" class:open={message.thinkingOpen}>
                    <button class="run-head" aria-expanded={!!message.thinkingOpen} onclick={() => (message.thinkingOpen = !message.thinkingOpen)}>
                      <Icon name="chevron" size={14} /><span class="run-summary">{message.streaming && !message.parts.length ? 'Thinking…' : 'Thought it through'}</span>
                    </button>
                    {#if message.thinkingOpen}<div class="thinking-text">{message.thinking}</div>{/if}
                  </div>
                {/if}
                {#each message.parts as part, p (p)}
                  {#if part.kind === 'text'}
                    <div class="markdown" use:markdown={part.text}></div>
                  {:else if part.kind === 'run'}
                    <div class="run" class:open={part.open}>
                      <button class="run-head" aria-expanded={part.open} onclick={() => (part.open = !part.open)}>
                        <Icon name="chevron" size={14} />
                        {#if part.calls.length === 1}
                          {@render callLine(part.calls[0])}
                        {:else}
                          <span class="run-summary">{summary(part.calls)}</span>
                          {#if part.calls.some((call) => call.running)}<span class="spin" aria-hidden="true"></span>{/if}
                        {/if}
                      </button>
                      {#if part.open}
                        {#if part.calls.length === 1}
                          {@render callDetail(part.calls[0])}
                        {:else}
                          <div class="run-list">
                            {#each part.calls as call, c (c)}
                              <div>
                                <button class="run-head" aria-expanded={!!call.open} onclick={() => (call.open = !call.open)}>
                                  <Icon name={note(call).icon} size={14} />{@render callLine(call)}
                                </button>
                                {#if call.open}{@render callDetail(call)}{/if}
                              </div>
                            {/each}
                          </div>
                        {/if}
                      {/if}
                    </div>
                  {:else if part.kind === 'approval'}
                    <ApprovalCard approval={part.approval} />
                  {:else}
                    <QcmForm form={part.form} onAnswer={(text) => send(text)} />
                  {/if}
                {/each}
                {#if message.streaming && !message.parts.length && !message.thinking}<span class="waiting" role="status" aria-label="Clara is answering"></span>{/if}
                {#each message.files as card}<FileCard {card} />{/each}
              </div>
              {#if message.notes.length}
                <div class="reply-notes">{#each message.notes as line}<span>{line}</span>{/each}</div>
              {/if}
              {#if message.error}<div class="failed-note"><Icon name="warn" size={18} /><span>{message.error}</span></div>{/if}
            </div>
          </div>
        {/if}
      {/each}
    </div>
  </div>

  <div class="composer" class:drop={dragging} role="group" aria-label="Write to Clara"
    ondragover={(event) => { event.preventDefault(); dragging = true; }} ondragleave={() => (dragging = false)} ondrop={drop}>
    <div class="composer-frame">
      <div class="composer-inner">
        {#if activePacks.some((id) => id !== 'web') || !activePacks.includes('web')}
          <div class="chips" aria-label="Tools of this conversation">
            {#each activePacks.filter((id) => id !== 'web') as id (id)}
              <span class="chip"><Icon name={packList.find((pack) => pack.id === id)?.icon ?? 'sparkle'} size={14} /><span class="name">{packLabel(id)}</span>
                <button aria-label={`Switch off ${packLabel(id)}`} onclick={() => togglePack(id)}><Icon name="close" size={14} /></button></span>
            {/each}
            {#if !activePacks.includes('web')}<span class="chip"><Icon name="globe" size={14} /><span class="name">No web search</span>
              <button aria-label="Switch web search back on" onclick={() => togglePack('web')}><Icon name="close" size={14} /></button></span>{/if}
          </div>
        {/if}
        {#if attached.length}
          <div class="chips">
            {#each attached as file, index}
              <span class="chip"><Icon name="clip" size={14} /><span class="name">{file.name}</span>
                <button aria-label={`Remove ${file.name}`} onclick={() => attached.splice(index, 1)}><Icon name="close" size={14} /></button></span>
            {/each}
          </div>
        {/if}
        <textarea rows="1" placeholder="Write to Clara" aria-label="Your message" bind:value={draft} use:autosize={draft}
          onkeydown={keydown} onpaste={paste}></textarea>
        <div class="composer-bar">
          <PlusMenu packs={packList} active={activePacks} projects={projectList} project={projectId} onFiles={() => picker?.click()}
            onToggle={togglePack} onProject={chooseProject} />
          <input type="file" multiple hidden bind:this={picker} onchange={pick} />
          <select class="model-select" aria-label="Model" value={conversationId ? conversation?.model ?? '' : fresh.model}
            onchange={(event) => setModel((event.target as HTMLSelectElement).value)}>
            <option value="">Default model ({models.default || 'none'})</option>
            {#each models.models as model (model.ref)}<option value={model.ref}>{model.ref}</option>{/each}
          </select>
          {#if busy}
            <button class="send stop" title="Stop" aria-label="Stop" onclick={stop}><Icon name="stop" /></button>
          {:else}
            <button class="send" title="Send" aria-label="Send" disabled={!draft.trim() && !attached.length} onclick={() => send()}><Icon name="send" /></button>
          {/if}
        </div>
      </div>
      <p class="composer-hint">Enter sends, Shift+Enter starts a new line. Drop or paste files to attach them (4 at most).</p>
    </div>
  </div>
</section>
