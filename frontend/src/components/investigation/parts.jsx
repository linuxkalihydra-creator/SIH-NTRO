/** Building blocks shared by the wallet / transaction / pattern / community views. */

import { Suspense, lazy, useState } from 'react';
import { getGraph } from '../../api/client.js';
import useApi from '../../lib/useApi.js';
import {
  SIGNALS,
  SIGNAL_LABELS,
  asArray,
  fmtNum,
  fmtPct,
  fmtScore,
  fmtTopPct,
  isNum,
} from '../../lib/format.js';
import { ApiState, ConfidenceMeter, CopyButton, RiskBadge, StatusBox, Tabs } from '../common.jsx';

// Cytoscape is large; load it on demand as its own chunk.
const InvestigationGraph = lazy(() => import('../graph/InvestigationGraph.jsx'));

export function Header({ kind, id, score, level, confidence, isSeed, chips, actions, children }) {
  return (
    <div className="inv-header">
      <div className="inv-title">
        <span className="kind-tag">{kind}</span>
        <code className="inv-id" title={id}>{id}</code>
        <CopyButton text={id} />
      </div>
      <div className="inv-meta">
        {(isNum(score) || level) && <RiskBadge score={score} level={level} />}
        <ConfidenceMeter value={confidence} />
        {isSeed && <span className="seed-tag seed-tag-large" title="Known-illicit seed wallet">SEED</span>}
        {chips}
      </div>
      {children}
      {actions && <div className="inv-actions">{actions}</div>}
    </div>
  );
}

export function Section({ title, aside, children }) {
  return (
    <section className="inv-section">
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
    return <p className="muted small">No risk contributions reported.</p>;
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

export function Reasons({ reasons }) {
  const list = asArray(reasons);
  if (!list.length) return <p className="muted small">No reasons reported.</p>;
  return (
    <ol className="reasons">
      {list.map((r, i) => (
        <li key={`${r.label}-${i}`}>
          <span className={`reason-cat cat-${r.category || 'other'}`}>{r.category || 'other'}</span>
          <div className="reason-text">
            <strong>{r.label || '—'}</strong>
            {r.detail && <span className="muted"> — {r.detail}</span>}
          </div>
          {isNum(r.contribution) && <span className="reason-points" title="Risk points">+{r.contribution.toFixed(1)}</span>}
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
      <h4>Detectors</h4>
      <ul className="chip-row">
        {list.map((d, i) => (
          <li
            key={`${d.name}-${i}`}
            className={`detector-chip${d.flagged ? ' flagged' : ''}`}
            title={`${d.name}: percentile ${fmtPct(d.percentile, 1)}${d.flagged ? ' — flagged' : ' — not flagged'}`}
          >
            <span aria-hidden="true">{d.flagged ? '⚑' : '○'}</span>
            <span>{d.name}</span>
            <strong>{isNum(d.percentile) ? `p${(d.percentile * 100).toFixed(1)}` : '—'}</strong>
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
      <h4>Feature attributions <span className="muted small">(share of anomaly score removed when reset to the median)</span></h4>
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

export function WhyFlagged({ detail }) {
  return (
    <Section title="Why flagged">
      <ContributionBar contributions={detail.contributions} signals={detail.signals} riskScore={detail.risk_score} />
      <Reasons reasons={detail.reasons} />
      <Detectors detectors={detail.detectors} />
      <FeatureAttributions attributions={detail.feature_attributions} />
    </Section>
  );
}

export function GraphSection({ datasetId, focusType, focusId, refresh, allowDepth, onSelect }) {
  const [depth, setDepth] = useState(allowDepth ? 1 : undefined);
  const key = `graph|${datasetId}|${focusType}|${focusId}|${depth ?? ''}|${refresh}`;
  const graph = useApi(key, (signal) => getGraph(datasetId, { focusType, focusId, depth }, signal));

  return (
    <Section
      title="Link analysis"
      aside={allowDepth && (
        <label className="inline-field">
          Depth
          <select value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
            <option value={1}>1 hop</option>
            <option value={2}>2 hops</option>
          </select>
        </label>
      )}
    >
      <ApiState result={graph} loadingText="Loading graph…" operation="Loading graph">
        <Suspense fallback={<StatusBox kind="loading" title="Loading graph renderer…" />}>
          <InvestigationGraph key={key} data={graph.data} onSelect={onSelect} />
        </Suspense>
      </ApiState>
    </Section>
  );
}

export function EvidenceTabs({ tabs, initial }) {
  const available = tabs.filter(Boolean);
  const [active, setActive] = useState(initial || available[0]?.id);
  const current = available.find((t) => t.id === active) || available[0];
  if (!current) return null;
  return (
    <Section title="Evidence">
      <Tabs tabs={available} active={current.id} onChange={setActive} label="Evidence" size="small" />
      <div className="evidence-body" role="tabpanel">{current.render()}</div>
    </Section>
  );
}

export function KeyValues({ items }) {
  const rows = items.filter((item) => item && item[1] !== undefined && item[1] !== null && item[1] !== '');
  if (!rows.length) return null;
  return (
    <dl className="kv">
      {rows.map(([k, v]) => (
        <div key={k} className="kv-row">
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}
