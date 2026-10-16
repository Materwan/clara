<script lang="ts">
  import { api, when } from '../lib/api';
  import { confirmDialog, promptDialog } from '../lib/dialog.svelte';
  import { conversations, go, live, toast, ui } from '../lib/state.svelte';
  import Icon from './Icon.svelte';

  // The list of conversations, in the rail under the pages: on every page, scrolling with the rest of the rail.
  let { current }: { current: string | null } = $props();
  let items = $state<any[]>([]);
  let query = $state('');
  let timer: ReturnType<typeof setTimeout> | undefined;
  const pinned = $derived(items.filter((item) => item.pinned));
  const others = $derived(items.filter((item) => !item.pinned));

  /** "14:05" today, "12 Oct" this year, "12 Oct 2025" before; the full date is in the title. */
  function short(iso: string): string {
    const date = new Date(iso);
    const now = new Date();
    if (date.toDateString() === now.toDateString()) return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    const year = date.getFullYear() === now.getFullYear() ? undefined : 'numeric';
    return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year });
  }

  async function load(): Promise<void> {
    try {
      items = (await api.get('/v1/conversations', { q: query, limit: 200 })).conversations;
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  $effect(() => {
    void conversations.changed;
    void live.refresh;
    load();
  });

  function search(): void {
    clearTimeout(timer);
    timer = setTimeout(load, 250);
  }

  async function rename(item: any): Promise<void> {
    const title = await promptDialog({ title: 'Rename the conversation', message: 'Title', label: 'Title', value: item.title || item.preview?.slice(0, 60) || '' });
    if (title === null) return;
    await api.patch(`/v1/conversations/${item.id}`, { title });
    load();
  }

  async function pin(item: any): Promise<void> {
    await api.patch(`/v1/conversations/${item.id}`, { pinned: !item.pinned });
    load();
  }

  async function remove(item: any): Promise<void> {
    if (!(await confirmDialog({ title: 'Delete the conversation', message: 'Delete this conversation? Its facts stay.', confirm: 'Delete', danger: true }))) return;
    try {
      await api.del(`/v1/conversations/${item.id}`);
      if (item.id === current) go('/chat');
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }
</script>

{#snippet row(item: any)}
  <li class="convo" class:active={item.id === current}>
    <!-- a click on the open conversation changes no hash, so it closes the drawer itself -->
    <a href={'#/chat/' + item.id} aria-current={item.id === current ? 'page' : undefined} title={`Last message ${when(item.updated_at)}`}
      onclick={() => (ui.rail = false)}>
      <span class="title">
        {#if item.incognito}<Icon name="incognito" size={14} label="Incognito" />{/if}
        {#if item.local_only}<Icon name="home" size={14} label="Local only" />{/if}
        <span>{item.title || item.preview || 'New conversation'}</span>
      </span>
      <span class="meta">{item.client} · {short(item.updated_at)}</span>
    </a>
    <span class="actions">
      <button class="ghost icon-btn" aria-pressed={!!item.pinned} title={item.pinned ? 'Unpin' : 'Pin'} aria-label={item.pinned ? 'Unpin' : 'Pin'}
        onclick={() => pin(item)}><Icon name="pin" size={16} /></button>
      <button class="ghost icon-btn" title="Rename" aria-label="Rename" onclick={() => rename(item)}><Icon name="edit" size={16} /></button>
      <button class="ghost icon-btn" title="Delete" aria-label="Delete" onclick={() => remove(item)}><Icon name="trash" size={16} /></button>
    </span>
  </li>
{/snippet}

<section class="convos" aria-label="Conversations">
  <label class="search-field">
    <span class="sr-only">Search conversations</span>
    <Icon name="search" size={16} />
    <input type="search" placeholder="Search conversations" bind:value={query} oninput={search} />
  </label>
  <ul class="convo-list">
    {#if pinned.length}
      <li class="group-title">Pinned</li>
      {#each pinned as item (item.id)}{@render row(item)}{/each}
    {/if}
    {#if others.length}
      <li class="group-title">Conversations</li>
      {#each others as item (item.id)}{@render row(item)}{/each}
    {/if}
    {#if !items.length}
      <li class="empty small">{query ? 'No conversation matches.' : 'No conversation yet. Write to Clara to start one.'}</li>
    {/if}
  </ul>
</section>
