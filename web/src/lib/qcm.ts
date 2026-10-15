// The answers to a QCM, written exactly as the server's connectors/forms.py expects (format_answers): keep in step.

export interface Question {
  text: string;
  type: 'single' | 'multiple' | 'text';
  options: string[];
  correct: number[] | null;
  explanation: string;
  answer: string;
}

export interface Form {
  ref: string;
  title: string;
  graded: boolean;
  questions: Question[];
  answers?: (number[] | string)[] | null;
}

const LETTERS = 'ABCDEFGHIJ';

export function formatAnswers(form: Form, answers: (number[] | string)[]): string {
  const lines = [`[QCM answers ${form.ref}]` + (form.title ? ` ${form.title}` : '')];
  form.questions.forEach((question, index) => {
    const answer = answers[index];
    let given: string;
    if (question.type === 'text') given = String(answer ?? '').trim();
    else given = [...((answer as number[]) ?? [])].sort((a, b) => a - b).map((n) => `${LETTERS[n]}. ${question.options[n]}`).join('; ');
    lines.push(`\n${index + 1}. ${question.text}\nAnswer: ${given || '(no answer)'}`);
  });
  return lines.join('\n');
}

export function grade(form: Form, answers: (number[] | string)[]): { right: number; asked: number } {
  let right = 0;
  let asked = 0;
  form.questions.forEach((question, index) => {
    if (question.type === 'text' || !question.correct) return;
    asked += 1;
    const given = [...((answers[index] as number[]) ?? [])].sort((a, b) => a - b);
    if (JSON.stringify(given) === JSON.stringify(question.correct)) right += 1;
  });
  return { right, asked };
}

export const letter = (index: number) => LETTERS[index];
