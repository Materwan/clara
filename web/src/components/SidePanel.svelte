<script lang="ts">
  import { download, saveBlob, size } from '../lib/api';
  import { inlineMath } from '../lib/markdown';
  import { type Plan } from '../lib/plan';
  import { type PanelFile, type PanelNote, baseName, fold, folded, folderOf, libraryLink, noteName, stamp } from '../lib/panel.svelte';
  import type { Form, Progress } from '../lib/qcm';
  import { type SubAgent, badge, cardLine } from '../lib/subagents';
  import { toast } from '../lib/state.svelte';
  import Icon from './Icon.svelte';
  import PlanCard from './PlanCard.svelte';
  import QuizView from './QuizView.svelte';

  // What Clara made in this conversation, newest first: a card for each workspace file and each vault note (name, folder,
  // when; a click opens it in the Library, a file can also be downloaded) and for each QCM, which replaces the list when
  // opened. Clara's plan (read only) comes first, then the sub agents she started (a card opens the sub agent's own conversation). The Library is
  // still the whole workspace and the whole vault. The sections Sub agents and Made here fold by their title (the plan does not); a folded title
  // shows how many cards it holds, and how many sub agents wait for your permission. Folding is display only.
  let { conversation, plan, subAgents, files, notes, forms, quiz = $bindable(null), onClose, onSend, onProgress }: {
    conversation: string;
    plan: Plan | null;
    subAgents: SubAgent[];
    files: PanelFile[];
    notes: PanelNote[];
    forms: Form[];
    quiz?: string | null; // the reference of the QCM that is open (null: the list)
    onClose: () => void;
    onSend: (message: string) => void;
    onProgress: (ref: string, progress: Progress) => void;
  } = $props();

  type Card = { kind: 'file'; at: string; file: PanelFile } | { kind: 'note'; at: string; note: PanelNote } | { kind: 'quiz'; at: string; form: Form };
  const cards = $derived<Card[]>(
    [
      ...files.map((file): Card => ({ kind: 'file', at: file.created, file })),
      ...notes.map((note): Card => ({ kind: 'note', at: note.created, note })),
      ...forms.map((form): Card => ({ kind: 'quiz', at: form.created_at, form })),
    ].sort((a, b) => b.at.localeCompare(a.at)),
  );
  const open = $derived(forms.find((form) => form.ref === quiz) ?? null);
  const asking = $derived(subAgents.filter((sub) => sub.waiting > 0).length); // a folded Sub agents section must not hide a request that waits for you

  // "Reported 2 minutes ago" moves on by itself while the panel stays open
  let now = $state(Date.now());
  $effect(() => {
    const timer = setInterval(() => (now = Date.now()), 30_000);
    return () => clearInterval(timer);
  });

  async function save(file: PanelFile): Promise<void> {
    try {
      saveBlob(await download(`/v1/workspace/file?path=${encodeURIComponent(file.path)}&raw=true`), baseName(file.path));
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  function progressLine(form: Form): string {
    const { summary } = form.progress;
    const count = `${form.questions.length} question${form.questions.length === 1 ? '' : 's'}`;
    if (form.pending) return `${count} · just made`;
    if (!summary.answered && !summary.skipped.length) return `${count} · not started`;
    if (form.progress.finished) return summary.asked ? `${count} · scored ${summary.right}/${summary.asked}` : `${count} · done`;
    return `${count} · ${summary.answered + summary.skipped.length} of ${form.questions.length} settled`;
  }
</script>

<div class="side-head">
  <h2 class="side-title">{open ? 'Quiz' : 'Made in this conversation'}</h2>
  <button class="ghost icon-btn" aria-label="Close the panel" onclick={onClose}><Icon name="close" size={18} /></button>
</div>

<div class="side-body">
  {#if open}
    {#key open.ref}
      <QuizView {conversation} form={open} onBack={() => (quiz = null)} {onSend} onProgress={(progress) => onProgress(open.ref, progress)} />
    {/key}
  {:else if !cards.length && !plan && !subAgents.length}
    <p class="empty">Files and notes Clara writes, quizzes she makes for you, her plan and the sub agents she starts appear here.</p>
  {:else}
    {#if plan}<PlanCard {plan} />{/if}
    {#if subAgents.length}
      <section class="fold" class:after={!!plan} aria-labelledby="subs-title">
        <h3 id="subs-title" class="subs-title">
          <button type="button" class="ghost fold-toggle" aria-expanded={!folded.subs} aria-controls="subs-list" onclick={() => fold('subs', !folded.subs)}>
            <span class="chev" aria-hidden="true"><Icon name="chevron" size={14} /></span>
            <span>Sub agents</span>
            {#if folded.subs}
              <span class="fold-count">· {subAgents.length}</span>
              {#if asking}<span class="fold-ask">· {asking} {asking === 1 ? 'asks' : 'ask'} you</span>{/if}
            {/if}
          </button>
        </h3>
        <ul id="subs-list" class="cards" hidden={folded.subs}>
          {#each subAgents as sub (sub.id)}
            {@const mark = badge(sub)}
            <li>
              <a class="sub-card" href={'#/chat/' + sub.id}>
                <span class="sub-head">
                  <strong class="sub-title" title={sub.title}>{sub.title || 'Sub agent'}</strong>
                  <span class="tag {mark.tone}">{mark.word}</span>
                </span>
                <span class="sub-line" title={sub.report || undefined}>{cardLine(sub, now)}</span>
                <span class="sub-open">Open its conversation <Icon name="chevron" size={14} /></span>
              </a>
            </li>
          {/each}
        </ul>
      </section>
    {/if}
    {#if cards.length}
      <section class="fold" class:after={!!plan || !!subAgents.length} aria-labelledby="made-title">
        <h3 id="made-title" class="subs-title">
          <button type="button" class="ghost fold-toggle" aria-expanded={!folded.made} aria-controls="made-list" onclick={() => fold('made', !folded.made)}>
            <span class="chev" aria-hidden="true"><Icon name="chevron" size={14} /></span>
            <span>Made here</span>
            {#if folded.made}<span class="fold-count">· {cards.length}</span>{/if}
          </button>
        </h3>
        <ul id="made-list" class="cards" hidden={folded.made}>
          {#each cards as card (card.kind === 'file' ? 'f:' + card.file.path : card.kind === 'note' ? 'n:' + card.note.path : 'q:' + card.form.ref)}
            <li>
              {#if card.kind === 'file'}
                <div class="card openable">
                  <a class="open" href={libraryLink('file', card.file.path)} title="Open in the Library">
                    <span class="glyph"><Icon name="file" size={18} /></span>
                    <span class="info">
                      <strong title={card.file.path}>{baseName(card.file.path)}</strong>
                      <span>{[folderOf(card.file.path), stamp(card.file.created), size(card.file.size)].filter(Boolean).join(' · ')}</span>
                    </span>
                  </a>
                  <button class="ghost icon-btn" title="Download" aria-label={`Download ${baseName(card.file.path)}`} onclick={() => save(card.file)}>
                    <Icon name="download" size={18} />
                  </button>
                </div>
              {:else if card.kind === 'note'}
                <div class="card openable">
                  <a class="open" href={libraryLink('note', card.note.path)} title="Open in the Library">
                    <span class="glyph"><Icon name="book" size={18} /></span>
                    <span class="info">
                      <strong title={card.note.path}>{noteName(card.note.path)}</strong>
                      <span>{[card.note.action === 'created' ? 'Created' : 'Edited', folderOf(card.note.path), stamp(card.note.created), size(card.note.size)].filter(Boolean).join(' · ')}</span>
                    </span>
                  </a>
                  <Icon name="chevron" size={16} />
                </div>
              {:else}
                <button class="card quiz-card" onclick={() => (quiz = card.form.ref)} aria-label={`Open the quiz ${card.form.title || ''}`.trim()}>
                  <span class="glyph"><Icon name="quiz" size={18} /></span>
                  <span class="info">
                    <strong use:inlineMath={card.form.title || 'Quiz'}></strong>
                    <span>{[progressLine(card.form), stamp(card.form.created_at)].filter(Boolean).join(' · ')}</span>
                  </span>
                  <Icon name="chevron" size={16} />
                </button>
              {/if}
            </li>
          {/each}
        </ul>
      </section>
    {/if}
  {/if}
</div>

<style>
  .fold.after { margin-top: var(--sp-8); }
  .subs-title { margin: 0; font-size: var(--fs-md); font-weight: 650; }
  .fold .cards { margin-top: var(--sp-4); }
  .fold .cards[hidden] { display: none; } /* .cards sets display, which would beat the hidden attribute */
  /* the title is the button that folds its section: a touch target of 32px or more, the chevron turns down when the section is open */
  .fold-toggle {
    width: 100%; min-height: 32px; justify-content: flex-start; gap: var(--sp-6); padding: var(--sp-4) var(--sp-8);
    color: var(--text-2); font-size: inherit; font-weight: inherit; text-align: left; white-space: normal; overflow-wrap: anywhere;
  }
  .fold-toggle:hover:not(:disabled) { color: var(--text); }
  .chev { display: inline-flex; flex: none; color: var(--muted); }
  .fold-toggle[aria-expanded='true'] .chev { transform: rotate(90deg); }
  .fold-count { color: var(--muted); font-weight: 500; }
  .fold-ask { color: var(--warn); } /* the words say it too: colour is not the only sign */
  /* a sub agent: a card with a thin border, the whole of it a link to the sub agent's conversation */
  .sub-card {
    display: flex; flex-direction: column; gap: var(--sp-4); min-width: 0; padding: var(--sp-12) var(--sp-14);
    border: 0.5px solid var(--line-strong); border-radius: var(--r-lg); background: var(--raised);
    color: inherit; text-decoration: none;
  }
  .sub-card:hover { border-color: var(--text-2); background: var(--hover); }
  .sub-card:focus-visible { outline: 2px solid var(--text); outline-offset: 2px; }
  .sub-head { display: flex; align-items: center; justify-content: space-between; gap: var(--sp-8); min-width: 0; }
  .sub-title { min-width: 0; font-size: 0.9375rem; font-weight: 500; line-height: 1.3; overflow-wrap: anywhere; }
  .sub-head .tag { flex: none; }
  .tag.run { background: var(--accent-soft); color: var(--accent-text); }
  .sub-line { font-size: var(--fs-sm); color: var(--muted); line-height: 1.4; overflow-wrap: anywhere; }
  .sub-open { display: inline-flex; align-items: center; gap: var(--sp-2); align-self: flex-start; margin-top: var(--sp-2); font-size: var(--fs-sm); color: var(--accent-text); }
</style>
