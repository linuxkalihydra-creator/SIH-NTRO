/**
 * Activity over time as two small multiples that share one time axis:
 * all transactions per period, and the ones scored HIGH or CRITICAL.
 *
 * Two panels rather than two y-scales on one plot: the counts differ by orders of
 * magnitude, and a dual axis would invent a relationship between them.
 * Hover (or focus + arrow keys) moves one crosshair through both panels and
 * shows every figure for that period; the table view below lists the same data.
 */

import { useMemo, useRef, useState } from 'react';
import { fmtBtc, fmtCompact, fmtInt } from '../../lib/format.js';
import { useWidth } from '../../lib/hooks.js';
import './charts.css';

const LEFT = 46;
const RIGHT = 10;
const TOP = 16;
const PLOT = 100;
const AXIS_BAND = 22;
const MAX_BAR = 24;
const GAP = 2;
const DAY = 86400000;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const PANELS = [
  { key: 'transactions', title: 'All transactions', series: 'all' },
  { key: 'flagged', title: 'HIGH or CRITICAL risk transactions', series: 'flagged' },
];

/** Up to four whole-number ticks from zero; the last one is the top of the scale. */
function niceTicks(maxValue) {
  if (!(maxValue > 0)) return [0, 1];
  const power = 10 ** Math.floor(Math.log10(maxValue));
  for (const multiple of [0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10]) {
    const step = multiple * power;
    if (step < 1 || !Number.isInteger(step)) continue;
    const intervals = Math.ceil(maxValue / step - 1e-9);
    if (intervals <= 3) return Array.from({ length: intervals + 1 }, (_, index) => index * step);
  }
  return [0, maxValue];
}

/** Column with a rounded data end and a square foot on the baseline. */
function columnPath(x, y, width, height) {
  const r = Math.min(4, width / 2, height);
  return `M${x},${y + height} V${y + r} Q${x},${y} ${x + r},${y} H${x + width - r} Q${x + width},${y} ${x + width},${y + r} V${y + height} Z`;
}

const pad = (value) => String(value).padStart(2, '0');

function dayLabel(ms, withYear = false) {
  const date = new Date(ms);
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]}${withYear ? ` ${date.getUTCFullYear()}` : ''}`;
}

/** "day" · "3 days" · "hour" · "6 hours" */
function periodName(width) {
  if (width >= DAY) return width > DAY ? `${Math.round(width / DAY)} days` : 'day';
  const hours = Math.max(1, Math.round(width / 3600000));
  return hours === 1 ? 'hour' : `${hours} hours`;
}

const clock = (ms) => {
  const date = new Date(ms);
  return `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
};

/** A period in words. Buckets start at the first transaction, so a "day" rarely starts at midnight. */
function periodLabel(start, width) {
  if (width >= DAY) {
    const midnight = start % DAY === 0;
    if (width === DAY && midnight) return dayLabel(start, true);
    if (midnight) return `${dayLabel(start)} – ${dayLabel(start + width - DAY, true)}`;
    return `${dayLabel(start)} ${clock(start)} – ${dayLabel(start + width, true)} ${clock(start + width)} UTC`;
  }
  return `${dayLabel(start, true)}, ${clock(start)}–${clock(start + width)} UTC`;
}

function tickLabel(start, width) {
  if (width >= DAY) return dayLabel(start);
  const date = new Date(start);
  return `${dayLabel(start)} ${pad(date.getUTCHours())}:00`;
}

/** Lay the buckets out on a continuous time axis (periods without transactions are simply absent from the data). */
function useSlots(data) {
  return useMemo(() => {
    const rows = (Array.isArray(data) ? data : [])
      .map((row) => ({ ...row, at: Date.parse(row.start) }))
      .filter((row) => Number.isFinite(row.at))
      .sort((a, b) => a.at - b.at);
    if (!rows.length) return { slots: [], width: DAY };
    const gaps = rows.slice(1).map((row, index) => row.at - rows[index].at).filter((gap) => gap > 0);
    const width = gaps.length ? Math.min(...gaps) : DAY;
    const count = Math.round((rows[rows.length - 1].at - rows[0].at) / width) + 1;
    if (count > 400) {
      return { slots: rows.map((row) => ({ start: row.at, row })), width };
    }
    const slots = Array.from({ length: count }, (_, index) => ({ start: rows[0].at + index * width, row: null }));
    for (const row of rows) slots[Math.round((row.at - rows[0].at) / width)].row = row;
    return { slots, width };
  }, [data]);
}

export default function Timeline({ data }) {
  const { slots, width: period } = useSlots(data);
  if (!slots.length) {
    return <p className="muted small">No activity timeline is available for this dataset.</p>;
  }
  return <TimelineChart slots={slots} period={period} />;
}

function TimelineChart({ slots, period }) {
  const wrapRef = useRef(null);
  const width = useWidth(wrapRef);
  const [active, setActive] = useState(null);

  const plotWidth = Math.max(60, width - LEFT - RIGHT);
  const band = plotWidth / slots.length;
  const barWidth = Math.max(1, Math.min(MAX_BAR, band - GAP));
  const xOf = (index) => LEFT + index * band + (band - barWidth) / 2;
  const centre = (index) => LEFT + index * band + band / 2;
  const tickCount = Math.max(2, Math.min(8, Math.floor(plotWidth / 120), slots.length));
  const xTicks = [...new Set(Array.from({ length: tickCount }, (_, k) => Math.round((k * (slots.length - 1)) / Math.max(1, tickCount - 1))))];

  const pick = (clientX) => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    const index = Math.floor((clientX - rect.left - LEFT) / band);
    setActive(Math.max(0, Math.min(slots.length - 1, index)));
  };

  const onKeyDown = (event) => {
    const moves = { ArrowLeft: -1, ArrowRight: 1 };
    if (event.key in moves) {
      event.preventDefault();
      setActive((current) => Math.max(0, Math.min(slots.length - 1, (current ?? slots.length - 1) + moves[event.key] * (current === null ? 0 : 1))));
    } else if (event.key === 'Home') {
      event.preventDefault();
      setActive(0);
    } else if (event.key === 'End') {
      event.preventDefault();
      setActive(slots.length - 1);
    } else if (event.key === 'Escape') {
      setActive(null);
    }
  };

  const current = active !== null ? slots[active] : null;
  const tipLeft = active !== null ? centre(active) : 0;
  const tipFlip = tipLeft > width - 190;

  return (
    <div className="timeline">
      <div
        className="timeline-plots"
        ref={wrapRef}
        tabIndex={0}
        role="group"
        aria-label="Activity over time. Use the left and right arrow keys to read each period."
        onPointerMove={(event) => pick(event.clientX)}
        onPointerLeave={() => setActive(null)}
        onFocus={() => setActive((value) => value ?? slots.length - 1)}
        onBlur={() => setActive(null)}
        onKeyDown={onKeyDown}
      >
        {width > 0 && PANELS.map((panel, panelIndex) => {
          const last = panelIndex === PANELS.length - 1;
          const values = slots.map((slot) => Number(slot.row?.[panel.key]) || 0);
          const ticks = niceTicks(Math.max(...values));
          const top = ticks[ticks.length - 1];
          const yOf = (value) => TOP + PLOT - (value / top) * PLOT;
          const peak = values.indexOf(Math.max(...values));
          const height = TOP + PLOT + (last ? AXIS_BAND : 6);
          return (
            <div className="timeline-panel" key={panel.key}>
              <p className="chart-title"><span className={`swatch series-${panel.series}`} aria-hidden="true" /> {panel.title} <span className="muted">per {periodName(period)}</span></p>
              <svg width={width} height={height} role="img" aria-label={`${panel.title}: ${fmtInt(values.reduce((a, b) => a + b, 0))} in total, peak ${fmtInt(values[peak])} on ${periodLabel(slots[peak].start, period)}`}>
                {ticks.map((tick) => (
                  <g key={tick}>
                    <line className={tick === 0 ? 'chart-baseline' : 'chart-grid'} x1={LEFT} x2={LEFT + plotWidth} y1={yOf(tick)} y2={yOf(tick)} />
                    <text className="chart-tick" x={LEFT - 7} y={yOf(tick)} dy="0.32em" textAnchor="end">{fmtCompact(tick)}</text>
                  </g>
                ))}
                {values.map((value, index) => (value > 0 ? (
                  <path
                    key={slots[index].start}
                    className={`chart-mark series-${panel.series}${active === index ? ' is-active' : ''}`}
                    d={columnPath(xOf(index), yOf(value), barWidth, Math.max(1, TOP + PLOT - yOf(value)))}
                  />
                ) : null))}
                {values[peak] > 0 && active === null && (
                  <text
                    className="chart-value"
                    x={Math.max(LEFT + 14, Math.min(LEFT + plotWidth - 14, centre(peak)))}
                    y={yOf(values[peak]) - 5}
                    textAnchor="middle"
                  >
                    {fmtInt(values[peak])}
                  </text>
                )}
                {values[peak] === 0 && (
                  <text className="chart-tick" x={LEFT + plotWidth / 2} y={TOP + PLOT / 2} textAnchor="middle">none in this period</text>
                )}
                {active !== null && <line className="chart-crosshair" x1={centre(active)} x2={centre(active)} y1={TOP - 6} y2={TOP + PLOT} />}
                {last && xTicks.map((index) => (
                  <text
                    key={index}
                    className="chart-tick"
                    x={centre(index)}
                    y={TOP + PLOT + 15}
                    textAnchor={index === 0 ? 'start' : index === slots.length - 1 ? 'end' : 'middle'}
                  >
                    {tickLabel(slots[index].start, period)}
                  </text>
                ))}
              </svg>
            </div>
          );
        })}
        {current && (
          <div className={`chart-tip${tipFlip ? ' flip' : ''}`} style={{ left: tipLeft }} role="status">
            <p className="chart-tip-head">{periodLabel(current.start, period)}</p>
            <p><span className="line-key series-all" aria-hidden="true" /><strong>{fmtInt(current.row?.transactions || 0)}</strong> transactions</p>
            <p><span className="line-key series-flagged" aria-hidden="true" /><strong>{fmtInt(current.row?.flagged || 0)}</strong> HIGH or CRITICAL</p>
            <p className="chart-tip-extra"><strong>{fmtBtc(current.row?.value || 0)}</strong> moved{(current.row?.flagged_value || 0) > 0 && <>, <strong>{fmtBtc(current.row.flagged_value)}</strong> in flagged transactions</>}</p>
          </div>
        )}
      </div>
      <details className="chart-table">
        <summary>Table view</summary>
        <div className="table-wrap" style={{ maxHeight: 260 }}>
          <table className="data-table">
            <caption className="sr-only">Transactions per period</caption>
            <thead>
              <tr>
                <th scope="col">Period</th>
                <th scope="col" className="num">Transactions</th>
                <th scope="col" className="num">HIGH or CRITICAL</th>
                <th scope="col" className="num">Value moved</th>
                <th scope="col" className="num">Flagged value</th>
              </tr>
            </thead>
            <tbody>
              {slots.filter((slot) => slot.row).map((slot) => (
                <tr key={slot.start}>
                  <td>{periodLabel(slot.start, period)}</td>
                  <td className="num">{fmtInt(slot.row.transactions)}</td>
                  <td className="num">{fmtInt(slot.row.flagged)}</td>
                  <td className="num">{fmtBtc(slot.row.value)}</td>
                  <td className="num">{fmtBtc(slot.row.flagged_value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
