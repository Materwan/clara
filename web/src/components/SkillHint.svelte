<script lang="ts">
  import type { Chip } from '../lib/skills';

  // The line under the box once the first word is a skill: what it takes after its name, and the special tools it needs. A tool that is
  // on is green; one that is off is ochre, and a click switches it on for this conversation (the server never does it by itself); one
  // that cannot be used says why. Plain text only: nothing here is ever drawn as HTML.
  let { hint, chips, onOn }: { hint: string; chips: Chip[]; onOn: (id: string) => void } = $props();
</script>

<div class="skill-hint" role="status">
  {#if hint}<span class="args">Arguments: <code>{hint}</code></span>{/if}
  {#each chips as chip (chip.id)}
    {#if chip.on}
      <span class="tag ok">{chip.label} on</span>
    {:else if chip.available}
      <button type="button" class="tag warn" title={`Switch ${chip.label} on for this conversation`} onclick={() => onOn(chip.id)}>{chip.label} is off: switch it on</button>
    {:else}
      <span class="tag warn" title={chip.reason}>{chip.label} is off{chip.reason ? `: ${chip.reason}` : ''}</span>
    {/if}
  {/each}
</div>

<style>
  .skill-hint { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-6) var(--sp-10); margin: var(--sp-8) var(--sp-4) 0; font-size: var(--fs-xs); color: var(--muted); min-width: 0; }
  .args { overflow-wrap: anywhere; min-width: 0; }
  .tag { white-space: normal; min-height: 24px; overflow-wrap: anywhere; }
  button.tag { border: 1px solid var(--warn); cursor: pointer; font-weight: 650; padding: var(--sp-2) var(--sp-8); }
  button.tag:hover { background: var(--warn-soft); border-color: var(--warn); }
</style>
