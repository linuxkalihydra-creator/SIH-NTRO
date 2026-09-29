import { useEffect, useRef, useState } from 'react';
import { Spinner } from './common.jsx';
import { fmtBytes } from '../lib/format.js';

function Elapsed({ startedAt }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));
  return <span className="elapsed">{Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, '0')}</span>;
}

function HealthChip({ health }) {
  if (health.status === 'loading') return <span className="chip chip-muted">Neo4j: checking…</span>;
  if (health.status === 'error') {
    return <span className="chip chip-bad" title="GET /health failed">API unreachable</span>;
  }
  const data = health.data || {};
  const ok = Boolean(data.graph_available);
  return (
    <span
      className={`chip ${ok ? 'chip-good' : 'chip-warn'}`}
      title={`API: ${data.status || 'unknown'} · Neo4j: ${data.graph_status || (ok ? 'available' : 'unavailable')}`}
    >
      <span className={`dot ${ok ? 'dot-good' : 'dot-warn'}`} aria-hidden="true" />
      Neo4j {ok ? 'online' : 'offline'}
    </span>
  );
}

function datasetLabel(d) {
  const name = d.filename || d.dataset_id;
  const state = d.analysis_status === 'completed' ? '' : ` · ${d.status || d.analysis_status || 'not analysed'}`;
  const records = d.record_count ? ` · ${Number(d.record_count).toLocaleString()} rec` : '';
  return `${name}${records}${state}`;
}

export default function TopBar({
  datasets,
  datasetId,
  onDatasetChange,
  onUpload,
  onAnalyze,
  onOpenSeeds,
  seedCount,
  busy,
  health,
  canAnalyze,
}) {
  const fileRef = useRef(null);
  const current = datasets.find((d) => d.dataset_id === datasetId);

  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">₿</span>
        <span className="brand-name">Bitcoin Investigation Platform</span>
      </div>

      <div className="topbar-controls">
        <label className="dataset-select">
          <span className="sr-only">Dataset</span>
          <select
            value={datasetId || ''}
            onChange={(event) => onDatasetChange(event.target.value)}
            disabled={!datasets.length || Boolean(busy)}
            title={current ? `${current.filename} (${fmtBytes(current.size_bytes)}) — ${current.dataset_id}` : 'No dataset'}
          >
            {!datasets.length && <option value="">No datasets</option>}
            {datasets.map((d) => (
              <option key={d.dataset_id} value={d.dataset_id}>{datasetLabel(d)}</option>
            ))}
          </select>
        </label>

        <input
          ref={fileRef}
          type="file"
          accept=".csv,.json,.xml"
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = '';
            if (file) onUpload(file);
          }}
        />
        <button type="button" className="btn" onClick={() => fileRef.current?.click()} disabled={Boolean(busy)}>
          Upload dataset
        </button>
        <button type="button" className="btn" onClick={onOpenSeeds} disabled={!canAnalyze}>
          Seeds <span className="count-pill">{seedCount}</span>
        </button>
        <button
          type="button"
          className="btn btn-primary"
          onClick={onAnalyze}
          disabled={!canAnalyze || Boolean(busy)}
          aria-busy={busy?.kind === 'analyze'}
        >
          {busy?.kind === 'analyze' ? <><Spinner label="Analysing" /> Analysing…</> : 'Analyze'}
        </button>
        {busy && (
          <span className="busy-indicator" role="status">
            {busy.kind !== 'analyze' && <Spinner />}
            {busy.label} <Elapsed startedAt={busy.startedAt} />
          </span>
        )}
        <HealthChip health={health} />
      </div>
    </header>
  );
}
