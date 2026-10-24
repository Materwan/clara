<script lang="ts">
  import { download, saveBlob, size } from '../lib/api';
  import { inlineMath } from '../lib/markdown';
  import { type PanelFile, type PanelNote, baseName, folderOf, libraryLink, noteName, stamp } from '../lib/panel.svelte';
  import type { Form, Progress } from '../lib/qcm';
  import { toast } from '../lib/state.svelte';
  import Icon from './Icon.svelte';
  import QuizView from './QuizView.svelte';

  // What Clara made in this conversation, newest first: a card for each workspace file and each vault note (name, folder,
  // when; a click opens it in the Library, a file can also be downloaded) and for each QCM, which replaces the list when
  // opened. The Library is still the whole workspace and the whole vault.
  let { conversation, files, notes, forms, quiz = $bindable(null), onClose, onSend, onProgress }: {
    conversation: string;
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
  {:else if !cards.length}
    <p class="empty">Files and notes Clara writes and quizzes she makes for you appear here.</p>
  {:else}
    <ul class="cards">
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
  {/if}
</div>
