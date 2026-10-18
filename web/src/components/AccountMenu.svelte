<script lang="ts">
  import { ui } from '../lib/state.svelte';
  import Icon from './Icon.svelte';
  import ThemeSwitch from './ThemeSwitch.svelte';

  // The account tab, pinned at the foot of the rail: who you are signed in as (the token's name). It opens upward onto
  // Settings, the colour theme and Sign out. A disclosure (aria-expanded), not a menu role: it holds a link, a switch and a button.
  let { name, alerts, inSettings, onSignOut }: { name: string; alerts: number; inSettings: boolean; onSignOut: () => void } = $props();
  let open = $state(false);
  let root = $state<HTMLElement>();
  let tab = $state<HTMLButtonElement>();
  const alertTitle = $derived(`${alerts} active alert${alerts > 1 ? 's' : ''}`);

  function close(): void {
    open = false;
  }

  // Escape closes the panel first, wherever the focus is (a click does not focus a button in Safari); it is heard in the capture
  // phase, so the rail's own Escape (the drawer) hears it only when the panel is shut.
  function keydown(event: KeyboardEvent): void {
    if (!open || event.key !== 'Escape') return;
    event.stopPropagation();
    open = false;
    tab?.focus();
  }

  function outside(event: Event): void {
    if (open && !root?.contains(event.target as Node)) open = false;
  }

  function settings(): void {
    open = false;
    ui.rail = false; // already on a settings page, the hash does not change: close the drawer here
  }

  // The drawer closing takes the panel with it (on a wide screen the rail never closes, so this never runs).
  $effect(() => {
    if (!ui.rail) open = false;
  });
</script>

<svelte:window onkeydowncapture={keydown} onpointerdown={outside} onfocusin={outside} />

<div class="account" bind:this={root}>
  <button type="button" class="account-tab" aria-expanded={open} aria-controls="account-menu" bind:this={tab} onclick={() => (open = !open)}>
    <span class="avatar"><Icon name="user" size={16} /></span>
    <span class="name">{name || 'Account'}</span>
    {#if alerts}<span class="count danger" title={alertTitle}>{alerts}<span class="sr-only"> active alerts</span></span>{/if}
    <Icon name="up" size={16} />
  </button>
  {#if open}
    <div class="account-menu frame" id="account-menu" role="group" aria-label="Account">
      <a class="account-item" href="#/memory" aria-current={inSettings ? 'page' : undefined} onclick={settings}>
        <Icon name="admin" size={18} />Settings
        {#if alerts}<span class="count danger" title={alertTitle}>{alerts}</span>{/if}
      </a>
      <div class="account-theme"><ThemeSwitch /></div>
      <button type="button" class="account-item" onclick={() => { close(); onSignOut(); }}><Icon name="logout" size={18} />Sign out</button>
    </div>
  {/if}
</div>
