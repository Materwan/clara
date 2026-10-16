<script lang="ts">
  import { untrack } from 'svelte';
  import { inlineMath } from '../lib/markdown';
  import { type Form, formatAnswers, grade, letter } from '../lib/qcm';

  let { form, onAnswer }: { form: Form; onAnswer?: (text: string) => void } = $props();
  // A form is answered once: its answers start from what it carries and are then only the reader's.
  let answers = $state<(number[] | string)[]>(untrack(() => form.answers ?? form.questions.map((q) => (q.type === 'text' ? '' : []))));
  let sent = $state(untrack(() => !!form.answers));
  const group = `qcm-${Math.random().toString(36).slice(2, 8)}`; // radio names, unique to this form
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

<div class="qcm">
  {#if form.title}
    <!-- svelte-ignore a11y_missing_content (the action writes the title) -->
    <h3 use:inlineMath={form.title}></h3>
  {/if}
  {#each form.questions as question, q}
    <fieldset disabled={sent}>
      <legend use:inlineMath={`${q + 1}. ${question.text}`}></legend>
      {#if question.type === 'text'}
        <textarea rows="2" bind:value={answers[q] as string} aria-label={`Answer ${q + 1}`}></textarea>
        {#if sent && question.answer}<p class="qcm-explain">Expected: <span use:inlineMath={question.answer}></span></p>{/if}
      {:else}
        {#each question.options as option, o}
          {@const chosen = (answers[q] as number[]).includes(o)}
          {@const right = sent && question.correct ? question.correct.includes(o) : null}
          <label class="qcm-option" class:right={right === true} class:wrong={right === false && chosen}>
            <input type={question.type === 'single' ? 'radio' : 'checkbox'} name={`${group}-${q}`} checked={chosen}
              onchange={() => toggle(q, o, question.type === 'single')} />
            <span class="letter">{letter(o)}</span><span use:inlineMath={option}></span>
          </label>
        {/each}
      {/if}
      {#if sent && question.explanation}<p class="qcm-explain" use:inlineMath={question.explanation}></p>{/if}
    </fieldset>
  {/each}
  {#if score || (!sent && onAnswer)}
    <div class="qcm-foot">
      {#if score}<p class="qcm-score">Score: <strong>{score.right} / {score.asked}</strong></p>{:else}<span class="muted small">Answer, then send: Clara reads your answers.</span>{/if}
      {#if !sent && onAnswer}<button class="primary" onclick={submit}>Send my answers</button>{/if}
    </div>
  {/if}
</div>
