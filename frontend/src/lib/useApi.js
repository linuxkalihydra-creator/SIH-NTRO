import { useCallback, useEffect, useRef, useState } from 'react';
import { isCancelled } from '../api/client.js';

/**
 * Runs `fetcher(signal)` whenever `key` changes (null/undefined key = idle).
 * Returns {status: 'idle'|'loading'|'success'|'error', data, error, reload, refreshing}.
 * Results are tagged with the key they belong to, so a new key never shows
 * data from the previous one.
 *
 * The one exception is opt-in: pass `keepWithin` (a string naming what the data
 * is "about", for example the dataset and list) and, while a new key with the same
 * `keepWithin` is loading, the previous rows stay on screen with `refreshing: true`.
 * Use it for lists whose filters are applied by the server.
 */
export default function useApi(key, fetcher, { keepWithin = null } = {}) {
  const fetcherRef = useRef(fetcher);
  const [nonce, setNonce] = useState(0);
  const [result, setResult] = useState({ key: null, nonce: -1, status: 'idle', data: null, error: null, within: null });

  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    if (key === null || key === undefined) return undefined;
    const controller = new AbortController();
    Promise.resolve()
      .then(() => fetcherRef.current(controller.signal))
      .then((data) => {
        if (!controller.signal.aborted) setResult({ key, nonce, status: 'success', data, error: null, within: keepWithin });
      })
      .catch((error) => {
        if (controller.signal.aborted || isCancelled(error)) return;
        setResult({ key, nonce, status: 'error', data: null, error, within: keepWithin });
      });
    return () => controller.abort();
    // `keepWithin` only labels the result; a change of it always comes with a change of `key`.
  }, [key, nonce, keepWithin]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  if (key === null || key === undefined) return { status: 'idle', data: null, error: null, reload, refreshing: false };
  if (result.key !== key || result.nonce !== nonce) {
    if (keepWithin && result.within === keepWithin && result.status === 'success') {
      return { status: 'success', data: result.data, error: null, reload, refreshing: true };
    }
    return { status: 'loading', data: null, error: null, reload, refreshing: false };
  }
  return { status: result.status, data: result.data, error: result.error, reload, refreshing: false };
}
