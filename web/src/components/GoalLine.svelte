<script lang="ts">
  import { type Goal, choices, counts } from '../lib/goal';
  import Icon from './Icon.svelte';

  // The one line above the box you write in when the conversation has a goal (`/goal`): its state and counters, the buttons that fit the state, and
  // the condition on a second line you can unfold. The label is a polite live region, so a pause or a verdict is announced; the dot pulses only while
  // the goal works (and not at all under prefers-reduced-motion, which app.css applies to every animation).
  let { goal, pending, onStop, onCommand }: {
    goal: Goal | null; pending: boolean; onStop: () => void; onCommand: (text: string) => void;
  } = $props();

  let open = $state(false); // the condition shown in full
  const may = $derived(goal ? choices(goal) : { resume: false, turns: false, minutes: false });
  const label = $derived.by(() => {
    if (!goal) return '';
    const numbers = counts(goal);
    if (goal.state === 'active') return `Goal · ${numbers}`;
    if (goal.state === 'paused') return `Goal paused${goal.reason ? ` (${goal.reason})` : ''} · ${numbers}`;
    return `Goal ${goal.state === 'met' ? 'met' : 'impossible'}${goal.reason ? `: ${goal.reason}` : ''} · ${numbers}`;
  });
</script>

<div class="goal-slot">
  {#if goal}
    <div class="goal {goal.state}">
      <div class="goal-main">
        {#if goal.state === 'active'}<span class="pulse" aria-hidden="true"></span>
        {:else}<Icon name={goal.state === 'paused' ? 'pause' : goal.state === 'met' ? 'check' : 'warn'} size={16} />{/if}
        <span class="goal-label" role="status" aria-live="polite">{label}</span>
        <span class="goal-actions">
          {#if goal.state === 'active'}
            <button type="button" class="small" onclick={onStop}><Icon name="stop" size={14} /> Stop</button>
          {/if}
          {#if may.resume}<button type="button" class="small" disabled={pending} onclick={() => onCommand('resume')}><Icon name="play" size={14} /> Resume</button>{/if}
          {#if may.turns}<button type="button" class="small" disabled={pending} onclick={() => onCommand('--turns 5')}>+5 turns</button>{/if}
          {#if may.minutes}<button type="button" class="small" disabled={pending} onclick={() => onCommand('--minutes 10')}>+10 min</button>{/if}
          {#if goal.state === 'met' || goal.state === 'impossible'}
            <button type="button" class="small" disabled={pending} onclick={() => onCommand('clear')}>Clear</button>
          {/if}
        </span>
      </div>
      <button type="button" class="condition" class:open aria-expanded={open} title={open ? 'Fold the condition' : goal.condition} onclick={() => (open = !open)}>
        <span class="word">Condition</span><span class="text">{goal.condition}</span>
      </button>
    </div>
  {/if}
</div>

<style>
  .goal { margin: 0 0 var(--sp-8); padding: var(--sp-6) var(--sp-12); border: 1px solid var(--line-strong); border-radius: var(--r-md); background: var(--panel); color: var(--text); font-size: var(--fs-sm); min-width: 0; }
  .goal.paused { background: var(--warn-soft); color: var(--warn); border-color: var(--warn); }
  .goal.met { background: var(--ok-soft); color: var(--ok); border-color: var(--ok); }
  .goal.impossible { background: var(--danger-soft); color: var(--danger); border-color: var(--danger); }
  .goal-main { display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-4) var(--sp-10); min-width: 0; }
  .goal-label { flex: 1 1 14rem; min-width: 0; font-weight: 650; overflow-wrap: anywhere; }
  .goal-actions { display: flex; flex-wrap: wrap; gap: var(--sp-6); margin-left: auto; }
  .goal-actions button { color: inherit; border-color: currentColor; background: transparent; }
  .goal-actions button:hover:not(:disabled) { background: color-mix(in srgb, currentColor 12%, transparent); }
  .pulse { flex: none; width: .5rem; height: .5rem; border-radius: 50%; background: var(--accent-line); animation: goal-pulse 1.4s ease-in-out infinite; }
  @keyframes goal-pulse { 0%, 100% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--accent-line) 60%, transparent); } 50% { box-shadow: 0 0 0 5px transparent; } }
  .condition { display: flex; justify-content: flex-start; gap: var(--sp-8); align-items: baseline; width: 100%; margin-top: var(--sp-2); padding: var(--sp-2) 0; border: none; background: none; color: inherit; font: inherit; font-weight: 400; text-align: left; min-width: 0; cursor: pointer; }
  .condition:hover:not(:disabled) { background: none; }
  .condition .word { flex: none; font-weight: 650; }
  .condition .text { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .condition.open .text { white-space: pre-wrap; overflow-wrap: anywhere; }
  .condition:focus-visible { outline: 2px solid var(--text); outline-offset: 2px; }
  @media (pointer: coarse) { .goal-actions button, .condition { min-height: 36px; } }
</style>
