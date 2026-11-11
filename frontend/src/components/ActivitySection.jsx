/** When the traffic happened and where it was first seen from. */

import { useState } from 'react';
import { Collapsible, Stat } from './common.jsx';
import Timeline from './charts/Timeline.jsx';
import RankedBars from './charts/RankedBars.jsx';
import { asArray, countryName, fmtDate, fmtInt, fmtPct, isNum } from '../lib/format.js';

const VISIBLE_ROWS = 8;

/** `total` is how many origins exist in all; the backend lists those with the most flagged transactions. */
function OriginCard({ title, hint, rows, total, noun, nameHeader, emptyText }) {
  const [all, setAll] = useState(false);
  const sorted = [...rows].sort((a, b) => b.transactions - a.transactions || b.flagged - a.flagged);
  const shown = all ? sorted : sorted.slice(0, VISIBLE_ROWS);
  return (
    <figure className="chart-card">
      <figcaption>
        {title}
        <span className="muted">{hint}</span>
      </figcaption>
      <RankedBars rows={shown} nameHeader={nameHeader} emptyText={emptyText} />
      {sorted.length > VISIBLE_ROWS && (
        <button type="button" className="link-button chart-more" onClick={() => setAll((value) => !value)} aria-expanded={all}>
          {all ? 'Show fewer' : `Show all ${sorted.length}`}
        </button>
      )}
      {Number(total) > rows.length && (
        <p className="muted small chart-note">
          The {fmtInt(rows.length)} {noun} with the most HIGH or CRITICAL transactions, of {fmtInt(total)} in all.
        </p>
      )}
    </figure>
  );
}

export default function ActivitySection({ overview, open, onToggle }) {
  const timeline = asArray(overview.timeline);
  const geography = overview.geography || {};
  const network = overview.network || {};
  const stats = overview.stats || {};

  const countries = asArray(geography.countries).map((row) => {
    const name = countryName(row.country);
    return { key: row.country, name, detail: name !== row.country ? row.country : '', transactions: row.transactions, flagged: row.flagged };
  });
  const networks = asArray(geography.asns).map((row) => ({
    key: String(row.asn),
    name: row.as_org || `AS${row.asn}`,
    detail: row.as_org ? `AS${row.asn}` : '',
    transactions: row.transactions,
    flagged: row.flagged,
  }));

  const flaggedTotal = timeline.reduce((acc, bucket) => acc + (bucket.flagged || 0), 0);
  const hint = [
    stats.start_time ? `${fmtDate(stats.start_time)} → ${fmtDate(stats.end_time)}` : null,
    timeline.length ? `${fmtInt(flaggedTotal)} HIGH or CRITICAL transactions` : null,
    Number(stats.ips) > 0 && isNum(network.attributed_share) ? `${fmtPct(network.attributed_share)} attributed to an origin IP` : null,
    Number(stats.ips) > 0 ? null : 'no source IPs recorded',
  ].filter(Boolean).join(' · ');
  const hasNetwork = Number(stats.observations) > 0 && (countries.length > 0 || networks.length > 0 || Number(network.attributed_transactions) > 0);
  const noOriginText = Number(stats.ips) > 0
    ? 'No transaction could be attributed to an origin IP: every first sighting came from a relay node.'
    : 'The dataset records no source IPs, so origins are unknown.';

  return (
    <Collapsible id="activity" title="Activity & origin" hint={hint} open={open} onToggle={onToggle}>
      {hasNetwork && (
        <div className="stat-row">
          <Stat label="Network sightings" value={fmtInt(network.observations)} sub="times a transaction was seen announced" />
          <Stat
            label="Seen more than once"
            value={fmtInt(network.multi_observed_transactions)}
            sub={isNum(stats.transactions) && stats.transactions > 0 ? `${fmtPct(network.multi_observed_transactions / stats.transactions)} of transactions: timing can rank the relays` : null}
          />
          <Stat
            label="Attributed to an origin"
            value={isNum(network.attributed_share) ? fmtPct(network.attributed_share, 1) : '—'}
            sub={`${fmtInt(network.attributed_transactions)} transactions tied to their first-seen IP`}
          />
          <Stat
            label="Mean origin confidence"
            value={isNum(network.mean_origin_confidence) ? fmtPct(network.mean_origin_confidence) : '—'}
            sub="how clearly the first sighting led the rest"
          />
          <Stat
            label="Relay nodes"
            value={fmtInt(network.relay_nodes)}
            sub="IPs that mostly pass on others’ transactions"
          />
        </div>
      )}
      <div className="activity-grid">
        <figure className="chart-card activity-timeline">
          <figcaption>
            Transactions over time
            <span className="muted">UTC · hover, or focus the chart and use the arrow keys</span>
          </figcaption>
          <Timeline data={timeline} />
        </figure>
        <OriginCard
          title="Origin countries"
          hint="transactions first seen from each"
          rows={countries}
          total={geography.country_count}
          noun="countries"
          nameHeader="Country"
          emptyText={noOriginText}
        />
        <OriginCard
          title="Origin networks"
          hint="by autonomous system"
          rows={networks}
          total={geography.asn_count}
          noun="networks"
          nameHeader="Network"
          emptyText={noOriginText}
        />
      </div>
      {hasNetwork && (
        <p className="muted small">
          Origins count only transactions whose first sighting was attributed to its creator; transactions first seen from a relay node are left out.
          A country or network is where the announcing IP is registered (a VPN, Tor exit or hosting provider), not necessarily where the person is.
        </p>
      )}
    </Collapsible>
  );
}
