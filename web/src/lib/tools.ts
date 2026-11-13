// What Clara did, in words: one line per tool call, and one line for a run of them (calls with no text between).

// tool → [icon, what she did, the argument that says what about]
const NOTES: Record<string, [string, string, string?]> = {
  remember: ['memory', 'Remembered', 'fact'],
  forget: ['memory', 'Forgot a fact'],
  recall_facts: ['memory', 'Looked in memory for', 'query'],
  search_history: ['chat', 'Searched earlier conversations for', 'query'],
  remind: ['bell', 'Set a reminder', 'text'],
  list_reminders: ['bell', 'Checked your reminders'],
  cancel_reminder: ['bell', 'Cancelled a reminder'],
  notify: ['bell', 'Notified you', 'text'],
  add_task: ['tasks', 'Added a task', 'title'],
  list_tasks: ['tasks', 'Read your tasks'],
  update_task: ['tasks', 'Updated a task', 'title'],
  delete_task: ['tasks', 'Deleted a task'],
  qcm: ['quiz', 'Made a quiz', 'title'],
  ask_questions: ['chat', 'Asked you a question'],
  read_conversation_file: ['clip', 'Read the file', 'name'],
  folder_list: ['folder', 'Listed a folder on your computer', 'path'],
  folder_find: ['folder', 'Looked for files named', 'pattern'],
  folder_grep: ['folder', 'Searched your files for', 'pattern'],
  folder_read: ['folder', 'Read a file on your computer', 'path'],
  list_project_files: ['folder', "Listed the project's files"],
  read_project_file: ['folder', 'Read', 'path'],
  search_project: ['folder', 'Searched the project for', 'query'],
  ws_list: ['file', 'Listed the workspace', 'path'],
  ws_read: ['file', 'Read', 'path'],
  ws_search: ['file', 'Searched the workspace for', 'query'],
  ws_log: ['file', 'Read the history', 'path'],
  ws_write: ['file', 'Wrote', 'path'],
  ws_edit: ['file', 'Edited', 'path'],
  ws_move: ['file', 'Moved', 'path'],
  ws_delete: ['file', 'Deleted', 'path'],
  present: ['file', 'Presented', 'path'],
  ws_run: ['terminal', 'Ran', 'command'],
  web_search: ['globe', 'Searched the web for', 'query'],
  web_fetch: ['globe', 'Read a page', 'url'],
  github_read: ['branch', 'Looked at', 'repo'],
  github_write: ['branch', 'Changed a file in', 'repo'],
  github_branch: ['branch', 'Worked on a branch', 'name'],
  github_pr: ['branch', 'Worked on a pull request', 'title'],
  github_issue: ['branch', 'Worked on an issue', 'title'],
  drive_list: ['cloud', 'Listed Drive files'],
  drive_read: ['cloud', 'Read a Drive file', 'file'],
  drive_search: ['cloud', 'Searched Drive for', 'query'],
  drive_create: ['cloud', 'Created on Drive', 'name'],
  drive_update: ['cloud', 'Replaced a Drive file', 'file'],
  drive_delete: ['cloud', 'Moved a Drive file to the trash', 'file'],
  open_second_brain: ['book', 'Opened the vault'],
  vault_search: ['book', 'Searched the vault for', 'query'],
  vault_read: ['book', 'Read the note', 'note'],
  vault_list: ['book', 'Listed the notes', 'folder'],
  vault_write: ['book', 'Wrote the note', 'note'],
  vault_edit: ['book', 'Edited the note', 'note'],
  vault_daily: ['book', 'Opened the daily note'],
  vault_move: ['book', 'Moved the note', 'note'],
  automation_list: ['play', 'Looked at your automations'],
  automation_create: ['play', 'Made an automation', 'name'],
  automation_update: ['play', 'Changed an automation', 'name'],
  automation_delete: ['play', 'Deleted an automation'],
  deferred_book: ['calendar', 'Booked a call for later', 'tool'],
  deferred_list: ['calendar', 'Looked at the booked calls'],
  deferred_cancel: ['calendar', 'Cancelled a booked call'],
  mail_search: ['mail', 'Searched your mail for', 'query'],
  mail_read: ['mail', 'Read a mail'],
  mail_attachment_read: ['clip', 'Read a mail attachment'],
  mail_attachment_save: ['download', 'Saved a mail attachment', 'path'],
  mail_draft: ['mail', 'Saved a mail draft', 'subject'],
  mail_send: ['mail', 'Sent a mail', 'subject'],
  mail_labels: ['mail', 'Looked at your mail labels'],
  mail_label_create: ['mail', 'Made a mail label', 'name'],
  mail_label: ['mail', 'Labelled a mail'],
  mail_trash: ['mail', 'Moved a mail to the Trash'],
  mail_mark_read: ['mail', 'Marked a mail as read'],
  mail_label_delete: ['mail', 'Deleted a mail label', 'name'],
  school_calendar: ['calendar', 'Read your school calendar'],
  school_grades: ['book', 'Read your grades'],
  moodle_courses: ['book', 'Listed your Moodle courses'],
  moodle_files: ['folder', 'Listed the files of a Moodle course', 'course'],
  moodle_file_save: ['download', 'Saved a Moodle document', 'path'],
};

export type Call = { name: string; arguments: any; result?: string; running?: boolean; granted?: number }; // granted: the Approve all next that allowed it

/** How a call that is over did not go: read from the words of its result, which the server writes (tools/base.py, gate/approvals.py). */
export type Outcome = 'failed' | 'refused' | 'asked' | 'skipped';
export const OUTCOME_TAGS: Record<Outcome, string> = { failed: 'failed', refused: 'refused', asked: 'asked permission', skipped: 'not run' };

export function outcome(call: Call): Outcome | undefined {
  if (call.running || call.result === undefined) return undefined;
  const text = call.result.trimStart();
  if (/^(Refused: |The person refused this )/.test(text)) return 'refused';
  if (text.startsWith('Waiting for permission')) return 'asked';
  if (text.startsWith('[not run')) return 'skipped';
  if (/^(Error: |Unknown tool: )/.test(text) || text.startsWith('{"error"')) return 'failed';
  return undefined;
}

const words = (name: string): string => name.replaceAll('_', ' ');

/** A call that did not go as asked is named by its tool, not by what it would have done: « Saved… » must not stand over a refusal. */
export function note(call: Call): { icon: string; label: string; detail: string; outcome?: Outcome } {
  const [icon, label, key] = NOTES[call.name] ?? ['bolt', words(call.name)];
  const bad = outcome(call);
  let args = call.arguments;
  if (typeof args === 'string') {
    try {
      args = JSON.parse(args);
    } catch {
      args = {};
    }
  }
  const raw = key && args && typeof args[key] === 'string' ? args[key].trim() : '';
  return { icon, label: bad ? words(call.name) : label, detail: raw.length > 80 ? raw.slice(0, 79) + '…' : raw, outcome: bad };
}

/** The result without its closing WARNING: lines (the reader's report ends with its warning): the page shows them apart, never clipped.
 *  A WARNING: line anywhere else is the text's own and stays in the result. */
export function withoutWarnings(result: string): { text: string; warnings: string[] } {
  const lines = result.split('\n');
  const warnings: string[] = [];
  while (lines.length && lines[lines.length - 1].startsWith('WARNING:')) warnings.unshift(lines.pop() as string);
  return { text: lines.join('\n'), warnings };
}

/** What did not go as asked in a run of calls, for the tags beside its summary: « 1 failed », « 2 refused ». */
export function problems(calls: Call[]): { outcome: Outcome; count: number }[] {
  const found = new Map<Outcome, number>();
  for (const call of calls) {
    const bad = outcome(call);
    if (bad) found.set(bad, (found.get(bad) ?? 0) + 1);
  }
  return [...found].map(([bad, count]) => ({ outcome: bad, count }));
}

/** "Searched the web ×2, read a page · 3 actions" (the calls that did not go as asked are left out of the kinds: they have tags) */
export function summary(calls: Call[]): string {
  const counts = new Map<string, number>();
  for (const call of calls) {
    if (outcome(call)) continue;
    const kind = note(call).label.replace(/ (for|about|to)$/, '');
    counts.set(kind, (counts.get(kind) ?? 0) + 1);
  }
  const kinds = [...counts].map(([kind, n], index) => {
    const text = index ? kind.charAt(0).toLowerCase() + kind.slice(1) : kind;
    return n > 1 ? `${text} ×${n}` : text;
  });
  const shown = kinds.length > 3 ? [...kinds.slice(0, 3), '…'].join(', ') : kinds.join(', ');
  return `${shown ? shown + ' · ' : ''}${calls.length} actions`;
}
