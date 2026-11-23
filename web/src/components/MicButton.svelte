<script lang="ts" module>
  import { api } from '../lib/api';
  import { DEFAULT_MAX_SECONDS, maxSeconds } from '../lib/dictation';

  let known: number | null = null; // the longest dictation the server allows (`dictation_max_seconds`), asked once per page load
  let asked: Promise<number> | null = null;
  const longest = (): Promise<number> => (asked ??= api.get('/v1/admin/settings')
    .then((found) => (known = maxSeconds(found.settings))).catch(() => (known = DEFAULT_MAX_SECONDS)));
</script>

<script lang="ts">
  import { onDestroy, untrack } from 'svelte';
  import { postAudio } from '../lib/api';
  import { type Answer, type Event, type Phase, clock, dictationPath, failure, pickMimeType, step, unavailable } from '../lib/dictation';
  import { toast } from '../lib/state.svelte';
  import Icon from './Icon.svelte';

  // The microphone button of the box you write in (docs/DECISIONS.md, « The web site may use the microphone, on a press only »). One press opens the
  // microphone and starts a recording with a timer; a second press stops it and sends the audio to Clara, who turns it into text on her computer.
  // The microphone is never open otherwise: every way a recording ends (stop, Cancel, Escape, the page hidden, another conversation opened, an
  // error, leaving the page) stops every track of the stream, so that the browser's recording indicator goes off. The state machine is lib/dictation.ts.
  let { conversation, disabled = false, onAnswer }: { conversation: string | null; disabled?: boolean; onAnswer: (answer: Answer) => void } = $props();

  let phase = $state<Phase>('idle');
  let elapsed = $state(0); // seconds of the recording under way
  let said = $state(''); // what a screen reader is told (a polite live region): not every second, the timer is only drawn
  let stream: MediaStream | null = null;
  let recorder: MediaRecorder | null = null;
  let chunks: Blob[] = [];
  let controller: AbortController | null = null;
  let ticker: ReturnType<typeof setInterval> | undefined;
  let started = 0;
  let limit = known ?? DEFAULT_MAX_SECONDS;
  let epoch = 0; // bumped by whatever ends a recording or a request: an answer of the browser or of the server that arrives later is dropped

  void longest().then((seconds) => (limit = seconds));

  const NOTICES: Partial<Record<Event, string>> = {
    hidden: 'The page was hidden: the recording was stopped and nothing was sent.',
    moved: 'You opened another conversation: the dictation was stopped and nothing was sent.',
  };

  function fire(event: Event): void {
    const next = step(phase, event);
    if (next.phase === phase && next.action === 'none') return;
    const before = phase;
    phase = next.phase;
    if (next.action === 'start') void open();
    else if (next.action === 'send') finish();
    else if (next.action === 'discard' || next.action === 'abort') {
      epoch++;
      release();
      controller?.abort();
      controller = null;
      said = '';
      const notice = NOTICES[event];
      if (notice && before !== 'idle') toast(notice, 'info');
    }
  }

  /** Close the microphone: every track stopped, the recorder and the timer dropped, the audio forgotten. */
  function release(): void {
    clearInterval(ticker);
    if (recorder && recorder.state !== 'inactive') {
      recorder.ondataavailable = null;
      recorder.onstop = null;
      try { recorder.stop(); } catch { /* already stopped */ }
    }
    stream?.getTracks().forEach((track) => { track.onended = null; track.stop(); });
    recorder = null;
    stream = null;
    chunks = [];
  }

  async function open(): Promise<void> {
    const mine = epoch;
    let got: MediaStream;
    try {
      got = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      if (mine !== epoch) return;
      fire('denied');
      toast(failure(error as Error), 'error');
      return;
    }
    if (mine !== epoch) { // cancelled while the browser asked: the microphone it just opened is closed at once
      got.getTracks().forEach((track) => track.stop());
      return;
    }
    try {
      const type = pickMimeType((candidate) => MediaRecorder.isTypeSupported(candidate));
      const made = new MediaRecorder(got, type ? { mimeType: type } : undefined);
      made.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      made.onerror = () => { if (mine === epoch) { fire('failed'); toast('The recording failed: the microphone stopped.', 'error'); } };
      stream = got;
      recorder = made;
      chunks = [];
      got.getTracks().forEach((track) => { track.onended = () => { if (mine === epoch && phase === 'recording') { fire('failed'); toast('The microphone was disconnected: nothing was sent.', 'error'); } }; });
      made.start(1000);
    } catch (error) {
      got.getTracks().forEach((track) => track.stop());
      fire('denied');
      toast(failure(error as Error), 'error');
      return;
    }
    started = performance.now();
    elapsed = 0;
    said = 'Recording.';
    ticker = setInterval(tick, 250);
    fire('granted');
  }

  function tick(): void {
    elapsed = (performance.now() - started) / 1000;
    const whole = Math.floor(elapsed);
    if (whole > 0 && whole % 30 === 0) said = `Recording, ${whole} seconds.`;
    if (elapsed >= limit) fire('limit');
  }

  /** Stop recording and send what was said. The microphone closes now, not when the answer comes. */
  function finish(): void {
    const made = recorder;
    if (!made) return fire('failed');
    clearInterval(ticker);
    const mine = ++epoch;
    const own = new AbortController();
    controller = own;
    const parts = chunks;
    const type = made.mimeType;
    said = 'Turning your dictation into text.';
    made.onstop = () => void upload(new Blob(parts, { type }), mine, own.signal);
    made.ondataavailable = (event) => { if (event.data.size) parts.push(event.data); };
    try { made.stop(); } catch { /* inactive */ }
    stream?.getTracks().forEach((track) => { track.onended = null; track.stop(); });
    stream = null;
  }

  async function upload(audio: Blob, mine: number, signal: AbortSignal): Promise<void> {
    recorder = null;
    chunks = [];
    if (mine !== epoch) return;
    if (!audio.size) {
      fire('failed');
      toast('Nothing was recorded.', 'error');
      return;
    }
    try {
      const answer = await postAudio<Answer>(dictationPath(conversation), audio, signal);
      if (mine !== epoch) return;
      said = '';
      fire('answered');
      onAnswer(answer);
    } catch (error) {
      if (mine !== epoch || (error as Error).name === 'AbortError') return;
      said = '';
      fire('failed');
      toast(failure(error as Error), 'error');
    }
  }

  function press(): void {
    if (phase === 'idle') {
      const why = unavailable({
        secure: window.isSecureContext, devices: !!navigator.mediaDevices?.getUserMedia, recorder: typeof MediaRecorder !== 'undefined',
      });
      if (why) {
        toast(why, 'error');
        return;
      }
    }
    fire('press');
  }

  // Opening another conversation ends a recording or a request: they belong to the one they were started in. (Leaving this page ends them below.)
  let seen = untrack(() => conversation);
  $effect(() => {
    const now = conversation;
    untrack(() => {
      if (now === seen) return;
      seen = now;
      fire('moved');
    });
  });

  onDestroy(() => {
    epoch++;
    release();
    controller?.abort();
  });

  const label = $derived(phase === 'recording' ? 'Stop and send' : phase === 'sending' ? 'Turning the dictation into text' : phase === 'starting' ? 'Waiting for the microphone' : 'Start dictation');
  const HELP =
    'Dictate: press to record, press again to stop and send. The audio goes to Clara’s computer to be turned into text and is never kept. ' +
    'The conversation then counts as outside content for good, because a microphone also hears other voices: what it writes outside still asks you.';
</script>

<svelte:window onkeydown={(event) => { if (event.key === 'Escape' && phase !== 'idle') fire('cancel'); }} />
<svelte:document onvisibilitychange={() => { if (document.hidden) fire('hidden'); }} />

<span class="mic">
  <button type="button" class="mic-button" class:recording={phase === 'recording'} aria-label={label} aria-pressed={phase === 'recording'}
    aria-describedby="mic-help" aria-busy={phase === 'sending'} title={phase === 'idle' ? HELP : label}
    disabled={disabled} aria-disabled={phase === 'starting' || phase === 'sending' ? 'true' : undefined} onclick={press}>
    {#if phase === 'sending'}<span class="spin" aria-hidden="true"></span>
    {:else if phase === 'recording'}<span class="rec" aria-hidden="true"></span><span class="time" aria-hidden="true">{clock(elapsed)}</span>
    {:else}<Icon name="mic" size={20} />{/if}
  </button>
  {#if phase !== 'idle'}
    <button type="button" class="ghost icon-btn mic-cancel" aria-label="Cancel the dictation" title="Cancel (Escape)" onclick={() => fire('cancel')}><Icon name="close" size={18} /></button>
  {/if}
  <span id="mic-help" class="sr-only">{HELP}</span>
  <span class="sr-only" role="status" aria-live="polite">{said}</span>
</span>

<style>
  .mic { display: inline-flex; align-items: center; gap: var(--sp-2); flex: none; }
  .mic-button { display: inline-flex; align-items: center; justify-content: center; gap: var(--sp-6); min-width: 40px; height: 40px; padding: 0 var(--sp-8); border-radius: var(--r-md); border: none; background: transparent; color: var(--muted); }
  .mic-button:hover:not(:disabled) { background: var(--hover); color: var(--text); }
  .mic-button.recording, .mic-button.recording:hover:not(:disabled) { background: var(--danger); color: var(--danger-ink); font-weight: 650; font-variant-numeric: tabular-nums; }
  .mic-button[aria-disabled='true'] { cursor: progress; }
  .rec { width: .55rem; height: .55rem; border-radius: 50%; background: currentColor; animation: rec-pulse 1.2s ease-in-out infinite; }
  @keyframes rec-pulse { 50% { opacity: .25; } }
  @media (max-width: 600px), (pointer: coarse) { .mic-button, .mic-cancel { min-width: 44px; min-height: 44px; } }
</style>
