<script lang="ts">
  import { onMount, tick } from 'svelte';
  import AccountMenu from './components/AccountMenu.svelte';
  import ConversationRail from './components/ConversationRail.svelte';
  import Dialog from './components/Dialog.svelte';
  import Icon from './components/Icon.svelte';
  import Mark from './components/Mark.svelte';
  import { api } from './lib/api';
  import { listen } from './lib/live';
  import { dismiss, go, holdToast, live, narrow, readRoute, releaseToast, route, session, toasts, ui, watchViewport } from './lib/state.svelte';
  import Login from './pages/Login.svelte';
  import Chat from './pages/Chat.svelte';
  import Memory from './pages/Memory.svelte';
  import Tasks from './pages/Tasks.svelte';
  import Schedules from './pages/Schedules.svelte';
  import Projects from './pages/Projects.svelte';
  import Workspace from './pages/Workspace.svelte';
  import Vault from './pages/Vault.svelte';
  import Connections from './pages/Connections.svelte';
  import Rules from './pages/Rules.svelte';
  import Models from './pages/Models.svelte';
  import Usage from './pages/Usage.svelte';
  import Admin from './pages/Admin.svelte';

  // The chat is a page without a link: the logo and New chat lead to it, and so does every conversation of the rail.
  const CHAT = { path: '/chat', label: 'Chat', icon: 'chat', page: Chat };
  // What you do with Clara: the links of the rail.
  const WORK = [
    { path: '/projects', label: 'Projects', icon: 'folder', page: Projects },
    { path: '/tasks', label: 'Tasks & reminders', icon: 'tasks', page: Tasks },
    { path: '/schedules', label: 'Schedules', icon: 'clock', page: Schedules },
    { path: '/workspace', label: 'Workspace', icon: 'file', page: Workspace },
    { path: '/vault', label: 'Vault', icon: 'book', page: Vault },
  ];
  // How Clara works: one "Settings" entry, these pages as its tabs.
  const SETTINGS = [
    { path: '/memory', label: 'Memory', icon: 'memory', page: Memory },
    { path: '/connections', label: 'Connections', icon: 'plug', page: Connections },
    { path: '/rules', label: 'Rules & approvals', icon: 'shield', page: Rules },
    { path: '/models', label: 'Models', icon: 'chip', page: Models },
    { path: '/usage', label: 'Usage', icon: 'bolt', page: Usage },
    { path: '/admin', label: 'Admin', icon: 'server', page: Admin },
  ];
  const PAGES = [CHAT, ...WORK, ...SETTINGS];

  let healthy = $state(true);
  let main = $state<HTMLElement>();
  let rail = $state<HTMLElement>();
  let railOpener: HTMLElement | null = null;
  const current = $derived(PAGES.find((page) => page.path === route.path) ?? CHAT);
  const inSettings = $derived(SETTINGS.includes(current));
  const alerts = $derived(Object.keys(live.alerts).length);
  // The lamp on Clara's portrait: green when connected, amber while the live stream reconnects, red when the server does not answer.
  const light = $derived(!healthy ? 'danger' : !live.connected ? 'warn' : 'ok');
  const status = $derived(!healthy ? 'Clara is not reachable' : !live.connected ? 'Reconnecting…' : 'Connected');

  async function check(): Promise<void> {
    try {
      const me = await api.get('/v1/auth/me');
      session.signedIn = true;
      session.token = me.token;
      session.approvals = me.approvals;
      listen();
    } catch {
      session.signedIn = false;
    }
    session.checked = true;
  }

  async function ping(): Promise<void> {
    try {
      healthy = (await fetch('/health', { cache: 'no-store' })).ok;
    } catch {
      healthy = false;
    }
  }

  async function signOut(): Promise<void> {
    await api.post('/v1/auth/logout').catch(() => undefined);
    session.signedIn = false;
  }

  function moved(): void {
    readRoute();
    ui.rail = false;
  }

  function keydown(event: KeyboardEvent): void {
    if (event.key === 'Escape') ui.rail = false;
  }

  function newChat(): void {
    ui.rail = false; // already on a new chat, the hash does not change: close the drawer here
    go('/chat');
  }

  onMount(() => {
    readRoute();
    addEventListener('hashchange', moved);
    const unwatch = watchViewport();
    check();
    ping();
    const timer = setInterval(ping, 20_000);
    return () => {
      removeEventListener('hashchange', moved);
      unwatch();
      clearInterval(timer);
    };
  });

  // The rail as a drawer: focus goes into it when it opens and back to what opened it when it closes.
  $effect(() => {
    if (ui.rail) {
      railOpener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      tick().then(() => rail?.querySelector<HTMLElement>('.close-rail')?.focus());
    } else if (railOpener) {
      railOpener.focus();
      railOpener = null;
    }
  });

  $effect(() => {
    if (session.signedIn && session.checked) listen();
  });

  $effect(() => {
    document.title = current.path === '/chat' ? 'Clara' : `${current.label} – Clara`;
  });
</script>

<svelte:window onkeydown={keydown} />

{#if !session.checked}
  <div class="empty">Loading…</div>
{:else if !session.signedIn}
  <Login onSignedIn={check} />
{:else}
  <button type="button" class="skip" onclick={() => main?.focus()}>Skip to content</button>
  <div class="shell">
    <aside class="rail" class:open={ui.rail} aria-label="Navigation" bind:this={rail}>
      <!-- the logo and New chat stay put at the top, the account tab at the foot; the pages and conversations scroll between them -->
      <div class="rail-top">
        <div class="rail-head">
          <a class="brand" href="#/chat"><span class="portrait"><Mark size={26} /><span class="dot {light}" aria-hidden="true"></span></span>Clara</a>
          <span class="sr-only" role="status">{status}</span>
          {#if live.server !== 'running'}<span class="state">{live.server}</span>{/if}
          <button class="ghost icon-btn close-rail" aria-label="Close navigation" onclick={() => (ui.rail = false)}><Icon name="close" /></button>
        </div>
        <button class="new-chat" onclick={newChat}><Icon name="plus" size={18} />New chat</button>
      </div>
      <div class="rail-scroll">
        <nav class="pages" aria-label="Pages">
          {#each WORK as page (page.path)}
            <a href={'#' + page.path} aria-current={page === current ? 'page' : undefined}>
              <Icon name={page.icon} size={18} />{page.label}
            </a>
          {/each}
        </nav>
        <ConversationRail current={current.path === '/chat' ? (route.params.id ?? null) : null} />
      </div>
      <!-- pinned: the approvals waiting for you, and the account tab -->
      <div class="rail-foot">
        {#if live.approvals.length}
          <a class="approvals-badge" href="#/rules"><Icon name="shield" size={18} /><span class="text">Waiting for you</span><span class="count">{live.approvals.length}</span></a>
        {/if}
        <AccountMenu name={session.token} {alerts} {inSettings} onSignOut={signOut} />
      </div>
    </aside>
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions (Escape closes it too) -->
    <div class="scrim" class:on={ui.rail} onclick={() => (ui.rail = false)} aria-hidden="true"></div>
    <main class="content" id="main" tabindex="-1" bind:this={main} inert={ui.rail && narrow.rail}>
      {#if current.path === '/chat'}
        <Chat />
      {:else}
        <header class="page-head">
          <button class="ghost icon-btn open-rail" aria-label="Open navigation" onclick={() => (ui.rail = true)}><Icon name="menu" /></button>
          <h1 class="title">{inSettings ? 'Settings' : current.label}</h1>
        </header>
        {#if inSettings}
          <nav class="settings-bar" aria-label="Settings">
            {#each SETTINGS as page (page.path)}
              <a href={'#' + page.path} aria-current={page === current ? 'page' : undefined}>
                <Icon name={page.icon} size={16} />{page.label}
                {#if page.path === '/rules' && live.approvals.length}<span class="count">{live.approvals.length}</span>{/if}
                {#if page.path === '/admin' && alerts}<span class="count danger">{alerts}</span>{/if}
              </a>
            {/each}
          </nav>
        {/if}
        <div class="scroll">
          {#key current.path}
            <current.page />
          {/key}
        </div>
      {/if}
    </main>
  </div>
{/if}

<Dialog />

<div class="toasts" aria-live="polite">
  {#each toasts.list as item (item.id)}
    <div class="toast {item.kind}" role={item.kind === 'error' ? 'alert' : 'status'}
      onmouseenter={() => holdToast(item.id)} onmouseleave={() => releaseToast(item.id)}
      onfocusin={() => holdToast(item.id)} onfocusout={() => releaseToast(item.id)}>
      {#if item.icon}<Icon name={item.icon} size={18} />{/if}<span class="text">{item.text}</span>
      <button type="button" aria-label="Dismiss" onclick={() => dismiss(item.id)}><Icon name="close" size={16} /></button>
    </div>
  {/each}
</div>
