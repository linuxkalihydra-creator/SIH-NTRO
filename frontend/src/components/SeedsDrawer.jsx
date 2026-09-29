import { useEffect, useRef, useState } from 'react';
import { parseIds } from '../lib/format.js';
import { CopyButton } from './common.jsx';

export default function SeedsDrawer({ seeds, analysed, busy, unknownSeeds, onApply, onClose, onSelectWallet }) {
  const [draft, setDraft] = useState(() => [...seeds]);
  const [text, setText] = useState('');
  const [fileError, setFileError] = useState(null);
  const dialogRef = useRef(null);
  const fileRef = useRef(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);

  useEffect(() => {
    const previous = document.activeElement;
    dialogRef.current?.querySelector('textarea')?.focus();
    const onKey = (event) => {
      if (event.key === 'Escape') onCloseRef.current();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      previous?.focus?.();
    };
  }, []);

  const addIds = (ids) => setDraft((current) => [...new Set([...current, ...ids])]);
  const pasted = parseIds(text);
  const changed = draft.length !== seeds.length || draft.some((id, i) => id !== seeds[i]);
  const unknown = new Set(unknownSeeds);

  const loadFile = async (file) => {
    setFileError(null);
    try {
      const content = await file.text();
      const ids = parseIds(content).filter((id) => !/^(wallet(_id)?|address|id|seed(_wallet)?s?)$/i.test(id));
      if (!ids.length) setFileError(`No wallet ids found in ${file.name}.`);
      addIds(ids);
    } catch {
      setFileError(`Could not read ${file.name}.`);
    }
  };

  const apply = async () => {
    const ok = await onApply(draft);
    if (ok) setText('');
  };

  return (
    <div className="overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="seeds-title" ref={dialogRef}>
        <div className="drawer-head">
          <h2 id="seeds-title">Seed wallets</h2>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close seeds">✕</button>
        </div>
        <p className="muted small">
          Seeds are known-illicit wallets; their risk is propagated through the transaction graph to connected wallets.
        </p>
        {!analysed && (
          <p className="note small">This dataset is not analysed yet — seeds you apply here are sent with the next Analyze run.</p>
        )}

        <label className="field-label" htmlFor="seed-input">Add wallet ids (one per line or comma separated)</label>
        <textarea
          id="seed-input"
          rows={4}
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="bc1q…&#10;1A1zP1…"
          spellCheck={false}
        />
        <div className="row gap wrap">
          <button type="button" className="btn btn-small" disabled={!pasted.length} onClick={() => { addIds(pasted); setText(''); }}>
            Add {pasted.length || ''} id{pasted.length === 1 ? '' : 's'}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".txt,.csv,text/plain,text/csv"
            className="sr-only"
            tabIndex={-1}
            aria-hidden="true"
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = '';
              if (file) loadFile(file);
            }}
          />
          <button type="button" className="btn btn-small" onClick={() => fileRef.current?.click()}>Load .txt / .csv</button>
          {draft.length > 0 && (
            <button type="button" className="btn btn-small btn-ghost" onClick={() => setDraft([])}>Clear all</button>
          )}
        </div>
        {fileError && <p className="error-text small">{fileError}</p>}

        <h3 className="drawer-subhead">Seeds to apply ({draft.length})</h3>
        {draft.length === 0 ? (
          <p className="muted small">No seed wallets. Risk is then based on anomaly, pattern and network signals only.</p>
        ) : (
          <ul className="seed-list">
            {draft.map((id) => (
              <li key={id} className={unknown.has(id) ? 'unknown' : undefined}>
                <button type="button" className="entity-link" onClick={() => onSelectWallet(id)} title={`Investigate ${id}`}>
                  <code>{id}</code>
                </button>
                {unknown.has(id) && <span className="chip chip-warn chip-small">not in dataset</span>}
                <CopyButton text={id} />
                <button type="button" className="icon-button" onClick={() => setDraft((c) => c.filter((s) => s !== id))} aria-label={`Remove seed ${id}`}>✕</button>
              </li>
            ))}
          </ul>
        )}

        {unknownSeeds.length > 0 && (
          <div className="note small" role="status">
            <strong>{unknownSeeds.length} unknown seed(s)</strong> were not found in the dataset and have no effect:
            <ul className="plain-list">
              {unknownSeeds.map((id) => <li key={id}><code>{id}</code></li>)}
            </ul>
          </div>
        )}

        <div className="drawer-foot">
          <button type="button" className="btn" onClick={onClose}>Close</button>
          <button type="button" className="btn btn-primary" onClick={apply} disabled={busy || !changed}>
            {busy ? 'Working…' : analysed ? 'Apply & re-score' : 'Save for analysis'}
          </button>
        </div>
      </aside>
    </div>
  );
}
