<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from './lib/api';
  import { listen } from './lib/live';
  import { live, readRoute, route, session, toasts } from './lib/state.svelte';
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

  const PAGES = [
    { path: '/chat', label: 'Chat', page: Chat },
    { path: '/memory', label: 'Memory', page: Memory },
    { path: '/tasks', label: 'Tasks & reminders', page: Tasks },
    { path: '/schedules', label: 'Schedules', page: Schedules },
    { path: '/projects', label: 'Projects', page: Projects },
    { path: '/workspace', label: 'Workspace', page: Workspace },
    { path: '/vault', label: 'Vault', page: Vault },
    { path: '/connections', label: 'Connections', page: Connections },
    { path: '/rules', label: 'Rules & approvals', page: Rules },
    { path: '/models', label: 'Models', page: Models },
    { path: '/usage', label: 'Usage', page: Usage },
    { path: '/admin', label: 'Admin', page: Admin },
  ];

  let menu = $state(false);
  let healthy = $state(true);
  const current = $derived(PAGES.find((page) => page.path === route.path) ?? PAGES[0]);

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
      healthy = (await fetch('/health')).ok;
    } catch {
      healthy = false;
    }
  }

  function theme(): void {
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    root.dataset.theme = dark ? 'light' : 'dark';
    try {
      localStorage.setItem('clara-theme', root.dataset.theme);
    } catch {
      /* not remembered */
    }
  }

  async function signOut(): Promise<void> {
    await api.post('/v1/auth/logout').catch(() => undefined);
    session.signedIn = false;
  }

  onMount(() => {
    readRoute();
    addEventListener('hashchange', readRoute);
    check();
    ping();
    const timer = setInterval(ping, 20_000);
    return () => {
      removeEventListener('hashchange', readRoute);
      clearInterval(timer);
    };
  });

  $effect(() => {
    if (session.signedIn && session.checked) listen();
  });
</script>

{#if !session.checked}
  <div class="empty">Loading…</div>
{:else if !session.signedIn}
  <Login onSignedIn={check} />
{:else}
  <div class="shell">
    <nav class:open={menu}>
      <div class="brand row">
        <span class="dot" class:down={!healthy || !live.connected} title={healthy ? (live.connected ? 'Connected' : 'Reconnecting…') : 'Clara is not reachable'}></span>
        <strong>Clara</strong>
        {#if live.server !== 'running'}<span class="tag warn">{live.server}</span>{/if}
      </div>
      {#each PAGES as page (page.path)}
        <a href={'#' + page.path} class:active={page.path === current.path} onclick={() => (menu = false)}>
          {page.label}
          {#if page.path === '/rules' && live.approvals.length}<span class="badge">{live.approvals.length}</span>{/if}
          {#if page.path === '/admin' && Object.keys(live.alerts).length}<span class="badge alert">{Object.keys(live.alerts).length}</span>{/if}
        </a>
      {/each}
      <div class="foot row small">
        <button class="small" onclick={theme}>Theme</button>
        <button class="small" onclick={signOut}>Sign out</button>
      </div>
    </nav>
    <main>
      <button class="burger small" onclick={() => (menu = !menu)}>☰</button>
      {#key current.path}
        <current.page />
      {/key}
    </main>
  </div>
{/if}

<div class="toasts">
  {#each toasts.list as item (item.id)}
    <div class="toast {item.kind}">{item.text}</div>
  {/each}
</div>

<style>
  .shell {
    display: grid;
    grid-template-columns: 210px 1fr;
    min-height: 100vh;
  }
  nav {
    background: var(--panel);
    border-right: 1px solid var(--line);
    display: flex;
    flex-direction: column;
    padding: 0.8rem 0.5rem;
    gap: 0.1rem;
    position: sticky;
    top: 0;
    height: 100vh;
  }
  nav a {
    color: var(--text);
    text-decoration: none;
    padding: 0.4rem 0.7rem;
    border-radius: 8px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  nav a.active {
    background: var(--accent-soft);
    color: var(--accent);
  }
  .brand {
    padding: 0.2rem 0.7rem 0.8rem;
  }
  .dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--ok);
  }
  .dot.down {
    background: var(--bad);
  }
  .foot {
    margin-top: auto;
    padding: 0.5rem 0.7rem;
  }
  .badge.alert {
    background: var(--bad);
  }
  main {
    min-width: 0;
  }
  .burger {
    display: none;
  }
  .toasts {
    position: fixed;
    right: 1rem;
    bottom: 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    z-index: 50;
    max-width: min(420px, 90vw);
  }
  .toast {
    background: var(--panel);
    border: 1px solid var(--line);
    border-left: 4px solid var(--accent);
    border-radius: 8px;
    padding: 0.6rem 0.8rem;
    box-shadow: var(--shadow);
    white-space: pre-wrap;
  }
  .toast.error {
    border-left-color: var(--bad);
  }
  .toast.ok {
    border-left-color: var(--ok);
  }
  @media (max-width: 760px) {
    .shell {
      grid-template-columns: 1fr;
    }
    nav {
      display: none;
      position: fixed;
      z-index: 40;
      width: 230px;
      box-shadow: var(--shadow);
    }
    nav.open {
      display: flex;
    }
    .burger {
      display: inline-block;
      margin: 0.5rem;
    }
  }
</style>
