<script lang="ts">
  import { tick, untrack } from 'svelte';
  import ApprovalCard from '../components/ApprovalCard.svelte';
  import AskQuestions from '../components/AskQuestions.svelte';
  import Checkbox from '../components/Checkbox.svelte';
  import FileCard from '../components/FileCard.svelte';
  import GrantBar from '../components/GrantBar.svelte';
  import Icon from '../components/Icon.svelte';
  import LevelSelect from '../components/LevelSelect.svelte';
  import Mark from '../components/Mark.svelte';
  import PlusMenu from '../components/PlusMenu.svelte';
  import SidePanel from '../components/SidePanel.svelte';
  import { ApiError, api, fileToBase64, stream, zone } from '../lib/api';
  import { answerFolderCall, budgetBytes, isFolderTool } from '../lib/folders';
  import { type Document, repack, unpack } from '../lib/documents';
  import { grants, stop as stopGrants } from '../lib/grants.svelte';
  import { ladder } from '../lib/levels.svelte';
  import { markdown } from '../lib/markdown';
  import { type PanelFile, type PanelNote, panel, remember } from '../lib/panel.svelte';
  import { type Form, type Progress, newForm, quizMessage } from '../lib/qcm';
  import { conversations, go, live, narrow, route, toast, ui } from '../lib/state.svelte';
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
    content: string; // the message as it is stored: what you wrote, and the documents in it (a reply's text is in its parts)
    shown?: string; // your words alone: what the bubble draws (the documents are chips)
    quiz?: { label: string; body: string }; // an answer sent from the QCM panel: drawn as a label and what was answered
    documents?: Document[]; // the documents of a stored message, kept to put them back when you edit it
    attachments: { name: string; chars?: number }[];
    parts: Part[];
    thinking?: string;
    thinkingOpen?: boolean;
    files: any[];
    notes: string[];
    versions?: number[];
    version?: number;
    streaming?: boolean;
    compacting?: boolean; // the answer is complete and stored; the conversation is being summarised after it
    error?: string;
    interrupted?: boolean; // the stored reply was cut off (its text says why)
    upload?: { name: string; data: string }[]; // the files of a question not stored yet, to send again with it
  };

  const blank = (role: Message['role'], content = ''): Message => ({ role, content, attachments: [], parts: [], files: [], notes: [] });
  /** Your message as the site draws it: your words in the bubble, a chip for each document in it. */
  function yours(content: string): Message {
    const sent = unpack(content);
    const message = blank('user', content);
    message.quiz = quizMessage(content) ?? undefined;
    message.shown = sent.text;
    message.documents = sent.documents;
    message.attachments = sent.documents.map(({ name, chars }) => ({ name, chars }));
    return message;
  }
  const SILENT = new Set(['qcm']); // the form is its own card
  // calls that can leave a file or a note for the side panel
  const WRITES = new Set(['ws_write', 'ws_edit', 'ws_move', 'ws_delete', 'ws_run', 'vault_write', 'vault_edit', 'vault_move', 'vault_daily', 'vault_daily_block']);
  const INTERRUPTED = '[This answer was interrupted'; // the note the server stores at the end of a reply cut off (turn.py)

  let conversationId = $derived(route.params.id ?? null);
  let conversation = $state<any>(null);
  let messages = $state<Message[]>([]);
  let draft = $state('');
  let attached = $state<File[]>([]);
  // A folder on this computer (DECISIONS.md, « Folders on your computer »): the browser reads it for Clara's folder tools.
  // It belongs to the conversation it was opened in (or to the first message sent from it); leaving that ends it.
  type Folder = { id: string; name: string; handle: FileSystemDirectoryHandle; budgetMb: string; conversation: string | null };
  let folder = $state<Folder | null>(null);
  let turnFolder: Folder | null = null; // the folder the turn in progress was sent with: its tool calls read this one only
  const folderSupported = typeof window !== 'undefined' && 'showDirectoryPicker' in window;
  // The side panel: what Clara made in this conversation (files, vault notes, QCMs), and the questions she waits on
  let side = $state<{ files: PanelFile[]; notes: PanelNote[]; forms: Form[] }>({ files: [], notes: [], forms: [] });
  let sideOpen = $state(false);
  let quiz = $state<string | null>(null); // the QCM open in the panel (null: the list of cards)
  let pendingAsk = $state<any>(null); // ask_questions: the questions that replace the box you write in
  let outbox: string[] = []; // answers of a QCM to send Clara, one after the other, when she has finished the one before
  let panelTimer: ReturnType<typeof setTimeout> | undefined;
  let busy = $state(false);
  const compacting = $derived(messages.some((message) => message.compacting));
  let dragging = $state(false);
  let controller: AbortController | null = null;
  let models = $state<any>({ models: [], default: '' });
  let fresh = $state({ incognito: false, local_only: false, model: '', packs: ['web'] as string[], level: null as number | null });
  /** The auto level of the conversation on screen: its own (null: it follows), the one it runs at, and what it follows. */
  let level = $state<{ own: number | null; level: number; source: string }>({ own: null, level: 3, source: 'default' });
  let projectLevel = $state<number | null>(null);
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
    if (id && id === skipReload) {
      skipReload = ''; // only the reload that `send` made unnecessary: coming back later reads the conversation
      return;
    }
    load(id);
  });

  // Leaving the conversation a folder was opened in ends the folder (a folder opened in a new chat waits for its first message)
  $effect(() => {
    const id = conversationId;
    untrack(() => {
      if (folder?.conversation && folder.conversation !== id) folder = null;
    });
  });

  // The project of the conversation: its name for the header, and for a conversation not started yet the special tools the project begins
  // its conversations with (the "+" menu can still change them); outside any project a new conversation has web search only.
  $effect(() => {
    const id = projectId;
    projectName = '';
    projectLevel = null;
    const fresher = untrack(() => !conversationId);
    if (!id) {
      if (fresher) fresh.packs = ['web'];
      return;
    }
    api.get(`/v1/projects/${id}`).then((project) => {
      if (projectId !== id) return;
      projectName = project.name;
      projectLevel = project.auto_level;
      if (!conversationId) fresh.packs = project.packs;
    }).catch(() => {});
  });

  // The auto level of the conversation on screen. A conversation not started yet has the one you pick for it, else its project's, else the default.
  $effect(() => {
    const id = conversationId;
    void live.refresh;
    if (!id) {
      level = fresh.level !== null ? { own: fresh.level, level: fresh.level, source: 'own' }
        : projectLevel !== null ? { own: null, level: projectLevel, source: 'project' } : { own: null, level: ladder.defaults.chat, source: 'default' };
      return;
    }
    api.get(`/v1/conversations/${id}/level`).then((found) => {
      if (conversationId === id) level = found;
    }).catch(() => {});
  });
  const followed = $derived(level.source === 'project' ? 'the project' : level.source === 'automation' ? 'its schedule or hook' : 'the default');

  async function setLevel(value: number | null): Promise<void> {
    if (!conversationId) {
      fresh.level = value;
      return;
    }
    try {
      level = await api.put(`/v1/conversations/${conversationId}/level`, { level: value });
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  let loads = 0; // the latest load: an older one that ends late draws nothing

  /** Leaving a conversation: the answer in it goes on without us, only the stream we read it from is dropped. */
  function leave(): void {
    controller?.abort();
    controller = null;
    busy = false;
  }

  async function load(id: string | null): Promise<void> {
    const mine = ++loads;
    leave();
    conversation = null;
    messages = [];
    side = { files: [], notes: [], forms: [] };
    sideOpen = false;
    quiz = null;
    pendingAsk = null;
    outbox = [];
    clearTimeout(panelTimer);
    models = await api.get('/v1/models').catch(() => ({ models: [], default: '' }));
    packList = (await api.get('/v1/packs', { where: 'chat' }).catch(() => ({ packs: [] }))).packs;
    projectList = (await api.get('/v1/projects').catch(() => ({ projects: [] }))).projects;
    if (mine !== loads) return;
    if (!id) chosenProject = undefined;
    if (!id) return;
    try {
      const found = await api.get(`/v1/conversations/${id}`);
      if (mine !== loads) return;
      conversation = found;
      const history = (await api.get(`/v1/conversations/${id}/messages`, { calls: true })).messages;
      if (mine !== loads) return;
      // An answer still being given is followed from its start: its approval cards come with it
      draw(found, history, found.answering ? [] : (found.approvals ?? []));
      loadPanel(id, true);
      if (found.answering) attach(id, found, history);
    } catch (error) {
      if (mine !== loads) return;
      toast((error as Error).message, 'error');
      go('/chat');
    }
  }

  function draw(found: any, history: any[], waiting: any[]): void {
    conversation = found;
    messages = group(history, waiting);
    scrollDown(true);
  }

  /** The stored messages of a conversation, drawn again (an answer that ended while we were not reading it). */
  async function refresh(id: string): Promise<void> {
    const found = await api.get(`/v1/conversations/${id}`);
    const history = (await api.get(`/v1/conversations/${id}/messages`, { calls: true })).messages;
    if (conversationId === id) draw(found, history, found.approvals ?? []);
  }

  // ---- the side panel -------------------------------------------------------------------------------------
  const cards = $derived(side.files.length + side.notes.length + side.forms.length);
  const showPanel = $derived(cards > 0 && sideOpen);

  /** Read what the side panel shows. `first`: the conversation has just been opened (the panel starts closed, unless you left it open). */
  async function loadPanel(id: string, first = false): Promise<void> {
    try {
      const found = await api.get(`/v1/conversations/${id}/panel`);
      if (conversationId !== id) return;
      const before = cards;
      // a QCM Clara has just made is not stored until her message ends: it stays in the list until the server has it
      const unsaved = side.forms.filter((form) => form.pending && !found.forms.some((item: Form) => item.ref === form.ref));
      side = { files: found.files, notes: found.notes ?? [], forms: [...found.forms, ...unsaved] };
      if (first) sideOpen = panel.choice === 'open' && cards > 0;
      else if (cards > before && panel.choice !== 'closed') sideOpen = true;
    } catch {
      /* the panel is a convenience: the conversation does not depend on it */
    }
  }

  function schedulePanel(id: string): void {
    clearTimeout(panelTimer);
    panelTimer = setTimeout(() => loadPanel(id), 400);
  }

  function togglePanel(): void {
    sideOpen = !sideOpen;
    remember(sideOpen ? 'open' : 'closed');
  }

  function closePanel(): void {
    sideOpen = false;
    remember('closed');
  }

  function openQuiz(ref: string): void {
    quiz = ref;
    sideOpen = true;
  }

  function progressed(ref: string, progress: Progress): void {
    side.forms = side.forms.map((form) => (form.ref === ref ? { ...form, progress } : form));
  }

  /** An answer of the QCM for Clara: sent now, or when she has finished the one before (the next question is already there to answer). */
  function tell(text: string): void {
    if (busy || outbox.length) outbox.push(text);
    else send(text);
  }

  function drain(): void {
    const next = outbox.shift();
    if (next !== undefined && !busy) send(next);
    else if (next !== undefined) outbox.unshift(next);
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
        const message = yours(item.content);
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
        if (item.content?.includes(INTERRUPTED)) answer.interrupted = true;
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

  // The page follows the answer as it streams, until you scroll up to read: then it stays where you are, and follows again once you are back at the bottom.
  let following = true;
  let lastTop = 0;

  function onScroll(): void {
    if (!scroller) return;
    const away = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
    if (away <= 2) following = true;
    else if (scroller.scrollTop < lastTop) following = false;
    else if (away < 40) following = true;
    lastTop = scroller.scrollTop;
  }

  function scrollDown(force = false): void {
    if (force) following = true;
    if (!following) return;
    queueMicrotask(() => scroller?.scrollTo({ top: scroller.scrollHeight }));
  }

  /** Archived: no new message. Unarchiving here is the same switch as the rail's. */
  async function unarchive(): Promise<void> {
    try {
      await api.patch(`/v1/conversations/${conversation.id}`, { archived: false });
      conversation = { ...conversation, archived: false, archived_by: '' };
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  /** Draw the events of an answer into `current` until its stream ends (`id`: the conversation they belong to). */
  async function follow(events: AsyncIterable<any>, user: Message, current: Message, id: string): Promise<void> {
    for await (const event of events) {
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
          if (call) Object.assign(call, { running: false, result: event.result, granted: event.grant });
          else addCall(current, { name: event.name, arguments: event.arguments, result: event.result, granted: event.grant });
          if (WRITES.has(event.name)) schedulePanel(id);
          break;
        }
        case 'approval':
          current.parts.push({ kind: 'approval', approval: event.approval });
          break;
        case 'qcm':
          current.parts.push({ kind: 'form', form: event.form });
          if (!side.forms.some((form) => form.ref === event.form.ref)) side.forms.push(newForm(event.form));
          openQuiz(event.form.ref);
          break;
        case 'questions':
          pendingAsk = event.ask;
          break;
        case 'questions_done':
          if (pendingAsk?.id === event.id) pendingAsk = null;
          break;
        case 'file':
          current.files.push(event);
          schedulePanel(id);
          break;
        case 'retrying':
          current.notes.push(event.message);
          break;
        case 'warning':
          current.notes.push(event.message);
          current.compacting = false;
          break;
        case 'compacting':
          current.compacting = true;
          break;
        case 'compacted':
          current.compacting = false;
          current.notes.push(`Summarised: ${Math.round(event.before)}% → ${Math.round(event.after)}% of the context.`);
          break;
        case 'discarded':
          current.notes.push(`The replaced answer had run: ${event.tools.join(', ')} (what they changed stays changed).`);
          break;
        case 'done':
          user.id = event.message_id ?? undefined;
          user.versions = event.versions?.length > 1 ? event.versions : undefined;
          user.version = user.versions ? user.versions.indexOf(event.message_id) + 1 : undefined;
          if (conversation?.id === id) conversation.context = event.context;
          loadPanel(id);
          break;
        case 'error':
          current.error = event.message;
          break;
        case 'tool_requests':
          void answerFolderCalls(event, turnFolder);
          break;
      }
      scrollDown();
    }
  }

  /** The end of a stream we were reading: ready for the next question, unless we left for another conversation. */
  async function finished(mine: AbortController, id: string | null): Promise<void> {
    conversations.changed++;
    if (controller !== mine) return; // left, or another stream has taken over
    controller = null;
    busy = false;
    pendingAsk = null; // the answer is over: nobody waits for these questions any more
    drain();
    if (!id) return;
    const latest = await api.get(`/v1/conversations/${id}`).catch(() => null);
    if (latest && conversationId === id) conversation = latest;
  }

  /** Opens a folder on this computer for this conversation: the browser's picker, read-only, with a budget set in the chip. */
  async function pickFolder(): Promise<void> {
    try {
      const handle = await (window as any).showDirectoryPicker({ mode: 'read' }) as FileSystemDirectoryHandle;
      folder = { id: crypto.randomUUID(), name: handle.name, handle, budgetMb: '', conversation: conversationId };
    } catch (error) {
      if ((error as Error).name !== 'AbortError') toast((error as Error).message, 'error');
    }
  }

  /** The browser answers a turn's folder calls from the folder that turn was sent with, and nothing else. */
  async function answerFolderCalls(event: any, granted: Folder | null): Promise<void> {
    const results: { id: string; content: string }[] = [];
    for (const call of event.calls ?? []) {
      const content = granted && isFolderTool(call.name)
        ? await answerFolderCall(granted.handle, call.name, call.arguments ?? {})
        : JSON.stringify({ error: 'The folder is no longer open in this conversation: open it again.' });
      results.push({ id: call.id, content });
    }
    await api.post(`/v1/turns/${event.turn}/tool-results`, { results }).catch((error) => toast((error as Error).message, 'error'));
  }

  async function send(text = draft, forkOf: number | null = null, carried?: { name: string; data: string }[]): Promise<void> {
    if (conversation?.archived) return;
    if (busy || (!text.trim() && !attached.length)) return;
    busy = true;
    const mine = new AbortController();
    controller = mine;
    const files = carried ?? await Promise.all(attached.map(async (file) => ({ name: file.name, data: await fileToBase64(file) })));
    let id = conversationId;
    let current: Message | undefined;
    const granted = folder;
    try {
      if (!id) {
        const made = await api.post('/v1/conversations', { incognito: fresh.incognito, local_only: fresh.local_only, model: fresh.model, project: projectId ?? undefined, packs: fresh.packs, auto_level: fresh.level ?? undefined });
        id = made.id as string;
        skipReload = id;
        go('/chat/' + id);
        conversation = made;
      }
      if (granted && !granted.conversation) granted.conversation = id;
      turnFolder = granted;
      if (forkOf !== null) {
        const index = messages.findIndex((message) => message.id === forkOf);
        if (index >= 0) messages = messages.slice(0, index);
      }
      const user = yours(text);
      user.upload = files;
      user.attachments.push(...files.map((file) => ({ name: file.name })));
      const answer = blank('assistant');
      answer.streaming = true;
      messages.push(user, answer);
      current = messages[messages.length - 1];
      draft = '';
      attached = [];
      scrollDown(true);
      // keep_running: the answer goes on if this page is closed or left, and is followed again from where it is
      const body = {
        message: text, conversation: id, attachments: files, timezone: zone, fork_of: forkOf ?? undefined, keep_running: true,
        folder: granted ? { id: granted.id, name: granted.name, budget_bytes: budgetBytes(granted.budgetMb) } : undefined,
      };
      await follow(stream('POST', '/v1/chat/stream', body, mine.signal), user, current, id);
      current.streaming = false;
      messages = [...messages];
    } catch (error) {
      if ((error as Error).name !== 'AbortError') toast((error as Error).message, 'error');
    } finally {
      if (current) current.streaming = false;
      turnFolder = null;
      finished(mine, id);
    }
  }

  /** Follow the answer Clara is still giving in a conversation we opened: the page was closed, or left, while she worked. */
  async function attach(id: string, found: any, history: any[]): Promise<void> {
    busy = true;
    const mine = new AbortController();
    controller = mine;
    let current: Message | undefined;
    try {
      const events = stream('GET', `/v1/conversations/${id}/run`, undefined, mine.signal);
      const first = await events.next();
      if (first.done || first.value.type !== 'resume') return;
      const user = yours(first.value.message);
      for (const name of first.value.attachments ?? []) {
        if (!user.attachments.some((file) => file.name === name)) user.attachments.push({ name });
      }
      const answer = blank('assistant');
      answer.streaming = true;
      messages.push(user, answer);
      current = messages[messages.length - 1];
      scrollDown(true);
      await follow(events, messages[messages.length - 2], current, id);
      current.streaming = false;
      messages = [...messages];
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        // not an answer we can follow (it ended just now, or it is not kept running): what is stored is what there is
        if (conversationId === id) draw(found, history, found.approvals ?? []);
      } else if ((error as Error).name !== 'AbortError') toast((error as Error).message, 'error');
    } finally {
      if (current) current.streaming = false;
      finished(mine, id);
    }
  }

  /** Stop the answer. It goes on without us, so it is told to stop; then what it had said is read back, marked as cut. */
  async function stop(): Promise<void> {
    const id = conversationId;
    outbox = [];
    pendingAsk = null;
    if (id) await api.del(`/v1/conversations/${id}/run`).catch(() => {});
    controller?.abort();
    if (id) await refresh(id).catch(() => {});
  }

  /** A reply that failed or was cut off can be tried again: its question is asked again (a new version when it is stored). */
  function canRetry(message: Message, index: number): boolean {
    const question = messages[index - 1];
    if (busy || question?.role !== 'user' || !(message.error || message.interrupted)) return false;
    return question.id !== undefined || index === messages.length - 1; // a question not stored: the reply after it is dropped
  }

  async function retry(index: number): Promise<void> {
    const question = messages[index - 1];
    const stored = question.id !== undefined; // a stored question carries its documents in its content: none sent again
    if (!stored) messages = messages.slice(0, index - 1);
    await send(question.content, question.id ?? null, stored ? [] : (question.upload ?? []));
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

  // On a phone the panel is a drawer over the page: the keys go into it when it opens (the page behind is inert)
  $effect(() => {
    if (showPanel && narrow.rail) tick().then(() => document.querySelector<HTMLElement>('.side-panel .side-head button')?.focus());
  });

  // Approve all next belongs to the conversation on screen and to this page: leaving either ends it
  $effect(() => {
    if (grants.conversation && grants.conversation !== conversationId) stopGrants();
  });
  $effect(() => stopGrants);
</script>

<!-- Escape closes the panel when it is a drawer over the page (a phone) and the keys are in it -->
<svelte:window onkeydown={(event) => { if (event.key === 'Escape' && showPanel && narrow.rail && event.target instanceof Element && event.target.closest('.side-panel')) closePanel(); }} />

{#snippet callDetail(call: Shown)}
  <div class="call-detail">
    <div><div class="call-key">Given</div><pre>{JSON.stringify(call.arguments, null, 2)}</pre></div>
    <div><div class="call-key">Returned</div>
      {#if call.result !== undefined}<pre>{call.result}</pre>{:else}<p class="small muted">{call.running ? 'Still running…' : 'Not kept.'}</p>{/if}</div>
  </div>
{/snippet}

{#snippet files(message: Message)}
  <div class="chips notes">{#each message.attachments as file}<span class="chip" title={file.chars ? `${file.chars.toLocaleString()} characters, sent with the message` : undefined}><Icon name="clip" size={14} /><span class="name">{file.name}</span></span>{/each}</div>
{/snippet}

{#snippet callLine(call: Shown)}
  {@const said = note(call)}
  <span class="run-summary"><b>{said.label}</b>{said.detail ? ` ${said.detail}` : ''}</span>
  {#if call.granted}<span class="tag warn" title="Allowed by Approve all next">auto-approved</span>{/if}
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
    {#if cards}
      <button class="ghost panel-toggle" aria-label={showPanel ? 'Close the side panel' : 'Open the side panel'} aria-expanded={showPanel}
        title="Files, notes and quizzes made in this conversation" onclick={togglePanel}>
        <Icon name="panel" size={18} /><span class="count">{cards}</span>
      </button>
    {/if}
  </header>

  <div class="chat-body">
  <div class="chat-main" inert={showPanel && narrow.rail}>

  <div class="messages" bind:this={scroller} onscroll={onScroll}>
    <div class="messages-inner">
      {#if !messages.length}
        <div class="welcome">
          <Mark size={56} />
          <h2 class="greeting">{greeting()}</h2>
          <p>Ask Clara anything. The notes she writes are saved in your workspace and show here as cards.</p>
          {#if !models.default}
            <div class="flag-notes" role="status">
              <p class="flag-note"><Icon name="chip" size={16} /><span>No default model is chosen yet, so Clara cannot answer. <a href="#/models">Choose one on Models</a> (a local Ollama model, or one of the providers whose key you add under Admin &gt; Secrets).</span></p>
            </div>
          {/if}
          {#if !conversationId && (fresh.incognito || fresh.local_only)}
            <div class="flag-notes" role="status">
              {#if fresh.incognito}<p class="flag-note"><Icon name="incognito" size={16} />Incognito: no facts are read or written, and the conversation is deleted 24 hours after its last message.</p>{/if}
              {#if fresh.local_only}<p class="flag-note"><Icon name="home" size={16} />Local only: only local models answer; nothing leaves your machines.</p>{/if}
            </div>
          {/if}
        </div>
      {/if}
      {#each messages as message, index (index)}
        {#if message.role === 'user'}
          <div class="msg user">
            <div class="body">
              {#if editing === message.id}
                <div class="edit">
                  <textarea rows="3" bind:value={editText} aria-label="Your question"></textarea>
                  {#if message.documents?.length}{@render files(message)}<span class="small muted">The files are sent again with it.</span>{/if}
                  <div class="row">
                    <button class="small" onclick={() => (editing = null)}>Cancel</button>
                    <button class="primary small" onclick={() => { const id = editing; editing = null; send(repack(editText, message.documents ?? []), id); }}>Send edited</button>
                  </div>
                </div>
              {:else}
                {#if message.quiz}
                  <div class="bubble quiz-bubble"><span class="quiz-label">{message.quiz.label}</span>
                    {#if message.quiz.body}<div class="markdown" use:markdown={message.quiz.body}></div>{/if}</div>
                {:else if message.shown?.trim()}<div class="bubble markdown" use:markdown={message.shown}></div>{/if}
                {#if message.attachments.length}
                  {@render files(message)}
                {/if}
                {#if !message.quiz && (message.versions || (message.id && !busy))}
                  <div class="msg-actions">
                    {#if message.versions}
                      <button class="ghost" aria-label="Previous version" onclick={() => switchVersion(message, -1)} disabled={message.version === 1}><Icon name="left" size={16} /></button>
                      <span>{message.version}/{message.versions.length}</span>
                      <button class="ghost" aria-label="Next version" onclick={() => switchVersion(message, 1)} disabled={message.version === message.versions.length}><Icon name="chevron" size={16} /></button>
                    {/if}
                    {#if message.id && !busy}
                      <button class="ghost" title="Ask again" aria-label="Ask again" onclick={() => send(message.content, message.id!)}><Icon name="refresh" size={16} /></button>
                      <button class="ghost" title="Edit" aria-label="Edit" onclick={() => { editing = message.id!; editText = message.shown ?? message.content; }}><Icon name="edit" size={16} /></button>
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
                    <ApprovalCard approval={part.approval} allowGrant={part.approval.conversation === conversationId} />
                  {:else}
                    {@const made = side.forms.find((item) => item.ref === part.form.ref)}
                    <button class="quiz-chip" onclick={() => openQuiz(part.form.ref)} aria-label={`Open the quiz ${part.form.title || ''} in the side panel`.trim()}>
                      <Icon name="quiz" size={18} />
                      <span class="info"><strong>{part.form.title || 'Quiz'}</strong>
                        <span>{part.form.questions.length} questions{made && made.progress.summary.answered ? ` · ${made.progress.summary.answered} answered` : ''} · open in the panel</span></span>
                      <Icon name="chevron" size={16} />
                    </button>
                  {/if}
                {/each}
                {#if message.streaming && !message.parts.length && !message.thinking}<span class="waiting" role="status" aria-label="Clara is answering"></span>{/if}
                {#each message.files as card}<FileCard {card} />{/each}
              </div>
              {#if message.compacting}
                <div class="compacting" role="status"><span class="spin" aria-hidden="true"></span><span>Compacting the conversation…</span></div>
              {/if}
              {#if message.notes.length}
                <div class="reply-notes">{#each message.notes as line}<span>{line}</span>{/each}</div>
              {/if}
              {#if message.error}<div class="failed-note"><Icon name="warn" size={18} /><span>{message.error}</span></div>{/if}
              {#if canRetry(message, index)}
                <div class="retry"><button class="small" onclick={() => retry(index)}><Icon name="refresh" size={16} /> Try again</button></div>
              {/if}
            </div>
          </div>
        {/if}
      {/each}
    </div>
  </div>

  <div class="composer" class:drop={dragging} role="group" aria-label="Write to Clara"
    ondragover={(event) => { event.preventDefault(); dragging = true; }} ondragleave={() => (dragging = false)} ondrop={drop}>
    <div class="composer-frame">
      {#if conversation?.archived}
        <p class="composer-hint" role="status">This conversation is archived: you can read it, but it takes no new message.
          <button type="button" class="link" onclick={unarchive}>Unarchive</button></p>
      {/if}
      {#if pendingAsk}
        {#key pendingAsk.id}
          <AskQuestions ask={pendingAsk} onDone={() => (pendingAsk = null)} />
        {/key}
      {:else}
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
        {#if folder}
          <div class="chips">
            <span class="chip folder-chip" title="A folder on your computer: Clara walks it and reads what she needs. Only this conversation.">
              <Icon name="folder" size={14} /><span class="name">{folder.name}</span>
              <label class="folder-budget">budget <input type="number" min="0" max="100" step="0.5" inputmode="decimal" placeholder="1"
                bind:value={folder.budgetMb} aria-label="How many MB of file content Clara may read from this folder (empty: the default)" /> MB</label>
              <button aria-label={`Close the folder ${folder.name}`} title="Close the folder" onclick={() => (folder = null)}><Icon name="close" size={14} /></button>
            </span>
          </div>
        {/if}
        <textarea rows="1" placeholder="Write to Clara" aria-label="Your message" bind:value={draft} use:autosize={draft}
          onkeydown={keydown} onpaste={paste} disabled={!!conversation?.archived}></textarea>
        <div class="composer-bar">
          <PlusMenu packs={packList} active={activePacks} projects={projectList} project={projectId} onFiles={() => picker?.click()}
            onFolder={pickFolder} {folderSupported} onToggle={togglePack} onProject={chooseProject} />
          <input type="file" multiple hidden bind:this={picker} onchange={pick} />
          <LevelSelect value={level.own} effective={level.level} follows={followed} onchange={setLevel} small />
          <select class="model-select" aria-label="Model" value={conversationId ? conversation?.model ?? '' : fresh.model}
            onchange={(event) => setModel((event.target as HTMLSelectElement).value)}>
            <option value="">Default model ({models.default || 'none'})</option>
            {#each models.models as model (model.ref)}<option value={model.ref}>{model.ref}</option>{/each}
          </select>
          {#if busy}
            <button class="send stop" title={compacting ? 'Stop compacting' : 'Stop'} aria-label={compacting ? 'Stop compacting' : 'Stop'} onclick={stop}><Icon name="stop" /></button>
          {:else}
            <button class="send" title="Send" aria-label="Send" disabled={!!conversation?.archived || (!draft.trim() && !attached.length)} onclick={() => send()}><Icon name="send" /></button>
          {/if}
        </div>
      </div>
      {/if}
      <p class="composer-hint">Enter sends, Shift+Enter starts a new line. Drop or paste files to attach them (4 at most).</p>
    </div>
  </div>
  <GrantBar />
  </div>
  {#if showPanel && conversationId}
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions (Escape closes the panel too) -->
    <div class="side-scrim" onclick={closePanel} aria-hidden="true"></div>
    <aside class="side-panel" aria-label="Made in this conversation">
      <SidePanel conversation={conversationId} files={side.files} notes={side.notes} forms={side.forms} bind:quiz onClose={closePanel} onSend={tell} onProgress={progressed} />
    </aside>
  {/if}
  </div>
</section>
