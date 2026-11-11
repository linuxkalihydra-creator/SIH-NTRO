/** Small presentational building blocks used throughout the workspace. */

import { useEffect, useRef, useState } from 'react';
import { apiErrorMessage, isNotAnalysed, isNotFound } from '../api/client.js';
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

/** A clickable id that navigates the investigation panel (wallet, transaction, actor, ip, pattern). */
export function EntityLink({ type, id, onSelect, level, head = 8, tail = 6, data, copy = true, children }) {
  if (id === null || id === undefined || id === '') return <span className="muted">—</span>;
  const norm = normLevel(level);
  return (
    <span className="id-text">
      <button
        type="button"
        className={`entity-link${norm ? ` level-${norm.toLowerCase()}` : ''}`}
        title={`Open ${type === 'ip' ? 'IP' : type} ${id}`}
        onClick={(event) => {
          event.stopPropagation();
          onSelect?.({ type, id: String(id), data });
        }}
      >
        {norm && <span className={`dot risk-dot-${norm.toLowerCase()}`} aria-hidden="true" />}
        <code>{children || truncateId(id, head, tail)}</code>
      </button>
      {copy && <CopyButton text={id} />}
    </span>
  );
}

const TAGS = {
  seed: { label: 'SEED', short: 'SEED', className: 'seed-tag', title: 'Known illicit seed wallet supplied by the investigator' },
  'cash-out': { label: 'cash-out point', short: 'cash-out', className: 'tag tag-cashout', title: 'Dirty funds arriving at a service-like entity: the service can identify the depositor' },
  structure: { label: 'in a structure', short: 'structure', className: 'tag tag-structure', title: 'Inside a detected laundering structure' },
  service: { label: 'service-like', short: 'service', className: 'tag tag-service', title: 'Its entity deals with many counterparties (exchange, merchant or processor)' },
};
const TAG_ORDER = Object.keys(TAGS);

/**
 * Lead tags from the backend (`seed`, `cash-out`, `structure`, `service`).
 * `single` shows only the most telling one, for dense lists; its tooltip names the rest.
 */
export function Tags({ tags, skipSeed = false, single = false }) {
  const list = TAG_ORDER.filter((tag) => (Array.isArray(tags) ? tags : []).includes(tag) && !(skipSeed && tag === 'seed'));
  if (!list.length) return null;
  if (single) {
    const [first, ...rest] = list;
    const title = [TAGS[first].title, ...rest.map((tag) => `Also: ${TAGS[tag].label}`)].join('. ');
    return <span className={TAGS[first].className} title={title}>{TAGS[first].short}{rest.length > 0 && <span className="tag-more">+{rest.length}</span>}</span>;
  }
  return list.map((tag) => (
    <span key={tag} className={TAGS[tag].className} title={TAGS[tag].title}>{TAGS[tag].label}</span>
  ));
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
export function ApiState({ result, loadingText = 'Loading…', empty, emptyText = 'Nothing to show.', operation, notFoundText, children }) {
  if (result.status === 'idle') return null;
  if (result.status === 'loading') return <StatusBox kind="loading" title={loadingText} />;
  if (result.status === 'error') {
    if (isNotAnalysed(result.error)) {
      return <StatusBox kind="empty" title="Not analysed yet">Run the analysis to generate this view.</StatusBox>;
    }
    if (notFoundText && isNotFound(result.error)) {
      return <StatusBox kind="empty" title="Not in this dataset">{notFoundText}</StatusBox>;
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

/** Mutually exclusive choice rendered as a row of toggle buttons. */
export function Segmented({ options, value, onChange, label }) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={`segment${option.value === value ? ' active' : ''}`}
          aria-pressed={option.value === value}
          title={option.title}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/**
 * A page section that can be folded away. `open` / `onToggle` are controlled so the
 * state can live in the URL; children are only rendered while it is open.
 */
export function Collapsible({ title, hint, open, onToggle, children, className = '', id }) {
  return (
    <details
      id={id}
      className={`panel fold ${className}`.trim()}
      open={open}
      onToggle={(event) => {
        if (event.currentTarget.open !== open) onToggle(event.currentTarget.open);
      }}
    >
      <summary>
        <h2>{title}</h2>
        {hint && <span className="muted small">{hint}</span>}
      </summary>
      {open && <div className="fold-body">{children}</div>}
    </details>
  );
}

/** Label + large figure + optional sub-line (the stat-tile contract). */
export function Stat({ label, value, sub, title, tone }) {
  return (
    <div className="kpi" title={title}>
      <span className="kpi-label">{label}</span>
      <span className={`kpi-value${tone ? ` kpi-${tone}` : ''}`}>{value}</span>
      {sub && <span className="kpi-sub">{sub}</span>}
    </div>
  );
}

/** Modal confirmation for destructive actions. */
export function ConfirmDialog({ title, children, confirmLabel = 'Confirm', busy = false, onConfirm, onCancel }) {
  const dialogRef = useRef(null);
  const cancelRef = useRef(onCancel);
  useEffect(() => { cancelRef.current = onCancel; }, [onCancel]);
  useEffect(() => {
    const previous = document.activeElement;
    dialogRef.current?.querySelector('button')?.focus();
    const onKey = (event) => {
      if (event.key === 'Escape') cancelRef.current();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      previous?.focus?.();
    };
  }, []);
  return (
    <div className="overlay overlay-center" onMouseDown={(event) => { if (event.target === event.currentTarget) onCancel(); }}>
      <div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" ref={dialogRef}>
        <h2 id="confirm-title">{title}</h2>
        <div className="dialog-body">{children}</div>
        <div className="dialog-foot">
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>Cancel</button>
          <button type="button" className="btn btn-danger-solid" onClick={onConfirm} disabled={busy}>
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
