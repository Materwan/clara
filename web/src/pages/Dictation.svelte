<script lang="ts">
  import Checkbox from '../components/Checkbox.svelte';
  import Icon from '../components/Icon.svelte';
  import PackCards from '../components/PackCards.svelte';
  import { api } from '../lib/api';
  import { CLIENT_NAMES, type Client, type Edit, changes, editOf } from '../lib/dictation';
  import { loadModes, modeList } from '../lib/modeList.svelte';
  import { conversations, session, toast } from '../lib/state.svelte';

  // The Dictation page (Settings): what each client keeps for voice dictation (docs/DECISIONS.md, « Dictation goes into conversations, per client »):
  // automatic sending, the mode and the tool packs a new dictation conversation starts with, and the conversation it dictates into now.
  // Changing them takes a token that may answer every request: the server refuses the others, and says so.
  type Row = { saved: Client; edit: Edit; title: string; error: string; saving: boolean; making: boolean };
  let rows = $state<Row[]>([]);
  let packs = $state<any[]>([]);
  let failed = $state('');
  const may = $derived(session.approvals === 'all');

  async function titleOf(id: string | null): Promise<string> {
    if (!id) return '';
    try {
      const found = await api.get(`/v1/conversations/${id}`);
      return found.title || 'Dictation conversation';
    } catch {
      return 'Dictation conversation';
    }
  }

  async function load(): Promise<void> {
    try {
      const [found, offered] = await Promise.all([api.get('/v1/dictation/clients'), api.get('/v1/packs', { where: 'chat' }).catch(() => ({ packs: [] })), loadModes().catch(() => undefined)]);
      packs = offered.packs;
      rows = await Promise.all((found.clients as Client[]).map(async (client) => ({
        saved: client, edit: editOf(client), title: await titleOf(client.conversation), error: '', saving: false, making: false,
      })));
    } catch (error) {
      failed = (error as Error).message;
    }
  }

  $effect(() => {
    void load();
  });

  const dirty = (row: Row): boolean => Object.keys(changes(editOf(row.saved), row.edit)).length > 0;

  async function save(row: Row): Promise<void> {
    row.saving = true;
    row.error = '';
    try {
      const done: Client = await api.put(`/v1/dictation/clients/${row.saved.client}`, changes(editOf(row.saved), row.edit));
      row.saved = done;
      row.edit = editOf(done);
      toast(`Saved: ${CLIENT_NAMES[done.client]}.`, 'ok');
    } catch (error) {
      row.error = (error as Error).message; // the server's own words: a token that may not change them, a mode that is gone, a pack that does not exist
    } finally {
      row.saving = false;
    }
  }

  async function fresh(row: Row): Promise<void> {
    row.making = true;
    row.error = '';
    try {
      const made = await api.post(`/v1/dictation/clients/${row.saved.client}/conversation`);
      row.saved = { ...row.saved, conversation: made.conversation };
      row.title = made.title;
      conversations.changed++; // the list in the rail shows it
      toast('A new dictation conversation is ready: the next dictations from this client go into it.', 'ok');
    } catch (error) {
      row.error = (error as Error).message;
    } finally {
      row.making = false;
    }
  }

  const packLabel = (id: string): string => packs.find((pack) => pack.id === id)?.label ?? id;
  // a mode deleted meanwhile still shows as the one picked
  const modeNames = (row: Row): string[] => (row.edit.mode && !modeList.names.includes(row.edit.mode) ? [...modeList.names, row.edit.mode] : modeList.names);
</script>

<div class="page stack">
  <section class="panel stack">
    <h2>Dictation</h2>
    <p>Speak instead of typing: press the microphone <Icon name="mic" size={16} label="microphone" /> in the box you write in, speak, and press it again. Clara turns the audio into text on
      her own computer and never keeps it; only the text is stored, as an ordinary message. She still answers in text.</p>
    <ul class="notes small">
      <li><strong>Automatic sending is an answer given in advance.</strong> When it is on for a client, what the microphone hears is sent to Clara as a message at once, without being read first.
        When it is off, the text waits in the box until you send it.</li>
      <li><strong>A conversation that took dictation is outside content for good.</strong> A microphone also hears other voices, so the conversation counts as having read outside content
        and nothing lifts that. Anything Clara writes outside there (mail, Drive, GitHub…) still asks you, even with automatic sending on, as its mode says for a tainted turn.</li>
      <li>No spoken phrase ever approves a request of Clara: approvals are answered on their own cards.</li>
      <li>This site opens the microphone only when you press the button, and closes it when you press again, cancel, or leave the page or the conversation.</li>
    </ul>
    {#if !may}<p class="warn small" role="status">This token may not change dictation settings: automatic sending, the mode and the tools decide what runs without your answer.</p>{/if}
    {#if failed}<p class="danger" role="alert">{failed}</p>{/if}
  </section>

  {#each rows as row (row.saved.client)}
    <section class="panel stack" aria-labelledby={`dict-${row.saved.client}`}>
      <h3 id={`dict-${row.saved.client}`}>{CLIENT_NAMES[row.saved.client] ?? row.saved.client}
        {#if row.saved.auto_send}<span class="tag warn">sends at once</span>{/if}</h3>
      <label class="row">
        <Checkbox bind:checked={row.edit.auto_send} disabled={!may} />
        <span>Send the text at once (automatic sending)</span>
      </label>
      <label class="field narrow">Mode of its new dictation conversations
        <select value={row.edit.mode ?? ''} disabled={!may} onchange={(event) => (row.edit.mode = (event.target as HTMLSelectElement).value || null)}>
          <option value="">Default mode</option>
          {#each modeNames(row) as name (name)}<option value={name}>{name}</option>{/each}
        </select>
      </label>
      <details class="tools">
        <summary>Tools its new dictation conversations start with: {row.edit.packs.length ? row.edit.packs.map(packLabel).join(', ') : 'the usual ones'}</summary>
        <PackCards legend="Tools its new dictation conversations start with" {packs} bind:value={row.edit.packs}
          help="An empty choice means the tools every new conversation starts with." />
      </details>
      <p class="small">Dictating into:
        {#if row.saved.conversation}<a href={'#/chat/' + row.saved.conversation}>{row.title}</a>{:else}<span class="muted">no conversation yet (the next dictation makes one)</span>{/if}</p>
      <div class="row">
        <button type="button" class="primary" disabled={!may || !dirty(row) || row.saving} onclick={() => save(row)}>{row.saving ? 'Saving…' : 'Save'}</button>
        <button type="button" disabled={row.making} onclick={() => fresh(row)}><Icon name="plus" size={16} />New dictation conversation</button>
      </div>
      {#if row.error}<p class="danger small" role="alert">{row.error}</p>{/if}
    </section>
  {/each}
</div>

<style>
  .notes { margin: 0; padding-left: var(--sp-20); display: flex; flex-direction: column; gap: var(--sp-6); }
  summary { cursor: pointer; font-size: var(--fs-sm); font-weight: 600; color: var(--text-2); margin-bottom: var(--sp-8); }
  h3 { display: flex; align-items: center; gap: var(--sp-10); flex-wrap: wrap; }
</style>
