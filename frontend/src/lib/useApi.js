import { useCallback, useEffect, useRef, useState } from 'react';
import { isCancelled } from '../api/client.js';

/**
 * Runs `fetcher(signal)` whenever `key` changes (null/undefined key = idle).
 * Returns {status: 'idle'|'loading'|'success'|'error', data, error, reload}.
 * Results are tagged with the key they belong to, so a new key never shows
 * data from the previous one.
 */
export default function useApi(key, fetcher) {
  const fetcherRef = useRef(fetcher);
  const [nonce, setNonce] = useState(0);
  const [result, setResult] = useState({ key: null, nonce: -1, status: 'idle', data: null, error: null });

  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    if (key === null || key === undefined) return undefined;
    const controller = new AbortController();
    Promise.resolve()
      .then(() => fetcherRef.current(controller.signal))
      .then((data) => {
        if (!controller.signal.aborted) setResult({ key, nonce, status: 'success', data, error: null });
      })
      .catch((error) => {
        if (controller.signal.aborted || isCancelled(error)) return;
        setResult({ key, nonce, status: 'error', data: null, error });
      });
    return () => controller.abort();
  }, [key, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  if (key === null || key === undefined) return { status: 'idle', data: null, error: null, reload };
  if (result.key !== key || result.nonce !== nonce) return { status: 'loading', data: null, error: null, reload };
  return { ...result, reload };
}
