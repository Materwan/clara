// Voice dictation on the page (docs/DECISIONS.md, « The web site may use the microphone, on a press only »): the pure rules of the
// microphone button, kept apart from the components so that node can test them. The microphone is opened by a press and by nothing
// else; every rule below that ends a recording ends it with the microphone closed.

export type Answer = { text: string; seconds: number; conversation: string | null; sent: boolean; running?: boolean };
export type Client = { client: string; conversation: string | null; auto_send: boolean; mode: string | null; packs: string[] };

/** The longest dictation when the server did not say (`dictation_max_seconds`, 300 by default). */
export const DEFAULT_MAX_SECONDS = 300;

/** The clients the server knows (`dictation/clients.py`), with what each is called on the page. */
export const CLIENT_NAMES: Record<string, string> = {
  web: 'This web site', island: 'Windows island', phone: 'Phone', cli: 'Terminal (clara-chat)', discord: 'Discord',
};

/** What the recorder may write, best first: webm with opus (Chrome, Firefox, Edge), ogg, then mp4 (Safari). The server decodes all of them. */
const MIME_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];

/** The first type the browser records, or '' to let it choose its own. */
export const pickMimeType = (supported: (type: string) => boolean): string => MIME_TYPES.find((type) => supported(type)) ?? '';

/** Why this page cannot record, or null. Called on a press: the button explains instead of failing without a word. */
export function unavailable(env: { secure: boolean; devices: boolean; recorder: boolean }): string | null {
  if (!env.secure) return 'The microphone only works on a secure page. Open Clara at its https address (the Tailscale one), or at localhost, and try again.';
  if (!env.devices || !env.recorder) return 'This browser cannot record from the microphone.';
  return null;
}

/** 83 seconds is « 01:23 ». */
export const clock = (seconds: number): string => {
  const whole = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(whole / 60)).padStart(2, '0')}:${String(whole % 60).padStart(2, '0')}`;
};

/** The longest dictation, from the runtime settings the server lists (`GET /v1/admin/settings`). */
export function maxSeconds(settings: { key: string; value: unknown }[] | undefined): number {
  const value = settings?.find((item) => item.key === 'dictation_max_seconds')?.value;
  return typeof value === 'number' && value >= 5 && value <= 3600 ? value : DEFAULT_MAX_SECONDS;
}

// ---- the button's state machine ---------------------------------------------------------------------------------
// idle → starting (the browser asks for the microphone) → recording → sending (the audio goes up and is turned into text) → idle.
export type Phase = 'idle' | 'starting' | 'recording' | 'sending';
/** `press`: the button; `granted`/`denied`: the browser's answer; `limit`: the longest dictation is reached; `hidden`: the page was hidden;
 *  `moved`: another conversation was opened; `cancel`: the Cancel button or Escape; `answered`/`failed`: the server's answer. */
export type Event = 'press' | 'granted' | 'denied' | 'limit' | 'hidden' | 'moved' | 'cancel' | 'answered' | 'failed';
/** `start`: ask for the microphone; `send`: stop recording and send the audio; `discard`: close the microphone and keep nothing;
 *  `abort`: drop the request (the server then stops the work). */
export type Action = 'start' | 'send' | 'discard' | 'abort' | 'none';

export function step(phase: Phase, event: Event): { phase: Phase; action: Action } {
  switch (phase) {
    case 'idle':
      return event === 'press' ? { phase: 'starting', action: 'start' } : { phase, action: 'none' };
    case 'starting': // the permission prompt is open: whatever ends it leaves the microphone closed
      if (event === 'granted') return { phase: 'recording', action: 'none' };
      if (event === 'denied') return { phase: 'idle', action: 'none' };
      if (event === 'hidden' || event === 'moved' || event === 'cancel') return { phase: 'idle', action: 'discard' };
      return { phase, action: 'none' };
    case 'recording':
      if (event === 'press' || event === 'limit') return { phase: 'sending', action: 'send' };
      if (event === 'hidden' || event === 'moved' || event === 'cancel' || event === 'failed') return { phase: 'idle', action: 'discard' };
      return { phase, action: 'none' };
    case 'sending': // the microphone is already closed: a hidden page does not stop the transcription, leaving the conversation or Cancel does
      if (event === 'answered' || event === 'failed') return { phase: 'idle', action: 'none' };
      if (event === 'moved' || event === 'cancel') return { phase: 'idle', action: 'abort' };
      return { phase, action: 'none' };
  }
}

// ---- words ------------------------------------------------------------------------------------------------------
type Failure = { name?: string; status?: number; code?: string; message?: string };

/** What went wrong, in plain words: the browser's refusal of the microphone, or the server's answer. */
export function failure(error: Failure): string {
  switch (error.name) {
    case 'NotAllowedError':
    case 'SecurityError':
      return 'The microphone was refused. Allow it for this site (the lock or settings icon in the address bar), then press the button again.';
    case 'NotFoundError':
    case 'OverconstrainedError':
      return 'No microphone was found. Plug one in or choose one in your system settings, then try again.';
    case 'NotSupportedError':
      return 'This browser cannot use the microphone here.';
    case 'NotReadableError':
    case 'AbortError':
      return 'The microphone cannot be opened: another program may be using it.';
  }
  switch (error.status) {
    case 0: return 'Clara cannot be reached: check the connection and try again.';
    case 409: return 'Clara is already answering in this conversation: wait for her, or stop her, then dictate again.';
    case 413: return error.code === 'too_long' ? 'That recording is too long: Clara takes a few minutes at most.' : 'That recording is too big to send.';
    case 422: return 'Clara could not read that recording. Try again.';
    case 429: return 'Clara is turning another dictation into text: try again in a moment.';
    case 503: return error.message || 'Dictation is not installed on this Clara.'; // the server says how to fix it (the extra, or `/dictation fetch-model`)
    case 504: return 'The transcription took too long and was stopped. Try a shorter recording.';
  }
  return error.message || 'The dictation failed.';
}

/** The text put at the end of what is already in the box, on a line of its own only if the box ended with one. */
export function withText(draft: string, text: string): string {
  if (!draft.trim()) return text;
  return `${draft.replace(/[ \t]+$/, '')}${/\n$/.test(draft) ? '' : ' '}${text}`;
}

/** What the page does with the server's answer: nothing was heard, the server sent it (follow the run), or the text goes into the box. */
export const outcome = (answer: Answer): 'silence' | 'follow' | 'compose' =>
  !answer.text.trim() ? 'silence' : answer.sent ? 'follow' : 'compose';

/** The route of a dictation from this page: into the open conversation, else into the web client's own (the server uses or makes it). */
export const dictationPath = (conversation: string | null): string =>
  `/v1/dictation?${new URLSearchParams(conversation ? { client: 'web', conversation } : { client: 'web' })}`;

/** The settings of a client as the page edits them, and the body of the PUT that changes only what differs from what the server holds. */
export type Edit = { auto_send: boolean; mode: string | null; packs: string[] };
export const editOf = (client: Client): Edit => ({ auto_send: client.auto_send, mode: client.mode, packs: [...client.packs] });
export function changes(saved: Edit, edit: Edit): Partial<Edit> {
  const body: Partial<Edit> = {};
  if (edit.auto_send !== saved.auto_send) body.auto_send = edit.auto_send;
  if (edit.mode !== saved.mode) body.mode = edit.mode;
  if ([...edit.packs].sort().join() !== [...saved.packs].sort().join()) body.packs = edit.packs;
  return body;
}
