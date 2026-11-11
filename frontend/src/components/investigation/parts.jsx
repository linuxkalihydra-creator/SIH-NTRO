/** Building blocks shared by the wallet / transaction / actor / IP / pattern / community views. */

import { Suspense, lazy } from 'react';
import { getGraph, reportUrl } from '../../api/client.js';
import useApi from '../../lib/useApi.js';
import { GRAPH_MODES } from '../../lib/hash.js';
import {
  SIGNALS,
  SIGNAL_LABELS,
  asArray,
  fmtAsn,
  fmtNum,
  fmtPct,
  fmtScore,
  fmtTopPct,
  isNum,
  isStandardPort,
  patternLabel,
  roleLabel,
} from '../../lib/format.js';
import { ApiState, ConfidenceMeter, CopyButton, EntityLink, RiskBadge, Segmented, StatusBox, Tabs } from '../common.jsx';
import DataTable from '../DataTable.jsx';

// Cytoscape is large; load it on demand as its own chunk.
const InvestigationGraph = lazy(() => import('../graph/InvestigationGraph.jsx'));

export function Header({ kind, id, score, level, confidence, confidenceLabel, isSeed, chips, actions, children }) {
  return (
    <div className="inv-header">
      <div className="inv-title">
        <span className="kind-tag">{kind}</span>
        <code className="inv-id" title={id}>{id}</code>
        <CopyButton text={id} />
      </div>
      <div className="inv-meta">
        {(isNum(score) || level) && <RiskBadge score={score} level={level} />}
        <ConfidenceMeter value={confidence} label={confidenceLabel} />
        {isSeed && <span className="seed-tag seed-tag-large" title="Known illicit seed wallet supplied by the investigator">SEED</span>}
        {chips}
      </div>
      {children}
      {actions && <div className="inv-actions">{actions}</div>}
    </div>
  );
}

/** Download link for the Markdown evidence sheet of one lead. */
export function ReportLink({ datasetId, kind, id }) {
  return (
    <a
      className="btn btn-small"
      href={reportUrl(datasetId, kind, id)}
      download
      title="Download this lead as a Markdown evidence sheet for the case file"
    >
      <span aria-hidden="true">↓</span> Evidence sheet
    </a>
  );
}

export function Section({ title, aside, children, id }) {
  return (
    <section className="inv-section" id={id}>
      <div className="inv-section-head">
        <h3>{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

/** Stacked horizontal bar of risk points per signal (sums to risk_score out of 100). */
export function ContributionBar({ contributions, signals, riskScore }) {
  const parts = SIGNALS.map((key) => ({
    key,
    points: isNum(contributions?.[key]) ? Math.max(0, contributions[key]) : 0,
    signal: signals?.[key],
  }));
  const total = parts.reduce((acc, p) => acc + p.points, 0);
  if (!contributions || total <= 0) {
    return <p className="muted small">No signal contributes risk points to this lead.</p>;
  }
  const scale = Math.max(100, total);
  return (
    <figure className="contrib">
      <div
        className="contrib-bar"
        role="img"
        aria-label={`Risk ${fmtScore(riskScore ?? total)} of 100: ${parts.map((p) => `${SIGNAL_LABELS[p.key]} ${p.points.toFixed(1)} points`).join(', ')}`}
      >
        {parts.filter((p) => p.points > 0).map((p) => (
          <span
            key={p.key}
            className={`contrib-seg sig-${p.key}`}
            style={{ width: `${(p.points / scale) * 100}%` }}
            title={`${SIGNAL_LABELS[p.key]}: ${p.points.toFixed(1)} pts${isNum(p.signal) ? ` (signal ${fmtNum(p.signal, 2)})` : ''}`}
          >
            {p.points / scale >= 0.1 && <span className="contrib-seg-label">{p.points.toFixed(1)}</span>}
          </span>
        ))}
      </div>
      <div className="contrib-scale" aria-hidden="true"><span>0</span><span>50</span><span>100</span></div>
      <ul className="legend-row">
        {parts.map((p) => (
          <li key={p.key}>
            <span className={`dot sig-${p.key}`} aria-hidden="true" />
            {SIGNAL_LABELS[p.key]} <strong>{p.points.toFixed(1)}</strong> pts
            {isNum(p.signal) && <span className="muted"> · signal {fmtNum(p.signal, 2)}</span>}
          </li>
        ))}
      </ul>
    </figure>
  );
}

export function Reasons({ reasons, onSelect }) {
  const list = asArray(reasons);
  if (!list.length) return <p className="muted small">No individual reasons were recorded for this lead.</p>;
  return (
    <ol className="reasons">
      {list.map((r, i) => (
        <li key={`${r.label}-${i}`}>
          <span className={`reason-cat cat-${r.category || 'other'}`}>{r.category || 'other'}</span>
          <div className="reason-text">
            <strong>{r.label || '—'}</strong>
            {r.detail && <span className="muted"> — {r.detail}</span>}
            {r.pattern_id && onSelect && (
              <>
                {' '}
                <button type="button" className="link-button" onClick={() => onSelect({ type: 'pattern', id: String(r.pattern_id) })}>
                  open pattern
                </button>
              </>
            )}
          </div>
          {isNum(r.contribution) && r.contribution > 0 && (
            <span className="reason-points" title="Risk points this reason adds">+{r.contribution.toFixed(1)}</span>
          )}
        </li>
      ))}
    </ol>
  );
}

export function Detectors({ detectors }) {
  const list = asArray(detectors);
  if (!list.length) return null;
  return (
    <div className="detectors">
      <h4>Anomaly detectors</h4>
      <ul className="chip-row">
        {list.map((d, i) => (
          <li
            key={`${d.name}-${i}`}
            className={`detector-chip${d.flagged ? ' flagged' : ''}`}
            title={`${d.name}: more unusual than ${fmtPct(d.percentile, 1)} of the population${d.flagged ? ' (flagged: top 5%)' : ''}`}
          >
            <span aria-hidden="true">{d.flagged ? '⚑' : '○'}</span>
            <span>{d.name}</span>
            <strong>{fmtTopPct(d.percentile)}</strong>
            <span className="sr-only">{d.flagged ? 'flagged' : 'not flagged'}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function FeatureAttributions({ attributions }) {
  const list = asArray(attributions);
  if (!list.length) return null;
  const max = Math.max(...list.map((a) => (isNum(a.contribution) ? a.contribution : 0)), 0.0001);
  return (
    <div className="attributions">
      <h4>What makes the behaviour unusual <span className="muted small">(share of the anomaly score that disappears when the feature is reset to the median)</span></h4>
      <div className="table-wrap">
        <table className="data-table">
          <caption className="sr-only">Feature attributions</caption>
          <thead>
            <tr>
              <th scope="col">Feature</th>
              <th scope="col" className="num">Value</th>
              <th scope="col" className="num">Percentile</th>
              <th scope="col">Contribution</th>
            </tr>
          </thead>
          <tbody>
            {list.map((a, i) => (
              <tr key={`${a.feature}-${i}`}>
                <td title={a.feature}>{a.label || a.feature}</td>
                <td className="num">{isNum(a.value) ? fmtNum(a.value, 4) : String(a.value ?? '—')}</td>
                <td className="num">{fmtTopPct(a.percentile)}</td>
                <td>
                  <span className="attr-bar">
                    <span className="attr-track" aria-hidden="true">
                      <span className="attr-fill" style={{ width: `${(Math.max(0, a.contribution || 0) / max) * 100}%` }} />
                    </span>
                    <span className="attr-value">{fmtPct(a.contribution, 0)}</span>
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/**
 * Score breakdown, reasons, detectors and feature attributions.
 * `scopeNote` says whose behaviour the detectors describe when that is not the lead itself.
 */
export function WhyFlagged({ detail, onSelect, scopeNote, title = 'Why flagged' }) {
  const hasModelOutput = asArray(detail.detectors).length > 0 || asArray(detail.feature_attributions).length > 0;
  return (
    <Section title={title}>
      {detail.contributions && (
        <ContributionBar contributions={detail.contributions} signals={detail.signals} riskScore={detail.risk_score} />
      )}
      <Reasons reasons={detail.reasons} onSelect={onSelect} />
      {scopeNote && hasModelOutput && <p className="note small">{scopeNote}</p>}
      <Detectors detectors={detail.detectors} />
      <FeatureAttributions attributions={detail.feature_attributions} />
    </Section>
  );
}

const MODE_OPTIONS = [
  { value: 'neighbourhood', label: 'Neighbourhood', title: 'Every transaction this wallet took part in' },
  { value: 'downstream', label: 'Follow the money', title: 'Where the funds went: the largest outputs, forward in time' },
  { value: 'upstream', label: 'Where it came from', title: 'Where the funds came from: the largest inputs, backward in time' },
];

const TRACE_HOPS = { 1: 3, 2: 6, 3: 8 };

function depthChoices(focusType, mode) {
  if (focusType === 'wallet') {
    if (mode === 'neighbourhood') return [[1, '1 hop'], [2, '2 hops'], [3, '3 hops']];
    return [1, 2, 3].map((depth) => [depth, `${TRACE_HOPS[depth]} hops`]);
  }
  if (focusType === 'transaction') return [[1, 'This transaction'], [2, 'With previous and next spends']];
  return null;
}

function graphCaption(focusType, mode, depth) {
  if (focusType === 'wallet') {
    if (mode === 'downstream') {
      return `Follows the largest outputs forward in time for up to ${TRACE_HOPS[depth] || 3} hops (three per step). The trace stops at service-like wallets: a service pools the funds of many users, so following it further says nothing about this money.`;
    }
    if (mode === 'upstream') {
      return `Follows the largest inputs backward in time for up to ${TRACE_HOPS[depth] || 3} hops (three per step). The trace stops at service-like wallets, which pool the funds of many users.`;
    }
    return depth > 1
      ? 'Every transaction this wallet took part in, then the riskiest transactions of the wallets it dealt with. IPs are shown for the wallet’s own transactions.'
      : 'Every transaction this wallet took part in (the 25 riskiest for a hub), with the IP each one was first seen from.';
  }
  if (focusType === 'transaction') {
    return depth > 1
      ? 'Inputs, outputs and every IP that announced this transaction, plus where the inputs came from and where the outputs were spent next.'
      : 'Inputs, outputs and every IP that announced this transaction: the first sighting and the later relays.';
  }
  if (focusType === 'pattern') {
    return 'Every transaction of this structure, with its wallets and first-seen IPs. Follow the arrows: the largest flow runs along the top, and a long chain continues on the row below in the opposite direction.';
  }
  if (focusType === 'cluster') return 'The 30 riskiest wallets of this community and the transactions that connect them.';
  if (focusType === 'actor') return 'The actor’s wallets (the 40 riskiest), their transactions, direct counterparties and the IPs they broadcast from.';
  if (focusType === 'ip') return 'The 30 riskiest transactions first seen from this IP, with the wallets that spent in them and their largest outputs.';
  return null;
}

/**
 * Link-analysis graph of one lead. Mode (wallets) and depth live in `view`, which the
 * workspace keeps in the URL, so a shared link reopens the same trace.
 */
export function GraphSection({ datasetId, focusType, focusId, refresh, onSelect, view, onViewChange }) {
  const withModes = focusType === 'wallet';
  const mode = withModes && GRAPH_MODES.includes(view?.graph) ? view.graph : 'neighbourhood';
  const choices = depthChoices(focusType, mode);
  const depth = choices ? Math.min(choices.length, Math.max(1, Number(view?.depth) || 1)) : undefined;
  const key = `graph|${datasetId}|${focusType}|${focusId}|${mode}|${depth ?? ''}|${refresh}`;
  const graph = useApi(key, (signal) => getGraph(datasetId, { focusType, focusId, depth, mode: withModes ? mode : undefined }, signal));
  const flow = focusType === 'transaction' || focusType === 'pattern' || (withModes && mode !== 'neighbourhood');
  const caption = graphCaption(focusType, mode, depth);

  return (
    <Section
      title="Link analysis"
      aside={(withModes || choices) && (
        <div className="graph-controls">
          {withModes && (
            <Segmented options={MODE_OPTIONS} value={mode} onChange={(next) => onViewChange?.({ graph: next })} label="What the graph shows" />
          )}
          {choices && (
            <label className="inline-field">
              {focusType === 'transaction' ? 'Show' : 'Depth'}
              <select value={depth} onChange={(event) => onViewChange?.({ depth: Number(event.target.value) })}>
                {choices.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
          )}
        </div>
      )}
    >
      {caption && <p className="muted small">{caption}</p>}
      <div className="graph-slot">
        <ApiState result={graph} loadingText="Loading graph…" operation="Loading graph" notFoundText="This lead is not part of the current analysis.">
          <Suspense fallback={<StatusBox kind="loading" title="Loading graph renderer…" />}>
            <InvestigationGraph key={key} data={graph.data} layout={flow ? 'flow' : 'force'} onSelect={onSelect} />
          </Suspense>
        </ApiState>
      </div>
    </Section>
  );
}

/** Evidence tabs; the active tab is controlled so it can be part of a shared link. */
export function EvidenceTabs({ tabs, active, onChange, title = 'Evidence' }) {
  const available = tabs.filter(Boolean);
  const current = available.find((tab) => tab.id === active) || available[0];
  if (!current) return null;
  return (
    <Section title={title} id="evidence">
      <Tabs tabs={available} active={current.id} onChange={(id) => onChange?.(id)} label={title} size="small" />
      <div className="evidence-body" role="tabpanel" aria-label={typeof current.label === 'string' ? current.label : undefined}>
        {current.render()}
      </div>
    </Section>
  );
}

export function KeyValues({ items }) {
  const rows = items.filter((item) => item && item[1] !== undefined && item[1] !== null && item[1] !== '');
  if (!rows.length) return null;
  return (
    <dl className="kv">
      {rows.map(([k, v, title]) => (
        <div key={k} className="kv-row" title={title}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Every model feature of a lead, for readers who want the raw inputs. */
/** `labels` maps a feature's column name to its readable name (sent with the detail). */
export function FeatureList({ features, labels }) {
  const entries = Object.entries(features || {});
  if (!entries.length) return <p className="muted small">No model features were stored for this lead.</p>;
  const name = (key) => labels?.[key] || key.replace(/_/g, ' ');
  return (
    <KeyValues items={entries.map(([key, value]) => [name(key), isNum(value) ? fmtNum(value, 4) : String(value)])} />
  );
}

export function IpLink({ ip, level, onSelect, copy = false }) {
  if (!ip) return <span className="muted">—</span>;
  return <EntityLink type="ip" id={ip} level={level} onSelect={onSelect} head={24} tail={12} copy={copy} />;
}

export function ActorLink({ id, level, onSelect }) {
  if (!id) return <span className="muted">—</span>;
  return <EntityLink type="actor" id={id} level={level} onSelect={onSelect} head={14} tail={6} copy={false} />;
}

/**
 * Why an entity was put in its actor: the two clues that agreed, in the backend's words,
 * followed by the identifiers behind them (the shared IP is absent when payment and
 * deposit were the two clues).
 */
export function ActorLinkEvidence({ link, onSelect, lead }) {
  if (!link) return null;
  const description = link.description || 'two independent clues';
  return (
    <span className="link-evidence">
      <span>{lead} <strong>{link.with || 'another entity'}</strong>. Clues that agree: {description}.</span>
      <span className="link-evidence-ids">
        {link.ip && <span><span className="muted">shared IP</span> <IpLink ip={link.ip} onSelect={onSelect} /></span>}
        {link.txid && (
          <span>
            <span className="muted">{link.kind === 'deposit' ? 'a deposit' : 'the payment'}</span>{' '}
            <EntityLink type="transaction" id={link.txid} onSelect={onSelect} head={6} tail={4} copy={false} />
          </span>
        )}
        {link.address && (
          <span>
            <span className="muted">same deposit address</span>{' '}
            <EntityLink type="wallet" id={link.address} onSelect={onSelect} head={8} tail={5} copy={false} />
          </span>
        )}
      </span>
    </span>
  );
}

/** "DE · AS24940 Hetzner Online GmbH" (any part may be missing). */
export function Where({ country, asn, org }) {
  const network = fmtAsn(asn, org);
  const parts = [country || null, network !== '—' ? network : null].filter(Boolean);
  if (!parts.length) return <span className="muted">—</span>;
  return <span className="where" title={parts.join(' · ')}>{parts.join(' · ')}</span>;
}

/** Listening ports with how often each was used; anything but the Bitcoin port is highlighted. */
export function PortChips({ ports }) {
  const list = asArray(ports);
  if (!list.length) return null;
  return (
    <ul className="chip-row" aria-label="Ports used">
      {list.map((p) => {
        const standard = isStandardPort(p.port);
        return (
          <li
            key={p.port}
            className={`chip chip-small${standard ? '' : ' chip-warn'}`}
            title={standard ? 'Standard Bitcoin port' : Number(p.port) > 0 ? 'Non-standard port' : 'Port not recorded'}
          >
            <code>{Number(p.port) > 0 ? `:${p.port}` : 'unknown'}</code> × {p.count}
          </li>
        );
      })}
    </ul>
  );
}

/** Detected structures a lead is part of; `withRole` adds what the wallet does inside each one. */
export function PatternList({ patterns, onSelect, withRole = false, emptyText = 'Not part of any detected structure.' }) {
  const list = asArray(patterns).map((p) => (typeof p === 'string' ? { type: p } : p)).filter(Boolean);
  if (!list.length) return <p className="muted small">{emptyText}</p>;
  const withId = list.filter((p) => p.pattern_id !== undefined && p.pattern_id !== null);
  const typeOnly = list.filter((p) => p.pattern_id === undefined || p.pattern_id === null);
  return (
    <>
      {withId.length > 0 && (
        <DataTable
          caption="Detected structures"
          maxHeight={320}
          columns={[
            { key: 'type', label: 'Type', sort: (p) => p.type, render: (p) => <span className="chip chip-small">{patternLabel(p.type)}</span> },
            withRole && { key: 'role', label: 'Role of this wallet', sort: (p) => p.role, render: (p) => (p.role ? roleLabel(p.role) : '—') },
            { key: 'strength', label: 'Strength', className: 'num', sort: (p) => p.strength, render: (p) => fmtPct(p.strength) },
            { key: 'summary', label: 'Summary', render: (p) => <span className="reason-cell" title={p.summary}>{p.summary || '—'}</span> },
          ].filter(Boolean)}
          rows={withId}
          rowKey={(p, i) => `${p.pattern_id}-${p.role || ''}-${i}`}
          onRowClick={(p) => onSelect({ type: 'pattern', id: String(p.pattern_id), data: p })}
        />
      )}
      {typeOnly.length > 0 && (
        <div className="chip-row">
          {typeOnly.map((p, i) => <span key={`${p.type}-${i}`} className="chip chip-small">{patternLabel(p.type)}</span>)}
        </div>
      )}
    </>
  );
}
