<script lang="ts">
  import { untrack } from 'svelte';
  import { inlineMath } from '../lib/markdown';
  import { type Form, formatAnswers, grade, letter } from '../lib/qcm';

  let { form, onAnswer }: { form: Form; onAnswer?: (text: string) => void } = $props();
  // A form is answered once: its answers start from what it carries and are then only the reader's.
  let answers = $state<(number[] | string)[]>(untrack(() => form.answers ?? form.questions.map((q) => (q.type === 'text' ? '' : []))));
  let sent = $state(untrack(() => !!form.answers));
  const score = $derived(sent && form.graded ? grade(form, answers) : null);

  function toggle(question: number, option: number, single: boolean): void {
    if (sent) return;
    const current = answers[question] as number[];
    answers[question] = single ? [option] : current.includes(option) ? current.filter((n) => n !== option) : [...current, option];
  }

  function submit(): void {
    sent = true;
    onAnswer?.(formatAnswers(form, answers));
  }
</script>

<div class="qcm panel">
  {#if form.title}
    <!-- svelte-ignore a11y_missing_content (the action writes the title) -->
    <h3 use:inlineMath={form.title}></h3>
  {/if}
  {#each form.questions as question, q}
    <fieldset>
      <legend use:inlineMath={`${q + 1}. ${question.text}`}></legend>
      {#if question.type === 'text'}
        <textarea rows="2" disabled={sent} bind:value={answers[q] as string}></textarea>
        {#if sent && question.answer}<div class="small muted">Expected: <span use:inlineMath={question.answer}></span></div>{/if}
      {:else}
        {#each question.options as option, o}
          {@const chosen = (answers[q] as number[]).includes(o)}
          {@const right = sent && question.correct ? question.correct.includes(o) : null}
          <label class="option" class:right={right === true} class:wrong={right === false && chosen}>
            <input type={question.type === 'single' ? 'radio' : 'checkbox'} checked={chosen} disabled={sent}
              onchange={() => toggle(q, o, question.type === 'single')} />
            <span>{letter(o)}.</span> <span use:inlineMath={option}></span>
          </label>
        {/each}
      {/if}
      {#if sent && question.explanation}<div class="small muted" use:inlineMath={question.explanation}></div>{/if}
    </fieldset>
  {/each}
  {#if score}<p><strong>Score: {score.right} / {score.asked}</strong></p>{/if}
  {#if !sent && onAnswer}<button class="primary" onclick={submit}>Send my answers</button>{/if}
</div>

<style>
  .qcm {
    margin: 0.5rem 0;
  }
  fieldset {
    border: none;
    padding: 0;
    margin: 0 0 0.8rem;
  }
  legend {
    font-weight: 600;
    margin-bottom: 0.3rem;
  }
  .option {
    display: flex;
    gap: 0.4rem;
    align-items: baseline;
    padding: 0.15rem 0.3rem;
    border-radius: 6px;
  }
  .option.right {
    background: color-mix(in srgb, var(--ok) 15%, transparent);
  }
  .option.wrong {
    background: color-mix(in srgb, var(--bad) 15%, transparent);
  }
  textarea {
    width: 100%;
  }
</style>
