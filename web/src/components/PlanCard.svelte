<script lang="ts">
  import { OUTSIDE, type Plan, summary, progress, statusWord } from '../lib/plan';
  import Icon from './Icon.svelte';

  // Clara's plan for this conversation, read only: the owner steers it by writing a message (there is no button here on purpose). Each status has
  // its own shape (ring, half disc, tick, cross) and its word for a screen reader, so colour is never the only sign. A dropped step is struck through.
  let { plan }: { plan: Plan } = $props();
  const finished = $derived(progress(plan).finished);
</script>

<section class="plan" class:plan-finished={finished} aria-labelledby="plan-title">
  <h3 id="plan-title" class="plan-title">{plan.title}</h3>
  <p class="plan-progress" role="status">
    {#if finished}<Icon name="check" size={14} />{/if}{summary(plan)}
  </p>
  <ol class="plan-steps">
    {#each plan.steps as step (step.n)}
      <li class="plan-step plan-{step.status}">
        <span class="plan-mark" aria-hidden="true">
          {#if step.status === 'doing'}<Icon name="auto" size={18} />
          {:else if step.status === 'done'}<Icon name="check" size={14} />
          {:else if step.status === 'dropped'}<Icon name="close" size={14} />{/if}
        </span>
        <span class="plan-body">
          <span class="sr-only">{statusWord(step.status)}: </span>
          <span class="plan-text">{step.text}</span>
          {#if step.note}<span class="plan-note">{step.note}</span>{/if}
          {#if step.outside}<span class="tag" title="Clara wrote this step after reading content from outside (a mail, a page, a file).">{OUTSIDE}</span>{/if}
        </span>
      </li>
    {/each}
  </ol>
</section>

<style>
  .plan { display: flex; flex-direction: column; gap: var(--sp-6); padding: var(--sp-10) var(--sp-12); border: 1px solid var(--line-strong); border-radius: var(--r-md); background: var(--raised); min-width: 0; }
  .plan.plan-finished { border-color: var(--ok); }
  .plan-title { margin: 0; font-size: var(--fs-lg); font-weight: 650; overflow-wrap: anywhere; }
  .plan-progress { display: flex; align-items: center; gap: var(--sp-4); margin: 0; font-size: var(--fs-sm); color: var(--muted); }
  .plan-finished .plan-progress { color: var(--ok); font-weight: 650; }
  .plan-steps { list-style: none; margin: var(--sp-4) 0 0; padding: 0; display: flex; flex-direction: column; gap: var(--sp-8); }
  .plan-step { position: relative; display: flex; gap: var(--sp-8); align-items: flex-start; min-width: 0; font-size: var(--fs-md); line-height: 1.4; }
  .plan-mark { flex: none; display: grid; place-items: center; width: 1.125rem; height: 1.125rem; margin-top: 1px; border-radius: 50%; }
  .plan-todo .plan-mark { border: 2px solid var(--line-strong); }
  .plan-doing .plan-mark { color: var(--accent-text); }
  .plan-done .plan-mark { background: var(--ok); color: var(--bg); }
  .plan-dropped .plan-mark { color: var(--muted); }
  .plan-body { flex: 1; min-width: 0; display: flex; flex-direction: column; align-items: flex-start; gap: var(--sp-2); overflow-wrap: anywhere; }
  .plan-doing .plan-text { font-weight: 650; }
  .plan-done .plan-text { color: var(--text-2); }
  .plan-dropped .plan-text { color: var(--muted); text-decoration: line-through; }
  .plan-note { font-size: var(--fs-sm); color: var(--muted); white-space: pre-wrap; }
</style>
