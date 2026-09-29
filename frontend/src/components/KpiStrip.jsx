import { RiskDistribution } from './common.jsx';
import { asArray, fmtInt, fmtNum, fmtPct, fmtTime, isNum, patternLabel, sumValues } from '../lib/format.js';

function Kpi({ label, value, sub, title }) {
  return (
    <div className="kpi" title={title}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      {sub && <span className="kpi-sub">{sub}</span>}
    </div>
  );
}

export default function KpiStrip({ overview }) {
  const stats = overview.stats || {};
  const dist = overview.risk_distribution || {};
  const patterns = overview.patterns || {};
  const geo = overview.geo_sources || {};
  const geoTotal = sumValues(geo);
  const geoText = Object.keys(geo).length
    ? Object.entries(geo)
      .sort((a, b) => (b[1] || 0) - (a[1] || 0))
      .map(([source, n]) => `${source}${geoTotal ? ` ${fmtPct((n || 0) / geoTotal)}` : ''}`)
      .join(' · ')
    : '—';
  const flagged = (dist.CRITICAL || 0) + (dist.HIGH || 0);
  const patternTotal = sumValues(patterns);

  return (
    <section className="kpis" aria-label="Dataset overview">
      <div className="kpi-grid">
        <Kpi label="Transactions" value={fmtInt(stats.transactions)} sub={stats.start_time ? `${fmtTime(stats.start_time).slice(0, 10)} → ${fmtTime(stats.end_time).slice(0, 10)}` : null} />
        <Kpi label="Wallets" value={fmtInt(stats.wallets)} sub={isNum(stats.ips) ? `${fmtInt(stats.ips)} IPs` : null} />
        <Kpi label="Entities" value={fmtInt(stats.entities)} sub={isNum(stats.multi_address_entities) ? `${fmtInt(stats.multi_address_entities)} multi-address` : null} />
        <Kpi label="Communities" value={fmtInt(stats.communities)} sub={isNum(stats.countries) ? `${fmtInt(stats.countries)} countries · ${fmtInt(stats.asns)} ASNs` : null} />
        <Kpi
          label="Critical + High"
          value={<span className="kpi-alert">{fmtInt(flagged)}</span>}
          sub={`${fmtInt(dist.CRITICAL || 0)} critical · ${fmtInt(dist.HIGH || 0)} high`}
        />
        <Kpi
          label="Patterns"
          value={fmtInt(patternTotal)}
          sub={Object.entries(patterns).map(([t, n]) => `${patternLabel(t)} ${n}`).join(' · ') || 'none detected'}
        />
        <Kpi label="Seeds" value={fmtInt(asArray(overview.seeds).length)} sub={asArray(overview.seeds).length ? 'known-illicit wallets' : 'none set'} />
        <Kpi
          label="GeoIP source"
          value={<span className="kpi-small-value">{geoText}</span>}
          sub={isNum(overview.processing_seconds) ? `analysed in ${fmtNum(overview.processing_seconds, 1)} s` : null}
          title={overview.analyzed_at ? `Analysed at ${fmtTime(overview.analyzed_at)}` : undefined}
        />
      </div>
      <RiskDistribution distribution={dist} />
    </section>
  );
}
