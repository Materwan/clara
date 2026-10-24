// A question that stops you until it is answered, in place of the browser's confirm() and prompt(): they ignore the theme,
// cannot hide what you type (a secret), and a browser may switch them off after a few. Dialog.svelte, mounted once in
// App.svelte, shows whichever is open.
//
//   if (!(await confirmDialog({ message: 'Delete this?', confirm: 'Delete', danger: true }))) return;
//   const value = await promptDialog({ message: 'New value of x', type: 'password' });   // null when cancelled

export type DialogRequest = {
  kind: 'confirm' | 'prompt';
  title: string;
  message: string;
  confirm: string; // the label of the button that says yes
  danger: boolean; // it cannot be undone: the yes button is red and the focus starts on No
  label: string; // prompt only: what the field is
  type: 'text' | 'password' | 'datetime-local';
  value: string; // prompt only: what the field starts with
};

export const dialog = $state<{ current: DialogRequest | null }>({ current: null });

let resolver: ((answer: boolean | string | null) => void) | null = null;

function open(request: DialogRequest): Promise<boolean | string | null> {
  resolver?.(request.kind === 'confirm' ? false : null); // a new question replaces one still open, which counts as No
  return new Promise((resolve) => {
    resolver = resolve;
    dialog.current = request;
  });
}

export async function confirmDialog(options: { message: string; title?: string; confirm?: string; danger?: boolean }): Promise<boolean> {
  const answer = await open({
    kind: 'confirm', title: options.title ?? '', message: options.message, confirm: options.confirm ?? 'Confirm',
    danger: options.danger ?? false, label: '', type: 'text', value: '',
  });
  return answer === true;
}

/** The text typed, or null when cancelled. */
export async function promptDialog(options: {
  message: string; title?: string; label?: string; value?: string; type?: DialogRequest['type']; confirm?: string;
}): Promise<string | null> {
  const answer = await open({
    kind: 'prompt', title: options.title ?? '', message: options.message, confirm: options.confirm ?? 'Save', danger: false,
    label: options.label ?? options.message, type: options.type ?? 'text', value: options.value ?? '',
  });
  return typeof answer === 'string' ? answer : null;
}

/** Called by Dialog.svelte with the answer, or with false / null for a cancel. */
export function settle(answer: boolean | string | null): void {
  const done = resolver;
  resolver = null;
  dialog.current = null;
  done?.(answer);
}
