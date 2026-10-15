<script lang="ts">
  import { api, when } from '../lib/api';
  import { go, live, toast } from '../lib/state.svelte';

  let { current, refresh }: { current: string | null; refresh: number } = $props();
  let items = $state<any[]>([]);
  let query = $state('');
  let timer: ReturnType<typeof setTimeout> | undefined;

  async function load(): Promise<void> {
    try {
      items = (await api.get('/v1/conversations', { q: query, limit: 200 })).conversations;
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  $effect(() => {
    void refresh;
    void live.refresh;
    load();
  });

  function search(): void {
    clearTimeout(timer);
    timer = setTimeout(load, 250);
  }

  async function rename(item: any): Promise<void> {
    const title = prompt('Title', item.title || item.preview?.slice(0, 60) || '');
    if (title === null) return;
    await api.patch(`/v1/conversations/${item.id}`, { title });
    load();
  }

  async function pin(item: any): Promise<void> {
    await api.patch(`/v1/conversations/${item.id}`, { pinned: !item.pinned });
    load();
  }

  async function remove(item: any): Promise<void> {
    if (!confirm('Delete this conversation? Its facts stay.')) return;
    try {
      await api.del(`/v1/conversations/${item.id}`);
      if (item.id === current) go('/chat');
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }
</script>

<aside>
  <input placeholder="Search conversations" bind:value={query} oninput={search} />
  <button class="primary" onclick={() => go('/chat')}>New conversation</button>
  <ul>
    {#each items as item (item.id)}
      <li class:active={item.id === current}>
        <a href={'#/chat/' + item.id}>
          <span class="title">{item.pinned ? '📌 ' : ''}{item.incognito ? '🕶 ' : ''}{item.local_only ? '🏠 ' : ''}{item.title || item.preview || 'New conversation'}</span>
          <span class="muted small">{item.client} · {when(item.updated_at)}</span>
        </a>
        <span class="actions">
          <button class="small" title="Pin" onclick={() => pin(item)}>📌</button>
          <button class="small" title="Rename" onclick={() => rename(item)}>✎</button>
          <button class="small" title="Delete" onclick={() => remove(item)}>🗑</button>
        </span>
      </li>
    {:else}
      <li class="empty">No conversation yet.</li>
    {/each}
  </ul>
</aside>

<style>
  aside {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    padding: 0.8rem;
    border-right: 1px solid var(--line);
    height: 100vh;
    overflow: auto;
    background: var(--panel);
  }
  ul {
    list-style: none;
    padding: 0;
    margin: 0;
  }
  li {
    display: flex;
    align-items: center;
    border-radius: 8px;
  }
  li.active {
    background: var(--accent-soft);
  }
  li a {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    padding: 0.4rem 0.5rem;
    color: var(--text);
    text-decoration: none;
  }
  .title {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .actions {
    display: none;
  }
  li:hover .actions {
    display: flex;
  }
  .actions button {
    border: none;
    background: none;
    padding: 0.1rem 0.25rem;
  }
</style>
