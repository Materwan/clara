<script lang="ts">
  import { onMount, tick, type Snippet } from 'svelte';
  import Icon from './Icon.svelte';

  // A form told in steps: the bar of steps, the title and lead of the open one, its fields (the `children` snippet gets the step's id)
  // and a bar at the foot that stays in view: Cancel, Back, Next, and the save on the last step (on every step when an existing item
  // is being changed, which has nothing left to discover). Going back is always free; going on is not: a step that holds something
  // missing (`missing`, the parent's words for it) keeps you on it, whether you press Next, Enter or a later step in the bar, and the
  // parent hears it (`onblocked`) to show the error and take you to the field. The bar says in a line what is missing; `problems`
  // is how many marks the step shows, which the parent holds back until the step was tried.
  type Step = { id: string; label: string; title: string; lead: string; problems?: number; missing?: string[] };
  let { steps, step = $bindable(), label, id, editing = false, busy = false, submitLabel, onsubmit, oncancel, onblocked, children }: {
    steps: Step[]; step: string; label: string; id: string; editing?: boolean; busy?: boolean; submitLabel: string;
    onsubmit: () => void; oncancel: () => void; onblocked?: (step: string) => void; children: Snippet<[string]>;
  } = $props();

  const index = $derived(Math.max(0, steps.findIndex((item) => item.id === step)));
  const current = $derived(steps[index]);
  const last = $derived(index === steps.length - 1);
  const missing = $derived(current.missing ?? []);
  // “To go on: give the hook a name.”, and how many more when there are several
  const why = $derived(
    missing.length
      ? `To go on: ${missing[0].charAt(0).toLowerCase()}${missing[0].slice(1)}${missing.length > 1 ? ` (and ${missing.length - 1} more)` : ''}`
      : '',
  );
  let heading = $state<HTMLHeadingElement>();

  // Open the step wanted, or the first step on the way that is not complete; the title takes the focus so a screen reader hears where it
  // is (the fields follow with Tab), unless the step is blocked, where the parent puts the focus on the field that is missing.
  async function go(position: number): Promise<void> {
    const wanted = Math.min(Math.max(position, 0), steps.length - 1);
    const stop = wanted > index ? steps.findIndex((item, at) => at >= index && at < wanted && (item.missing ?? []).length > 0) : -1;
    const target = stop === -1 ? wanted : stop;
    step = steps[target].id;
    await tick();
    if (stop === -1) heading?.focus();
    else onblocked?.(steps[target].id);
  }

  onMount(() => heading?.focus());

  function submit(event: SubmitEvent): void {
    event.preventDefault();
    if (!busy) onsubmit();
  }

  // Enter in a field goes on to the next step while there is one (a new item is not saved half told); a search field never submits.
  function enter(event: KeyboardEvent): void {
    const target = event.target as HTMLInputElement;
    if (event.key !== 'Enter' || target.tagName !== 'INPUT' || ['checkbox', 'radio', 'button', 'submit'].includes(target.type)) return;
    if (target.type === 'search') event.preventDefault();
    else if (!editing && !last) {
      event.preventDefault();
      go(index + 1);
    }
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions (the form hears the Enter that its own fields send up) -->
<form class="panel wizard" {id} aria-label={label} onsubmit={submit} onkeydown={enter}>
  <ol class="steps" aria-label="Steps">
    {#each steps as item, position (item.id)}
      <li>
        <button type="button" class="step" aria-current={item.id === step ? 'step' : undefined} onclick={() => go(position)}>
          <span class="step-num">{position + 1}</span><span class="step-label">{item.label}</span>
          {#if item.problems}<span class="danger"><Icon name="warn" size={14} /></span><span class="sr-only">{item.problems} to fix</span>{/if}
        </button>
      </li>
    {/each}
  </ol>
  <div class="step-head">
    <h3 tabindex="-1" bind:this={heading}>{current.title}</h3>
    <p class="muted">{current.lead}</p>
  </div>
  <div class="step-body stack">{@render children(step)}</div>
  <div class="wizard-bar">
    <button type="button" class="ghost" onclick={oncancel}>Cancel</button>
    <span class="wizard-why grow" id={`${id}-why`}>{why}</span>
    {#if index > 0}<button type="button" onclick={() => go(index - 1)}>Back</button>{/if}
    {#if !last}
      <button type="button" class:primary={!editing} aria-disabled={missing.length ? 'true' : undefined}
        aria-describedby={missing.length ? `${id}-why` : undefined} onclick={() => go(index + 1)}>Next</button>
    {/if}
    {#if last || editing}<button class="primary" aria-busy={busy}>{submitLabel}</button>{/if}
  </div>
</form>
