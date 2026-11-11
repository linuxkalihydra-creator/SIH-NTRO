/**
 * Ranked horizontal bars: where transactions originated, largest first, with the
 * HIGH or CRITICAL part of each bar drawn in the risk colour.
 *
 * rows: [{ key, name, detail?, transactions, flagged }]
 * Every figure is printed beside its bar, so nothing depends on hovering.
 */

import { useState } from 'react';
import { fmtInt, fmtPct } from '../../lib/format.js';
import './charts.css';

export default function RankedBars({ rows, nameHeader = 'Origin', emptyText = 'Nothing to show.' }) {
  const [hover, setHover] = useState(null);
  const list = (Array.isArray(rows) ? rows : [])
    .filter((row) => row && Number(row.transactions) > 0)
    .sort((a, b) => b.transactions - a.transactions || b.flagged - a.flagged);
  if (!list.length) return <p className="muted small">{emptyText}</p>;
  const max = list[0].transactions;

  return (
    <div className="ranked">
      <ul className="legend-row" aria-label="Bar colours">
        <li><span className="swatch series-flagged" aria-hidden="true" />HIGH or CRITICAL risk</li>
        <li><span className="swatch series-all" aria-hidden="true" />other transactions</li>
      </ul>
      <div className="ranked-head" aria-hidden="true">
        <span>{nameHeader}</span>
        <span />
        <span className="num">Flagged</span>
        <span className="num">All</span>
      </div>
      <ol className="ranked-rows">
        {list.map((row) => {
          const flagged = Math.max(0, Math.min(row.flagged || 0, row.transactions));
          const rest = row.transactions - flagged;
          const share = row.transactions ? flagged / row.transactions : 0;
          const label = `${row.name}${row.detail ? ` (${row.detail})` : ''}: ${fmtInt(row.transactions)} transactions originated, ${fmtInt(flagged)} HIGH or CRITICAL (${fmtPct(share, 1)})`;
          return (
            <li
              key={row.key}
              className={`ranked-row${hover === row.key ? ' is-active' : ''}`}
              onPointerEnter={() => setHover(row.key)}
              onPointerLeave={() => setHover(null)}
            >
              <span className="ranked-name" title={row.detail ? `${row.name} · ${row.detail}` : row.name}>
                <strong>{row.name}</strong>
                {row.detail && <span className="muted"> {row.detail}</span>}
              </span>
              <span className="ranked-track" role="img" aria-label={label}>
                <span className="ranked-bar" style={{ width: `${(row.transactions / max) * 100}%` }}>
                  {flagged > 0 && <span className="ranked-seg series-flagged" style={{ flexGrow: flagged }} />}
                  {rest > 0 && <span className="ranked-seg series-all" style={{ flexGrow: rest }} />}
                </span>
                {hover === row.key && (
                  <span className="chart-tip chart-tip-inline" role="status">
                    <span className="chart-tip-head">{row.name}{row.detail ? ` · ${row.detail}` : ''}</span>
                    <span><span className="line-key series-all" aria-hidden="true" /><strong>{fmtInt(row.transactions)}</strong> transactions originated</span>
                    <span><span className="line-key series-flagged" aria-hidden="true" /><strong>{fmtInt(flagged)}</strong> HIGH or CRITICAL ({fmtPct(share, 1)})</span>
                  </span>
                )}
              </span>
              <span className="num ranked-figure">{flagged > 0 ? <strong>{fmtInt(flagged)}</strong> : <span className="muted">0</span>}</span>
              <span className="num ranked-figure">{fmtInt(row.transactions)}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
