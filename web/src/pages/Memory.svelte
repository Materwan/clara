<script lang="ts">
  import { api, localInput, when } from '../lib/api';
  import { toast } from '../lib/state.svelte';

  const STATES = ['live', 'unconfirmed', 'expired', 'superseded', 'all'];
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
    if (!confirm('Forget this fact? Clara will not save it again by herself.')) return;
    await api.del(`/v1/memory/facts/${fact.id}`);
    load();
  }
</script>

<div class="page stack">
  <h1>Memory <span class="muted small">{count} facts</span></h1>
  <form class="panel row" onsubmit={add}>
    <input class="grow" placeholder="A fact about you (one short sentence)" bind:value={text} maxlength="300" required />
    <input placeholder="category" bind:value={category} maxlength="32" />
    <label class="small row" title="until when it holds">until <input type="datetime-local" bind:value={expires} /></label>
    <label class="small row"><input type="checkbox" bind:checked={pinned} /> pinned</label>
    <button class="primary">Add</button>
  </form>
  <div class="row">
    <div class="tabs grow">
      {#each STATES as name}<button class:active={shown === name} onclick={() => (shown = name)}>{name}</button>{/each}
    </div>
    <input placeholder="Search" bind:value={query} onchange={load} />
  </div>
  <table>
    <thead><tr><th>Fact</th><th>Source</th><th>Since</th><th></th></tr></thead>
    <tbody>
      {#each facts as fact (fact.id)}
        <tr>
          <td>
            {#if editing === fact.id}
              <input class="wide" bind:value={editText} />
              <button class="small primary" onclick={() => change(fact, { text: editText })}>Save</button>
            {:else}
              {fact.pinned ? '📌 ' : ''}{fact.text}
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
              <button class="small" onclick={() => { const until = prompt('Holds until (YYYY-MM-DDTHH:MM)', localInput(new Date(Date.now() + 7 * 864e5).toISOString())); if (until) change(fact, { expires_at: until }); }}>Expire…</button>
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
  .wide {
    width: 70%;
  }
</style>
