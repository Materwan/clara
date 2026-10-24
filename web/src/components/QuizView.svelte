<script lang="ts">
  import { untrack } from 'svelte';
  import { api } from '../lib/api';
  import { inlineMath } from '../lib/markdown';
  import { type Form, type Progress, letter, stateOf } from '../lib/qcm';
  import { toast } from '../lib/state.svelte';
  import Checkbox from './Checkbox.svelte';
  import Icon from './Icon.svelte';

  // A QCM, one question at a time (the side panel). Check sends your answer to Clara (`onSend`, with the message the server
  // wrote) and marks it at once from her key; Skip sends nothing. The progress is the server's: every call returns it.
  let { conversation, form, onBack, onSend, onProgress }: {
    conversation: string;
    form: Form;
    onBack: () => void;
    onSend: (message: string) => void;
    onProgress: (progress: Progress) => void;
  } = $props();

  const base = $derived(`/v1/conversations/${conversation}/qcm/${form.ref}`);
  const count = $derived(form.questions.length);
  const group = `quiz-${Math.random().toString(36).slice(2, 8)}`; // radio names, unique to this view

  /** Where to start: the first question not answered (one skipped counts), the first one when all are. */
  function start(): number {
    const entries = form.progress.entries;
    const open = entries.findIndex((entry) => !entry);
    if (open >= 0) return open;
    return Math.max(0, entries.findIndex((entry) => entry?.status === 'skipped'));
  }

  let index = $state(untrack(start));
  let results = $state(untrack(() => form.progress.finished));
  let picked = $state<number[]>([]);
  let typed = $state('');
  let hinted = $state(false);
  let busy = $state(false);

  const question = $derived(form.questions[index]);
  const entry = $derived(form.progress.entries[index]);
  const answered = $derived(entry?.status === 'answered');
  const standing = $derived(stateOf(question, entry));
  const keyed = $derived(answered && question.type !== 'text' && question.correct !== null);
  const ready = $derived(question.type === 'text' ? typed.trim().length > 0 : picked.length > 0);
  const summary = $derived(form.progress.summary);
  const locked = $derived(busy || !!form.pending);

  function go(next: number): void {
    index = Math.max(0, Math.min(count - 1, next));
    results = false;
    picked = [];
    typed = '';
    hinted = false;
  }

  function toggle(option: number): void {
    if (answered) return;
    if (question.type === 'single') picked = [option];
    else picked = picked.includes(option) ? picked.filter((n) => n !== option) : [...picked, option].sort((a, b) => a - b);
  }

  async function settle(path: 'answer' | 'skip', body: object): Promise<boolean> {
    busy = true;
    try {
      const done = await api.post(`${base}/${path}`, body);
      onProgress(done.progress);
      if (done.message) onSend(done.message);
      return true;
    } catch (error) {
      toast((error as Error).message, 'error');
      return false;
    } finally {
      busy = false;
    }
  }

  async function check(): Promise<void> {
    if (!ready || locked) return;
    await settle('answer', { question: index + 1, answer: question.type === 'text' ? typed.trim() : picked });
  }

  async function skip(): Promise<void> {
    if (locked) return;
    if (await settle('skip', { question: index + 1 })) {
      if (index < count - 1) go(index + 1);
      else results = true;
    }
  }

  function next(): void {
    if (index < count - 1) go(index + 1);
    else results = true;
  }

  async function retry(): Promise<void> {
    busy = true;
    try {
      onProgress((await api.post(`${base}/retry`)).progress);
      go(0);
    } catch (error) {
      toast((error as Error).message, 'error');
    } finally {
      busy = false;
    }
  }

  const SAYS: Record<string, string> = { right: 'right', wrong: 'wrong', sent: 'sent to Clara', skipped: 'skipped', open: 'not answered' };
</script>

<div class="quiz">
  <div class="quiz-top">
    <button class="ghost small" onclick={onBack}><Icon name="back" size={16} />Back</button>
    <!-- svelte-ignore a11y_missing_content (the action writes the title) -->
    <h3 class="quiz-title" use:inlineMath={form.title || 'Quiz'}></h3>
    {#if form.progress.attempts > 1}<span class="tag">Attempt {form.progress.attempt}</span>{/if}
  </div>

  <div class="quiz-bar" role="group" aria-label="Questions">
    {#each form.questions as item, n (n)}
      {@const state = stateOf(item, form.progress.entries[n])}
      <button class="seg {state}" aria-current={!results && n === index ? 'step' : undefined} aria-label={`Question ${n + 1}: ${SAYS[state]}`}
        title={`Question ${n + 1}: ${SAYS[state]}`} onclick={() => go(n)}></button>
    {/each}
  </div>

  {#if results}
    <section class="quiz-body" aria-labelledby="quiz-results">
      <h4 id="quiz-results" class="sr-only">Results</h4>
      {#if summary.asked}
        <p class="score"><strong>{summary.right}</strong> / {summary.asked}<span class="unit">right</span></p>
      {:else}
        <p class="score"><strong>{summary.answered}</strong> / {count}<span class="unit">answered</span></p>
      {/if}
      <p class="muted small">
        {summary.answered} answered{summary.skipped.length ? ` · ${summary.skipped.length} skipped` : ''}{summary.open.length ? ` · ${summary.open.length} not reached` : ''}.
        {#if summary.answered && !summary.asked}Clara gave no key: her corrections are in the chat.{/if}
      </p>
      <ol class="recap">
        {#each form.questions as item, n (n)}
          {@const state = stateOf(item, form.progress.entries[n])}
          <li><button class="recap-row" onclick={() => go(n)}>
            <span class="dot-state {state}" aria-hidden="true"></span>
            <span class="recap-text" use:inlineMath={`${n + 1}. ${item.text}`}></span>
            <span class="recap-state">{SAYS[state]}</span>
          </button></li>
        {/each}
      </ol>
      <div class="quiz-foot">
        <button class="primary" onclick={retry} disabled={busy}>Retry</button>
        <button onclick={onBack}>Back to the list</button>
      </div>
    </section>
  {:else}
    <section class="quiz-body" aria-label={`Question ${index + 1} of ${count}`}>
      <div class="quiz-nav">
        <button class="ghost icon-btn" aria-label="Previous question" disabled={index === 0} onclick={() => go(index - 1)}><Icon name="left" size={18} /></button>
        <span class="quiz-count">Question {index + 1} / {count}</span>
        <button class="ghost icon-btn" aria-label="Next question" disabled={index === count - 1} onclick={() => go(index + 1)}><Icon name="chevron" size={18} /></button>
        <button class="ghost small results-link" onclick={() => (results = true)}>Results</button>
      </div>

      <fieldset class="quiz-q" disabled={answered || locked}>
        <legend use:inlineMath={question.text}></legend>
        {#if entry?.status === 'skipped'}<span class="tag">Skipped</span>{/if}
        {#if question.type === 'text'}
          <textarea rows="3" aria-label="Your answer" placeholder="Your answer" value={answered ? String(entry?.answer ?? '') : typed}
            oninput={(event) => (typed = (event.target as HTMLTextAreaElement).value)}></textarea>
        {:else}
          {#each question.options as option, o (o)}
            {@const chosen = (answered ? (entry?.answer as number[]) : picked).includes(o)}
            {@const inKey = keyed ? question.correct!.includes(o) : false}
            <label class="opt" class:chosen class:right={keyed && inKey} class:wrong={keyed && chosen && !inKey}>
              {#if question.type === 'single'}
                <input type="radio" name={`${group}-${index}`} checked={chosen} onchange={() => toggle(o)} />
              {:else}
                <Checkbox checked={chosen} onchange={() => toggle(o)} />
              {/if}
              <span class="letter">{letter(o)}</span><span class="opt-text" use:inlineMath={option}></span>
            </label>
          {/each}
        {/if}
      </fieldset>

      {#if !answered && hinted && question.hint}
        <p class="quiz-note hint"><Icon name="bulb" size={16} /><span use:inlineMath={question.hint}></span></p>
      {/if}

      {#if answered}
        <div class="quiz-note verdict {standing}" role="status">
          {#if standing === 'right'}<Icon name="check" size={16} /><span>Correct. Clara's comment follows in the chat.</span>
          {:else if standing === 'wrong'}<Icon name="close" size={16} /><span>Not quite. Clara's correction follows in the chat.</span>
          {:else}<Icon name="chat" size={16} /><span>Sent to Clara: her correction follows in the chat.</span>{/if}
        </div>
        {#if question.type === 'text' && question.answer}<p class="quiz-note">Expected: <span use:inlineMath={question.answer}></span></p>{/if}
        {#if question.explanation}<p class="quiz-note" use:inlineMath={question.explanation}></p>{/if}
      {/if}

      <div class="quiz-foot">
        {#if answered}
          <button class="primary" onclick={next}>{index < count - 1 ? 'Next' : 'See results'}</button>
        {:else}
          {#if question.hint}
            <button class="ghost" aria-expanded={hinted} onclick={() => (hinted = !hinted)}><Icon name="bulb" size={16} />Hint</button>
          {/if}
          <button class="ghost" onclick={skip} disabled={locked}>Skip<Icon name="forward" size={16} /></button>
          <button class="primary" onclick={check} disabled={!ready || locked}
            title={form.pending ? 'Clara is still finishing her message' : undefined}>Check answer</button>
        {/if}
      </div>
    </section>
  {/if}
</div>
