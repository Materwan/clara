<script lang="ts">
  import { api, when } from '../lib/api';
  import { confirmDialog, promptDialog } from '../lib/dialog.svelte';
  import { conversations, go, live, toast, ui } from '../lib/state.svelte';
  import Icon from './Icon.svelte';

  // The list of conversations, in the rail under the pages: on every page, scrolling with the rest of the rail.
  // The conversations of a project or of a schedule are grouped under its name; they are listed only when that project
  // or schedule is switched on for the history (a search looks through all of them).
  type Group = { key: string; kind: 'project' | 'schedule'; name: string; items: any[] };
  const KEY = 'clara-rail-groups';
  const SHOWN = 5; // conversations a group lists before "Show more"

  let { current }: { current: string | null } = $props();
  let items = $state<any[]>([]);
  let query = $state('');
  let timer: ReturnType<typeof setTimeout> | undefined;
  let choice = $state<Record<string, boolean>>(remembered()); // group → true: opened by you, false: closed by you
  let all = $state<Record<string, boolean>>({}); // groups that listed everything ("Show more")
  const pinned = $derived(items.filter((item) => item.pinned));
  const others = $derived(items.filter((item) => !item.pinned));
  const loose = $derived(others.filter((item) => !item.group));
  const groups = $derived.by(() => {
    const found = new Map<string, Group>();
    for (const item of others) {
      if (!item.group) continue;
      const key = `${item.group.kind}:${item.group.id}`;
      const group: Group = found.get(key) ?? { key, kind: item.group.kind, name: item.group.name, items: [] };
      group.items.push(item);
      found.set(key, group);
    }
    return [...found.values()];
  });
  const projects = $derived(groups.filter((group) => group.kind === 'project'));
  const schedules = $derived(groups.filter((group) => group.kind === 'schedule'));

  function remembered(): Record<string, boolean> {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) ?? '{}');
      return Object.fromEntries(Object.entries(saved).filter(([, value]) => typeof value === 'boolean')) as Record<string, boolean>;
    } catch {
      return {};
    }
  }

  /** A group is open when you opened it, or while a search runs, or by default when it holds the open conversation. */
  const isOpen = (group: Group): boolean => !!query || (choice[group.key] ?? group.items.some((item) => item.id === current));

  function toggle(group: Group): void {
    choice[group.key] = !isOpen(group);
    try {
      localStorage.setItem(KEY, JSON.stringify(choice));
    } catch {
      /* not remembered: the page keeps it until reloaded */
    }
  }

  /** The first conversations of a group (and the open one, wherever it is) unless it was asked for whole. */
  const listed = (group: Group): any[] =>
    query || all[group.key] ? group.items : group.items.filter((item, index) => index < SHOWN || item.id === current);

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
      items = (await api.get('/v1/conversations', { q: query, kind: 'any', history: !query, limit: 200 })).conversations;
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

{#snippet row(item: any, nested = false)}
  <li class="convo" class:nested class:active={item.id === current}>
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

{#snippet section(group: Group)}
  {@const open = isOpen(group)}
  <li class="group-head">
    <button type="button" class="ghost" aria-expanded={open} title={group.name} onclick={() => toggle(group)}>
      <Icon name="chevron" size={14} /><Icon name={group.kind === 'project' ? 'folder' : 'clock'} size={14} />
      <span class="name">{group.name}</span><span class="n">{group.items.length}</span>
    </button>
  </li>
  {#if open}
    {#each listed(group) as item (item.id)}{@render row(item, true)}{/each}
    {#if listed(group).length < group.items.length}
      <li class="more"><button type="button" class="ghost small" onclick={() => (all[group.key] = true)}>Show {group.items.length - listed(group).length} more</button></li>
    {/if}
  {/if}
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
    {#if projects.length}
      <li class="group-title">Projects</li>
      {#each projects as group (group.key)}{@render section(group)}{/each}
    {/if}
    {#if schedules.length}
      <li class="group-title">Schedules</li>
      {#each schedules as group (group.key)}{@render section(group)}{/each}
    {/if}
    {#if loose.length}
      <li class="group-title">Conversations</li>
      {#each loose as item (item.id)}{@render row(item)}{/each}
    {/if}
    {#if !items.length}
      <li class="empty small">{query ? 'No conversation matches.' : 'No conversation yet. Write to Clara to start one.'}</li>
    {/if}
  </ul>
</section>
