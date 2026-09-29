/** Small presentational building blocks used throughout the workspace. */

import { useEffect, useRef, useState } from 'react';
import { apiErrorMessage, isNotAnalysed } from '../api/client.js';
import { fmtPct, isNum, normLevel, truncateId } from '../lib/format.js';

const SHORT_LEVEL = { CRITICAL: 'CRIT', HIGH: 'HIGH', MEDIUM: 'MED', LOW: 'LOW' };

export function RiskBadge({ level, score, compact = false }) {
  const norm = normLevel(level);
  if (!norm && !isNum(score)) return <span className="muted">—</span>;
  return (
    <span className={`risk-badge risk-${(norm || 'none').toLowerCase()}`} title={norm ? `${norm} risk` : undefined}>
      {isNum(score) && <strong>{score.toFixed(1)}</strong>}
      {norm && <span>{compact ? SHORT_LEVEL[norm] : norm}</span>}
    </span>
  );
}

export function ConfidenceMeter({ value, label = 'Confidence' }) {
  if (!isNum(value)) return null;
  const pct = Math.max(0, Math.min(1, value));
  return (
    <span className="confidence" title={`${label}: ${fmtPct(pct)}`}>
      <span className="confidence-label">{label}</span>
      <span className="confidence-track" aria-hidden="true">
        <span className="confidence-fill" style={{ width: `${pct * 100}%` }} />
      </span>
      <span className="confidence-value">{fmtPct(pct)}</span>
    </span>
  );
}

export function Spinner({ label }) {
  return <span className="spinner" role="status" aria-label={label || 'Loading'} />;
}

export function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);

  const copy = async (event) => {
    event.stopPropagation();
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(String(text));
      } else {
        const area = document.createElement('textarea');
        area.value = String(text);
        document.body.appendChild(area);
        area.select();
        document.execCommand('copy');
        area.remove();
      }
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  };

  return (
    <button type="button" className="icon-button copy-button" onClick={copy} title="Copy full id" aria-label={`Copy ${text}`}>
      {copied ? '✓' : '⧉'}
    </button>
  );
}

/** Monospace truncated id with the full value in the tooltip and a copy button. */
export function IdText({ id, head = 8, tail = 6, copy = true }) {
  if (id === null || id === undefined || id === '') return <span className="muted">—</span>;
  return (
    <span className="id-text">
      <code title={String(id)}>{truncateId(id, head, tail)}</code>
      {copy && <CopyButton text={id} />}
    </span>
  );
}

/** A clickable id that navigates the investigation panel. */
export function EntityLink({ type, id, onSelect, level, head = 8, tail = 6, data }) {
  if (id === null || id === undefined || id === '') return <span className="muted">—</span>;
  const norm = normLevel(level);
  return (
    <span className="id-text">
      <button
        type="button"
        className={`entity-link${norm ? ` level-${norm.toLowerCase()}` : ''}`}
        title={`Investigate ${type} ${id}`}
        onClick={(event) => {
          event.stopPropagation();
          onSelect?.({ type, id: String(id), data });
        }}
      >
        {norm && <span className={`dot risk-dot-${norm.toLowerCase()}`} aria-hidden="true" />}
        <code>{truncateId(id, head, tail)}</code>
      </button>
      <CopyButton text={id} />
    </span>
  );
}

export function StatusBox({ kind = 'empty', title, children, action }) {
  return (
    <div className={`status-box status-${kind}`} role={kind === 'error' ? 'alert' : undefined}>
      {kind === 'loading' && <Spinner />}
      <div>
        {title && <p className="status-title">{title}</p>}
        {children && <div className="status-body">{children}</div>}
      </div>
      {action}
    </div>
  );
}

/** Loading / error / empty wrapper around a useApi result. */
export function ApiState({ result, loadingText = 'Loading…', empty, emptyText = 'Nothing to show.', operation, children }) {
  if (result.status === 'idle') return null;
  if (result.status === 'loading') return <StatusBox kind="loading" title={loadingText} />;
  if (result.status === 'error') {
    if (isNotAnalysed(result.error)) {
      return <StatusBox kind="empty" title="Not analysed yet">Run the analysis to generate this view.</StatusBox>;
    }
    return (
      <StatusBox
        kind="error"
        title={`${operation || 'Request'} failed`}
        action={<button type="button" className="btn btn-small" onClick={result.reload}>Retry</button>}
      >
        {apiErrorMessage(result.error, operation)}
      </StatusBox>
    );
  }
  if (empty) return <StatusBox kind="empty" title={emptyText} />;
  return children;
}

export function Tabs({ tabs, active, onChange, label, size = 'normal' }) {
  const refs = useRef({});
  const onKeyDown = (event, index) => {
    if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
    event.preventDefault();
    const next = tabs[(index + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
    onChange(next.id);
    refs.current[next.id]?.focus();
  };
  return (
    <div className={`tabs tabs-${size}`} role="tablist" aria-label={label}>
      {tabs.map((tab, index) => (
        <button
          key={tab.id}
          ref={(node) => { refs.current[tab.id] = node; }}
          type="button"
          role="tab"
          aria-selected={active === tab.id}
          tabIndex={active === tab.id ? 0 : -1}
          className={`tab${active === tab.id ? ' active' : ''}`}
          onClick={() => onChange(tab.id)}
          onKeyDown={(event) => onKeyDown(event, index)}
        >
          {tab.label}
          {tab.count !== undefined && tab.count !== null && <span className="tab-count">{tab.count}</span>}
        </button>
      ))}
    </div>
  );
}

/** Stacked 100% bar of risk levels with a direct-labelled legend. */
export function RiskDistribution({ distribution, label = 'Wallet risk distribution' }) {
  const levels = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'];
  const counts = levels.map((level) => ({ level, count: isNum(distribution?.[level]) ? distribution[level] : 0 }));
  const total = counts.reduce((acc, item) => acc + item.count, 0);
  if (!total) return null;
  return (
    <figure className="risk-dist" aria-label={label}>
      <figcaption>{label}</figcaption>
      <div className="stack-bar" role="img" aria-label={counts.map((c) => `${c.level} ${c.count}`).join(', ')}>
        {counts.filter((c) => c.count > 0).map((c) => (
          <span
            key={c.level}
            className={`stack-seg risk-bg-${c.level.toLowerCase()}`}
            style={{ flexGrow: c.count, minWidth: 3 }}
            title={`${c.level}: ${c.count.toLocaleString()} (${fmtPct(c.count / total, 1)})`}
          />
        ))}
      </div>
      <ul className="legend-row">
        {counts.map((c) => (
          <li key={c.level}>
            <span className={`dot risk-dot-${c.level.toLowerCase()}`} aria-hidden="true" />
            {c.level} <strong>{c.count.toLocaleString()}</strong>
          </li>
        ))}
      </ul>
    </figure>
  );
}
