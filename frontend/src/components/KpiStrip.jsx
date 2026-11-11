import { Stat } from './common.jsx';
import {
  ACTOR_RULE,
  RISK_LEVELS,
  asArray,
  fmtCompact,
  fmtDate,
  fmtInt,
  fmtSeconds,
  fmtTime,
  isNum,
  patternLabel,
  sumValues,
} from '../lib/format.js';

const LEAD_ROWS = [
  { tab: 'wallets', label: 'Wallets', key: 'risk_distribution' },
  { tab: 'transactions', label: 'Transactions', key: 'transaction_risk_distribution' },
  { tab: 'actors', label: 'Actors', key: 'actor_risk_distribution' },
  { tab: 'ips', label: 'Origin IPs', key: 'ip_risk_distribution' },
];

const SHORT = { CRITICAL: 'Critical', HIGH: 'High', MEDIUM: 'Medium', LOW: 'Low' };
const SHORT_PATTERN = { peeling_chain: 'peeling', layering: 'layering', rapid_passthrough: 'pass-through', coinjoin: 'CoinJoin' };

/**
 * Leads of every kind by risk level. A table, not a bar: LOW outnumbers the rest by
 * orders of magnitude, so any bar would hide exactly the figures that matter.
 * Each figure opens that list, filtered to the level.
 */
function RiskTable({ overview, onPick }) {
  const rows = LEAD_ROWS.filter((row) => overview[row.key] && sumValues(overview[row.key]) > 0);
  if (!rows.length) return null;
  return (
    <table className="risk-table">
      <caption>Leads by risk level</caption>
      <thead>
        <tr>
          <th scope="col"><span className="sr-only">Lead type</span></th>
          {RISK_LEVELS.map((level) => (
            <th key={level} scope="col" className="num">
              <span className={`dot risk-dot-${level.toLowerCase()}`} aria-hidden="true" /> {SHORT[level]}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.tab}>
            <th scope="row">
              <button type="button" className="link-button" onClick={() => onPick(row.tab, '')} title={`Open the ${row.label.toLowerCase()} list`}>{row.label}</button>
            </th>
            {RISK_LEVELS.map((level) => {
              const count = overview[row.key][level];
              const hot = (level === 'CRITICAL' || level === 'HIGH') && count > 0;
              return (
                <td key={level} className="num">
                  {count > 0 ? (
                    <button
                      type="button"
                      className={`link-button figure${hot ? ' hot' : ''}`}
                      onClick={() => onPick(row.tab, level)}
                      title={`Show the ${fmtInt(count)} ${level} ${row.label.toLowerCase()}`}
                    >
                      {fmtInt(count)}
                    </button>
                  ) : <span className="muted">0</span>}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function KpiStrip({ overview, onPick }) {
  const stats = overview.stats || {};
  const dist = overview.risk_distribution || {};
  const patterns = overview.patterns || {};
  const flagged = (dist.CRITICAL || 0) + (dist.HIGH || 0);
  const patternTotal = sumValues(patterns);
  const found = Object.entries(patterns).filter(([, n]) => n > 0);
  const patternText = found.map(([type, n]) => `${n} ${SHORT_PATTERN[type] || patternLabel(type).toLowerCase()}`).join(' · ');
  const patternTitle = found.map(([type, n]) => `${patternLabel(type)}: ${n}`).join(' · ');
  const seeds = asArray(overview.seeds).length;
  const period = stats.start_time ? `${fmtDate(stats.start_time)} → ${fmtDate(stats.end_time)}` : null;
  const stages = overview.stage_seconds && typeof overview.stage_seconds === 'object'
    ? Object.entries(overview.stage_seconds).filter(([, seconds]) => isNum(seconds)).map(([stage, seconds]) => `${stage} ${fmtSeconds(seconds)}`).join(' · ')
    : '';

  return (
    <section className="kpis" aria-label="Dataset overview">
      <div className="kpi-main">
        <div className="kpi-grid">
          <Stat label="Transactions" value={fmtInt(stats.transactions)} sub={period} title={period ? `First to last transaction (UTC): ${fmtTime(stats.start_time)} → ${fmtTime(stats.end_time)}` : undefined} />
          <Stat
            label="Value moved"
            value={isNum(stats.total_value) ? <>{fmtCompact(stats.total_value)} <span className="kpi-unit">BTC</span></> : '—'}
            sub="sum of all outputs"
          />
          <Stat
            label="Network sightings"
            value={fmtInt(stats.observations)}
            sub={Number(stats.ips) > 0
              ? `${fmtInt(stats.origin_ips)} origin IPs · ${fmtInt(stats.countries)} countries`
              : 'no source IPs in this dataset'}
            title="Rows of network metadata: one per time a transaction was seen being announced"
          />
          <Stat
            label="Wallets"
            value={fmtInt(stats.wallets)}
            sub={isNum(stats.entities) ? `${fmtInt(stats.entities)} entities · ${fmtInt(stats.multi_address_entities)} multi-address` : null}
            title="Addresses, and the owners (entities) the ownership heuristics group them into"
          />
          <Stat
            label="Actors"
            value={fmtInt(stats.actors)}
            sub={isNum(stats.multi_entity_actors) ? `${fmtInt(stats.multi_entity_actors)} span several entities` : null}
            title={ACTOR_RULE}
          />
          <Stat
            label="Flagged wallets"
            value={fmtInt(flagged)}
            tone={flagged > 0 ? 'alert' : undefined}
            sub={`${fmtInt(dist.CRITICAL || 0)} critical · ${fmtInt(dist.HIGH || 0)} high`}
            title="Wallets scored HIGH (60+) or CRITICAL (80+)"
          />
          <Stat
            label="Structures"
            value={fmtInt(patternTotal)}
            sub={patternText || 'none detected'}
            title={`Laundering structures found by the pattern detectors${patternTitle ? `. ${patternTitle}` : ''}`}
          />
          <Stat
            label="Seeds"
            value={fmtInt(seeds)}
            sub={seeds ? 'known illicit wallets' : 'none: scored without prior knowledge'}
            title="Known illicit wallets supplied by the investigator; taint is traced from them"
          />
        </div>
        <p className="kpi-meta muted" title={stages ? `Pipeline stages: ${stages}` : undefined}>
          {overview.filename && <><strong>{overview.filename}</strong> · </>}
          {overview.analyzed_at ? <>analysed {fmtTime(overview.analyzed_at)} UTC</> : 'analysis time not recorded'}
          {isNum(overview.processing_seconds) && <> in {fmtSeconds(overview.processing_seconds)}</>}
          {isNum(stats.communities) && <> · {fmtInt(stats.communities)} communities</>}
          {isNum(stats.asns) && <> · {fmtInt(stats.asns)} networks</>}
        </p>
      </div>
      <RiskTable overview={overview} onPick={onPick} />
    </section>
  );
}
