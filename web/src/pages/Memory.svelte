<script lang="ts">
  import Checkbox from '../components/Checkbox.svelte';
  import Icon from '../components/Icon.svelte';
  import Segmented from '../components/Segmented.svelte';
  import { api, localInput, when } from '../lib/api';
  import { confirmDialog, promptDialog } from '../lib/dialog.svelte';
  import { toast } from '../lib/state.svelte';

  const STATES = ['live', 'unconfirmed', 'expired', 'superseded', 'all'].map((value) => ({ value, label: value.charAt(0).toUpperCase() + value.slice(1) }));
  let shown = $state('live');
  let query = $state('');
  let facts = $state<any[]>([]);
  let count = $state(0);
  let text = $state('');
  let category = $state('');
  let expires = $state('');
  let pinned = $state(false);
  let editing = $state<number | null>(null);
  let editText = $state('');

  async function load(): Promise<void> {
    const found = await api.get('/v1/memory/facts', { state: shown, q: query });
    facts = found.facts;
    count = found.live;
  }

  $effect(() => {
    void shown;
    load();
  });

  async function add(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    try {
      await api.post('/v1/memory/facts', { text, category, pinned, expires_at: expires || null });
      text = category = expires = '';
      pinned = false;
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function change(fact: any, fields: any): Promise<void> {
    try {
      await api.patch(`/v1/memory/facts/${fact.id}`, fields);
      editing = null;
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function confirmFact(fact: any): Promise<void> {
    await api.post(`/v1/memory/facts/${fact.id}/confirm`);
    load();
  }

  async function forget(fact: any): Promise<void> {
    if (!(await confirmDialog({ title: 'Forget a fact', message: 'Forget this fact? Clara will not save it again by herself.', confirm: 'Forget', danger: true }))) return;
    await api.del(`/v1/memory/facts/${fact.id}`);
    load();
  }

  async function expire(fact: any): Promise<void> {
    const until = await promptDialog({
      title: 'Hold until', message: 'This fact holds until:', label: 'Holds until', type: 'datetime-local', confirm: 'Set',
      value: localInput(new Date(Date.now() + 7 * 864e5).toISOString()),
    });
    if (until) change(fact, { expires_at: until });
  }
</script>

<div class="page stack">
  <form class="panel row end" onsubmit={add}>
    <label class="field grow">Fact <input placeholder="A fact about you, in one short sentence" bind:value={text} maxlength="300" required /></label>
    <label class="field">Category <input bind:value={category} maxlength="32" /></label>
    <label class="field">Holds until <input type="datetime-local" bind:value={expires} /></label>
    <label class="small row pinned"><Checkbox bind:checked={pinned} />Pinned</label>
    <button class="primary">Add</button>
  </form>
  <div class="row">
    <Segmented label="Which facts" options={STATES} bind:value={shown} />
    <span class="muted small push">{count} facts</span>
    <input type="search" aria-label="Search facts" placeholder="Search" bind:value={query} onchange={load} />
  </div>
  <table>
    <thead><tr><th>Fact</th><th>Source</th><th>Since</th><th><span class="sr-only">Actions</span></th></tr></thead>
    <tbody>
      {#each facts as fact (fact.id)}
        <tr>
          <td>
            {#if editing === fact.id}
              <input class="fact-edit" aria-label="Fact" bind:value={editText} />
              <button class="small primary" onclick={() => change(fact, { text: editText })}>Save</button>
            {:else}
              {#if fact.pinned}<Icon name="pin" size={16} label="Pinned" />{/if}
              {fact.text}
              {#if fact.category}<span class="tag">{fact.category}</span>{/if}
              {#if !fact.confirmed}<span class="tag warn">unconfirmed</span>{/if}
              {#if fact.expires_at}<span class="tag">until {when(fact.expires_at)}</span>{/if}
              {#if fact.superseded_by}<span class="tag">replaced by #{fact.superseded_by}</span>{/if}
            {/if}
          </td>
          <td class="small">{fact.source}</td>
          <td class="small muted">{when(fact.created_at)}</td>
          <td class="row">
            {#if !fact.confirmed}<button class="small primary" onclick={() => confirmFact(fact)}>Confirm</button>{/if}
            <button class="small" onclick={() => { editing = fact.id; editText = fact.text; }}>Edit</button>
            <button class="small" onclick={() => change(fact, { pinned: !fact.pinned })}>{fact.pinned ? 'Unpin' : 'Pin'}</button>
            {#if fact.expires_at}
              <button class="small" onclick={() => change(fact, { expires_at: '' })}>Keep</button>
            {:else}
              <button class="small" onclick={() => expire(fact)}>Expire…</button>
            {/if}
            <button class="small danger" onclick={() => forget(fact)}>Forget</button>
          </td>
        </tr>
      {:else}
        <tr><td colspan="4" class="empty">Nothing here.</td></tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  .fact-edit {
    width: 70%;
  }
  .pinned {
    min-height: 2.2rem; /* as tall as the fields beside it, so the box sits level with them */
  }
</style>
