// A QCM as the server sends it (GET /v1/conversations/{id}/panel: connectors/forms.py for the form, connectors/quizzes.py
// for the progress), and what the side panel needs to draw it.

export interface Question {
  text: string;
  type: 'single' | 'multiple' | 'text';
  options: string[];
  correct: number[] | null;
  explanation: string;
  hint: string;
  answer: string;
}

/** What was done with a question: answered (the chosen option numbers, or the typed text) or skipped. */
export interface Entry {
  status: 'answered' | 'skipped';
  answer: number[] | string;
}

export interface Summary {
  total: number;
  answered: number;
  skipped: number[]; // question numbers, from 1
  open: number[];
  right: number;
  asked: number; // the answered questions that have a key
}

export interface Progress {
  attempt: number;
  attempts: number;
  entries: (Entry | null)[];
  summary: Summary;
  finished: boolean; // every question answered or skipped
  closed: boolean; // the final score was sent to Clara
  legacy: boolean; // answered before the panel existed, in one message
}

export interface Form {
  ref: string;
  title: string;
  graded: boolean;
  created_at: string;
  questions: Question[];
  progress: Progress;
  pending?: boolean; // Clara's message is not stored yet: the server cannot take answers
}

const LETTERS = 'ABCDEFGHIJ';
export const letter = (index: number): string => LETTERS[index];

export function emptyProgress(count: number): Progress {
  return {
    attempt: 1,
    attempts: 1,
    entries: Array.from({ length: count }, () => null),
    summary: { total: count, answered: 0, skipped: [], open: Array.from({ length: count }, (_, n) => n + 1), right: 0, asked: 0 },
    finished: false,
    closed: false,
    legacy: false,
  };
}

/** A form that has just been made, in the stream of the answer: nothing is stored yet. */
export function newForm(form: Omit<Form, 'progress' | 'created_at'>): Form {
  return { ...form, created_at: new Date().toISOString(), progress: emptyProgress(form.questions.length), pending: true };
}

/** Is the answer the key's? null when the question has no key (a free text, or a choice without `correct`). */
export function isRight(question: Question, answer: number[] | string): boolean | null {
  if (question.type === 'text' || !question.correct) return null;
  const given = [...(answer as number[])].sort((a, b) => a - b);
  return JSON.stringify(given) === JSON.stringify(question.correct);
}

export type State = 'right' | 'wrong' | 'sent' | 'skipped' | 'open';

/** Where a question stands: marked by the key, sent to Clara (no key), skipped, or not touched. */
export function stateOf(question: Question, entry: Entry | null): State {
  if (!entry) return 'open';
  if (entry.status === 'skipped') return 'skipped';
  const right = isRight(question, entry.answer);
  return right === null ? 'sent' : right ? 'right' : 'wrong';
}

const MARK = /^\[QCM (answer|finished) [0-9a-f]{8}(?: (\d+)\/(\d+))?\] ?([^\n]*)\n?/;

/** The message the panel sent for a question (connectors/forms.py, answer_message), drawn as a label and a body. */
export function quizMessage(content: string): { label: string; body: string } | null {
  const found = MARK.exec(content);
  if (!found) return null;
  const [head, kind, number, count, title] = found;
  const where = kind === 'finished' ? 'finished' : `question ${number}/${count}`;
  // each line of the message stays a line (a markdown hard break), and the formulas in it are drawn
  return { label: ['Quiz', title.trim(), where].filter(Boolean).join(' · '), body: content.slice(head.length).trim().replaceAll('\n', '  \n') };
}
