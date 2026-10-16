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
  read_conversation_file: ['clip', 'Read the file', 'name'],
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
};

export type Call = { name: string; arguments: any; result?: string; running?: boolean };

export function note(call: Call): { icon: string; label: string; detail: string } {
  const [icon, label, key] = NOTES[call.name] ?? ['bolt', call.name.replaceAll('_', ' ')];
  let args = call.arguments;
  if (typeof args === 'string') {
    try {
      args = JSON.parse(args);
    } catch {
      args = {};
    }
  }
  const raw = key && args && typeof args[key] === 'string' ? args[key].trim() : '';
  return { icon, label, detail: raw.length > 80 ? raw.slice(0, 79) + '…' : raw };
}

/** "Searched the web ×2, read a page · 3 actions" */
export function summary(calls: Call[]): string {
  const counts = new Map<string, number>();
  for (const call of calls) {
    const kind = note(call).label.replace(/ (for|about|to)$/, '');
    counts.set(kind, (counts.get(kind) ?? 0) + 1);
  }
  const kinds = [...counts].map(([kind, n], index) => {
    const text = index ? kind.charAt(0).toLowerCase() + kind.slice(1) : kind;
    return n > 1 ? `${text} ×${n}` : text;
  });
  return `${kinds.length > 3 ? [...kinds.slice(0, 3), '…'].join(', ') : kinds.join(', ')} · ${calls.length} actions`;
}
