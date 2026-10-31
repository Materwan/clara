// Waiting for an answer, and asking again: what a read does when the link hiccups (a connection that drops, a proxy that
// answers 502 while Clara restarts, a request that never comes back). It imports nothing, so tests/retry.test.mjs can run it
// alone; api.ts is the one that applies it to `fetch`.

/** A request that had not been answered when its time ran out. */
export class TimedOut extends Error {}

/**
 * Runs `run` with a signal that is aborted when `ms` have passed (0: never) or when `outer` is aborted. If the time ran out
 * and `run` failed because of it, the error is `TimedOut`; any other failure is `run`'s own.
 */
export async function within<T>(ms: number, outer: AbortSignal | undefined, run: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const inner = new AbortController();
  const forward = (): void => inner.abort();
  if (outer?.aborted) inner.abort();
  outer?.addEventListener('abort', forward, { once: true });
  let expired = false;
  const clock = ms > 0 ? setTimeout(() => { expired = true; inner.abort(); }, ms) : undefined;
  try {
    return await run(inner.signal);
  } catch (error) {
    if (expired) throw new TimedOut(`No answer within ${Math.round(ms / 1000)} s.`);
    throw error;
  } finally {
    clearTimeout(clock);
    outer?.removeEventListener('abort', forward);
  }
}

export type Retry = {
  /** How many more times to ask after a failure. */
  retries: number;
  /** Is this failure worth asking again for? (the others are thrown at once) */
  retryable: (error: unknown) => boolean;
  /** The first pause, in ms; each next one is three times longer (400, 1200, ...). */
  pause?: number;
  /** The caller gave up: ask no more. */
  cancelled?: () => boolean;
  /** Waits that long (replaced in tests). */
  wait?: (ms: number) => Promise<void>;
};

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

/** Runs `run`, and again after a pause while it fails with something retryable, at most `retries` more times. */
export async function retrying<T>(run: () => Promise<T>, { retries, retryable, pause = 400, cancelled = () => false, wait = sleep }: Retry): Promise<T> {
  for (let tried = 0; ; tried++) {
    try {
      return await run();
    } catch (error) {
      if (tried >= retries || cancelled() || !retryable(error)) throw error;
      await wait(pause * 3 ** tried);
    }
  }
}
