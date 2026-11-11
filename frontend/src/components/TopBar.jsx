import { useEffect, useId, useRef, useState } from 'react';
import { EXPORT_KINDS, apiErrorMessage, exportUrl, searchLeads } from '../api/client.js';
import useApi from '../lib/useApi.js';
import { useDebounced, useDismiss } from '../lib/hooks.js';
import { LEAD_LABELS, asArray, fmtBytes, fmtPct, isNum, truncateId } from '../lib/format.js';
import AnalysisProgress from './AnalysisProgress.jsx';
import { RiskBadge, Spinner } from './common.jsx';

function HealthChip({ health }) {
  if (health.status === 'loading') return <span className="chip chip-muted">Neo4j: checking…</span>;
  if (health.status === 'error') {
    return <span className="chip chip-bad" title="The API did not answer the health check">API unreachable</span>;
  }
  const data = health.data || {};
  const ok = Boolean(data.graph_available);
  return (
    <span
      className={`chip ${ok ? 'chip-good' : 'chip-muted'}`}
      title={ok
        ? 'Neo4j is connected: analysed graphs are also stored there'
        : 'Neo4j is optional. Without it, link-analysis graphs are served from the stored analysis and nothing is lost.'}
    >
      <span className={`dot ${ok ? 'dot-good' : 'dot-off'}`} aria-hidden="true" />
      Neo4j {ok ? 'online' : 'offline'}
    </span>
  );
}

function datasetLabel(d) {
  const name = d.filename || d.dataset_id;
  const state = d.analysis_status === 'completed' ? '' : ` · ${d.analysis_status === 'running' ? 'analysing' : d.analysis_status === 'failed' ? 'failed' : 'not analysed'}`;
  const records = d.record_count ? ` · ${Number(d.record_count).toLocaleString('en-US')} tx` : '';
  return `${name}${records}${state}`;
}

const SHORT_KIND = { wallet: 'Wallet', transaction: 'Tx', ip: 'IP', actor: 'Actor', pattern: 'Pattern' };

/** The part of a long id around the match, so the reader sees why it was found. */
function snippet(id, needle) {
  const text = String(id);
  const at = text.toLowerCase().indexOf(String(needle).toLowerCase());
  if (at < 0) return { before: truncateId(text, 16, 10), match: '', after: '' };
  const WINDOW = 34;
  if (text.length <= WINDOW) {
    return { before: text.slice(0, at), match: text.slice(at, at + needle.length), after: text.slice(at + needle.length) };
  }
  const start = Math.max(0, Math.min(at - 8, text.length - WINDOW));
  const end = Math.min(text.length, start + WINDOW);
  return {
    before: `${start > 0 ? '…' : ''}${text.slice(start, at)}`,
    match: text.slice(at, Math.min(end, at + needle.length)),
    after: `${text.slice(Math.min(end, at + needle.length), end)}${end < text.length ? '…' : ''}`,
  };
}

/** Typeahead over every wallet, transaction, IP, actor and pattern of the dataset. */
function SearchBox({ datasetId, enabled, onSelect }) {
  const [text, setText] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const boxRef = useRef(null);
  const inputRef = useRef(null);
  const listId = useId();
  const typed = text.trim();
  const query = useDebounced(typed, 180);
  const usable = enabled && query.length >= 2;
  const results = useApi(usable ? `search|${datasetId}|${query}` : null, (signal) => searchLeads(datasetId, query, { limit: 12 }, signal));
  const items = usable && results.status === 'success' ? asArray(results.data) : [];
  const index = Math.max(0, Math.min(active, items.length - 1));
  const pending = typed.length >= 2 && (typed !== query || results.status === 'loading');
  useDismiss(boxRef, open, () => setOpen(false));

  // "/" focuses the search from anywhere outside a form field.
  useEffect(() => {
    const onKey = (event) => {
      if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target;
      if (target instanceof HTMLElement && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))) return;
      if (!inputRef.current || inputRef.current.disabled) return;
      event.preventDefault();
      inputRef.current.focus();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  const choose = (item) => {
    if (!item) return;
    onSelect({ type: item.type, id: String(item.id) });
    setOpen(false);
    setText('');
    inputRef.current?.blur();
  };

  const onKeyDown = (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setOpen(true);
      if (items.length) setActive((index + (event.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length);
    } else if (event.key === 'Enter') {
      if (open && items[index]) {
        event.preventDefault();
        choose(items[index]);
      }
    } else if (event.key === 'Escape') {
      if (open) setOpen(false);
      else inputRef.current?.blur();
    }
  };

  const showPop = open && enabled && typed.length > 0;
  let body = null;
  if (showPop) {
    if (typed.length < 2) {
      body = <p className="search-note">Type at least two characters of a wallet, transaction, IP, actor or pattern id.</p>;
    } else if (results.status === 'error' && !pending) {
      body = <p className="search-note error-text">{apiErrorMessage(results.error, 'Search')}</p>;
    } else if (pending && !items.length) {
      body = <p className="search-note"><Spinner label="Searching" /> Searching…</p>;
    } else if (!items.length) {
      body = <p className="search-note">Nothing in this dataset has “{query}” in its id.</p>;
    } else {
      body = (
        <ul id={listId} role="listbox" aria-label="Search results" className="search-results">
          {items.map((item, i) => {
            const parts = snippet(item.id, query);
            const plain = LEAD_LABELS[item.type] || item.type;
            const extra = item.label && item.label !== plain ? item.label : '';
            return (
              <li
                key={`${item.type}:${item.id}`}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={i === index}
                className={`search-result${i === index ? ' active' : ''}`}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(item)}
              >
                <span className="kind-tag" title={plain}>{SHORT_KIND[item.type] || plain}</span>
                <span className="search-main">
                  <code>{parts.before}<mark>{parts.match}</mark>{parts.after}</code>
                  {extra && <span className="search-extra muted" title={extra}>{extra}</span>}
                </span>
                {item.type === 'pattern'
                  ? (isNum(item.risk_score) && <span className="muted small nowrap">strength {fmtPct(item.risk_score / 100)}</span>)
                  : <RiskBadge score={item.risk_score} level={item.risk_level} compact />}
              </li>
            );
          })}
        </ul>
      );
    }
  }

  return (
    <div className="search-box" ref={boxRef} role="search">
      <input
        ref={inputRef}
        type="search"
        role="combobox"
        aria-label="Search the whole dataset"
        aria-expanded={Boolean(showPop && items.length)}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showPop && items.length ? `${listId}-${index}` : undefined}
        placeholder={enabled ? 'Search wallet, transaction, IP, actor…' : 'Search (analyse a dataset first)'}
        value={text}
        disabled={!enabled}
        autoComplete="off"
        spellCheck={false}
        onChange={(event) => { setText(event.target.value); setActive(0); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
      />
      {enabled && !text && <kbd className="search-key" aria-hidden="true">/</kbd>}
      {body && <div className="search-pop">{body}</div>}
    </div>
  );
}

const EXPORT_LABELS = {
  wallets: 'Wallet leads',
  transactions: 'Transaction leads',
  actors: 'Actor leads',
  ips: 'IP leads',
  patterns: 'Detected patterns',
};

/** Ranked leads of each kind as CSV; plain links, so the browser saves the file. */
function ExportMenu({ datasetId, enabled }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useDismiss(ref, open, () => setOpen(false));
  return (
    <div className="menu" ref={ref}>
      <button
        type="button"
        className="btn"
        aria-haspopup="true"
        aria-expanded={open}
        disabled={!enabled}
        title={enabled ? 'Download the ranked leads as spreadsheets' : 'Analyse the dataset to export its leads'}
        onClick={() => setOpen((value) => !value)}
      >
        Export <span aria-hidden="true" className="caret">▾</span>
      </button>
      {open && (
        <div className="menu-pop">
          <p className="menu-title">Ranked leads, CSV</p>
          <ul>
            {EXPORT_KINDS.map((kind) => (
              <li key={kind}>
                <a href={exportUrl(datasetId, kind)} download={`leads_${kind}.csv`} onClick={() => setOpen(false)}>
                  <span>{EXPORT_LABELS[kind] || kind}</span>
                  <span className="muted small">leads_{kind}.csv</span>
                </a>
              </li>
            ))}
          </ul>
          <p className="menu-note">Evidence sheets for single leads are in the investigation panel.</p>
        </div>
      )}
    </div>
  );
}

export default function TopBar({
  datasets,
  datasetId,
  onDatasetChange,
  onUploadClick,
  onAnalyze,
  onOpenSeeds,
  onDelete,
  onSelect,
  seedCount,
  busy,
  job,
  analysing,
  analysingThis,
  health,
  analysed,
}) {
  const current = datasets.find((d) => d.dataset_id === datasetId);
  const working = Boolean(busy);

  // The bar wraps on narrow windows and grows while an analysis runs; sticky panels below follow its real height.
  const barRef = useRef(null);
  useEffect(() => {
    const node = barRef.current;
    if (!node) return undefined;
    const apply = () => document.documentElement.style.setProperty('--topbar-h', `${Math.round(node.getBoundingClientRect().height)}px`);
    apply();
    if (!window.ResizeObserver) return undefined;
    const observer = new ResizeObserver(apply);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <header className="topbar" ref={barRef}>
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">₿</span>
        <span className="brand-name">Bitcoin Investigation Platform</span>
      </div>

      <SearchBox key={datasetId || 'none'} datasetId={datasetId} enabled={Boolean(datasetId && analysed)} onSelect={onSelect} />

      <div className="topbar-controls">
        <div className="control-group">
          <label className="dataset-select">
            <span className="sr-only">Dataset</span>
            <select
              value={datasetId || ''}
              onChange={(event) => onDatasetChange(event.target.value)}
              disabled={!datasets.length || working}
              title={current ? `${current.filename} (${fmtBytes(current.size_bytes)}) · ${current.dataset_id}` : 'No dataset'}
            >
              {!datasets.length && <option value="">No datasets</option>}
              {datasets.map((d) => (
                <option key={d.dataset_id} value={d.dataset_id}>{datasetLabel(d)}</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn btn-ghost btn-danger"
            onClick={onDelete}
            disabled={!current || working || analysingThis}
            title={analysingThis ? 'Wait for the analysis to finish before deleting' : 'Delete this dataset and its stored analysis'}
          >
            Delete
          </button>
        </div>

        <div className="control-group">
          <button type="button" className="btn" onClick={onUploadClick} disabled={working} title="CSV, TSV, JSON, JSON Lines or XML, optionally gzip-compressed">
            Upload dataset
          </button>
          <button type="button" className="btn" onClick={onOpenSeeds} disabled={!datasetId} title="Known illicit wallets to trace taint from">
            Seeds <span className="count-pill">{seedCount}</span>
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={onAnalyze}
            disabled={!datasetId || working || analysing}
            title={analysed ? 'Run the whole pipeline again on this dataset' : 'Run the pipeline: ingestion, correlation, models, scoring'}
          >
            {analysed ? 'Re-analyze' : 'Analyze'}
          </button>
          <ExportMenu key={datasetId || 'none'} datasetId={datasetId} enabled={Boolean(datasetId && analysed)} />
        </div>

        {busy && (
          <span className="busy-indicator" role="status">
            <Spinner /> {busy.label}
          </span>
        )}
        <HealthChip health={health} />
      </div>

      {job && <AnalysisProgress job={job} compact />}
    </header>
  );
}
