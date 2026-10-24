<script lang="ts">
  import { onMount, tick, untrack } from 'svelte';
  import { ApiError, api } from '../lib/api';
  import { toast } from '../lib/state.svelte';
  import Checkbox from './Checkbox.svelte';
  import Icon from './Icon.svelte';

  type Option = { label: string; description: string };
  type Ask = { id: string; conversation: string; questions: { question: string; header: string; multi_select: boolean; options: Option[] }[] };

  // The questions Clara asks while she works (`ask_questions`), in the place of the box you write in: one after the other,
  // each with its options and a last line, "Other", where you write anything. A review step comes before Submit. Clara's turn
  // waits for the answers; dismissing them (Escape, the cross) tells her you declined.
  let { ask, onDone }: { ask: Ask; onDone: () => void } = $props();

  // A set of questions is answered once (the parent keys it on its id): what it starts from is read once.
  const questions = untrack(() => ask.questions);
  const count = questions.length;
  const review = count > 1; // a single question is submitted from its own step
  const last = review ? count : count - 1;
  let step = $state(0);
  let busy = $state(false);
  let picked = $state<number[][]>(questions.map(() => []));
  let other = $state<string[]>(questions.map(() => ''));
  let otherOn = $state<boolean[]>(questions.map(() => false));
  let root = $state<HTMLElement>();
  const group = `ask-${untrack(() => ask.id)}`;

  const question = $derived(step < count ? ask.questions[step] : null);
  const answered = (q: number): boolean => picked[q].length > 0 || (otherOn[q] && other[q].trim().length > 0);
  const complete = $derived(ask.questions.every((_, q) => answered(q)));

  async function focusFirst(): Promise<void> {
    await tick();
    root?.querySelector<HTMLElement>('input:checked, input:not([type="text"]):not(:disabled)')?.focus();
  }

  onMount(() => {
    focusFirst();
  });

  function pick(q: number, option: number): void {
    const single = !ask.questions[q].multi_select;
    if (single) {
      picked[q] = [option];
      otherOn[q] = false;
      if (review && q === step) {
        step += 1; // one answer ends a single question: on to the next
        focusFirst();
      }
    } else {
      picked[q] = picked[q].includes(option) ? picked[q].filter((n) => n !== option) : [...picked[q], option].sort((a, b) => a - b);
    }
  }

  function chooseOther(q: number): void {
    if (!ask.questions[q].multi_select) picked[q] = [];
    otherOn[q] = !ask.questions[q].multi_select ? true : !otherOn[q];
  }

  function typing(q: number, value: string): void {
    other[q] = value;
    if (value && !otherOn[q]) chooseOther(q);
  }

  function go(to: number): void {
    step = Math.max(0, Math.min(last, to));
    focusFirst();
  }

  async function reply(body: object): Promise<void> {
    busy = true;
    try {
      await api.post(`/v1/conversations/${ask.conversation}/questions/${ask.id}`, body);
    } catch (error) {
      // Clara no longer waits (answered elsewhere, timed out, stopped): say so and let the box come back
      if (!(error instanceof ApiError && error.status === 409)) {
        toast((error as Error).message, 'error');
        busy = false;
        return;
      }
      toast('Clara is not waiting for these answers any more.', 'info');
    }
    onDone();
  }

  const submit = (): Promise<void> =>
    reply({ answers: ask.questions.map((_, q) => ({ options: picked[q], other: otherOn[q] ? other[q].trim() : '' })) });
  const dismiss = (): Promise<void> => reply({ declined: true });

  function advance(): void {
    if (step < last && step < count && answered(step)) go(step + 1);
    else if (!review && complete) submit();
  }

  function keydown(event: KeyboardEvent): void {
    if (event.target instanceof Element && event.target.closest('.side-panel')) return; // the panel beside has its own keys
    if (event.key === 'Escape') {
      event.preventDefault();
      dismiss();
      return;
    }
    const typingInField = event.target instanceof HTMLInputElement && event.target.type === 'text';
    if (event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement) return;
    if (event.key === 'Enter' && !(event.target instanceof HTMLButtonElement || event.target instanceof HTMLAnchorElement)) {
      event.preventDefault();
      if (step === count) submit();
      else advance();
      return;
    }
    if (typingInField || event.ctrlKey || event.metaKey || event.altKey || !question || !/^[1-9]$/.test(event.key)) return;
    const number = Number(event.key) - 1;
    if (number < question.options.length) {
      event.preventDefault();
      pick(step, number);
    } else if (number === question.options.length) {
      event.preventDefault();
      chooseOther(step);
      tick().then(() => root?.querySelector<HTMLInputElement>('.other-input')?.focus());
    }
  }

  /** What was chosen, in words: for the review. */
  function chosen(q: number): string {
    const parts = picked[q].map((n) => ask.questions[q].options[n].label);
    if (otherOn[q] && other[q].trim()) parts.push(`Other: ${other[q].trim()}`);
    return parts.join('; ') || 'No answer';
  }
</script>

<svelte:window onkeydown={keydown} />

<div class="ask" role="group" aria-label="Clara asks" bind:this={root}>
  <div class="ask-head">
    {#if question}<span class="tag">{question.header || `Question ${step + 1}`}</span>{:else}<span class="tag">Review</span>{/if}
    <span class="ask-step muted">{question ? (count > 1 ? `${step + 1} of ${count}` : '') : 'Check your answers'}</span>
    <button class="ghost icon-btn ask-dismiss" aria-label="Dismiss the questions (Escape)" title="Dismiss (Escape)" onclick={dismiss} disabled={busy}>
      <Icon name="close" size={18} />
    </button>
  </div>

  {#if question}
    <fieldset class="ask-q" disabled={busy}>
      <legend>{question.question}</legend>
      {#if question.multi_select}<p class="muted small">Choose any number.</p>{/if}
      {#each question.options as option, o (o)}
        {@const on = picked[step].includes(o)}
        <label class="opt" class:chosen={on}>
          {#if question.multi_select}<Checkbox checked={on} onchange={() => pick(step, o)} />
          {:else}<input type="radio" name={`${group}-${step}`} checked={on} onchange={() => pick(step, o)} />{/if}
          <span class="letter">{o + 1}</span>
          <span class="opt-body"><span class="opt-text">{option.label}</span>{#if option.description}<span class="opt-desc">{option.description}</span>{/if}</span>
        </label>
      {/each}
      <div class="opt other" class:chosen={otherOn[step]}>
        {#if question.multi_select}<Checkbox checked={otherOn[step]} onchange={() => chooseOther(step)} aria-label="Other" />
        {:else}<input type="radio" name={`${group}-${step}`} checked={otherOn[step]} onchange={() => chooseOther(step)} aria-label="Other" />{/if}
        <span class="letter">{question.options.length + 1}</span>
        <input type="text" class="other-input" placeholder="Other: write your own" aria-label="Your own answer" maxlength="2000"
          value={other[step]} oninput={(event) => typing(step, (event.target as HTMLInputElement).value)} onfocus={() => { if (!otherOn[step]) chooseOther(step); }} />
      </div>
    </fieldset>
  {:else}
    <dl class="ask-review">
      {#each ask.questions as item, q (q)}
        <div>
          <dt>{item.question}</dt>
          <dd class:muted={!answered(q)}>{chosen(q)} <button class="link" onclick={() => go(q)}>Change</button></dd>
        </div>
      {/each}
    </dl>
  {/if}

  <div class="ask-foot">
    <button class="ghost" onclick={() => go(step - 1)} disabled={step === 0 || busy}><Icon name="back" size={16} />Back</button>
    <span class="grow"></span>
    {#if step === count}
      <button class="primary" onclick={submit} disabled={busy || !complete} aria-busy={busy}>Submit</button>
    {:else if review}
      <button class="primary" onclick={advance} disabled={busy || !answered(step)}>Next</button>
    {:else}
      <button class="primary" onclick={submit} disabled={busy || !answered(0)} aria-busy={busy}>Submit</button>
    {/if}
  </div>
  {#if step === count && !complete}<p class="field-error" role="status">Answer every question to submit.</p>{/if}
</div>
