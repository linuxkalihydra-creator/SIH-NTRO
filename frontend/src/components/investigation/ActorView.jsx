/** An actor: entities operated together (two of three clues agree: same IP, a direct payment, a shared deposit address). */

import { getActor } from '../../api/client.js';
import useApi from '../../lib/useApi.js';
import { ACTOR_RULE, asArray, fmtBtc, fmtInt, fmtNum, fmtPct, fmtScore, fmtTopPct, isNum, patternLabel, plural } from '../../lib/format.js';
import { ApiState, EntityLink, RiskBadge } from '../common.jsx';
import DataTable from '../DataTable.jsx';
import {
  ActorLinkEvidence,
  EvidenceTabs,
  FeatureList,
  GraphSection,
  Header,
  IpLink,
  KeyValues,
  ReportLink,
  Section,
  Where,
  WhyFlagged,
} from './parts.jsx';

function WalletsTab({ wallets, total, onSelect, seedSet }) {
  const rows = asArray(wallets);
  if (!rows.length) return <p className="muted small">No wallets are listed for this actor.</p>;
  return (
    <div className="stack">
      <DataTable
        caption="Wallets of this actor"
        maxHeight={340}
        initialSort={{ key: 'risk', dir: 'desc' }}
        columns={[
          {
            key: 'wallet',
            label: 'Wallet',
            sort: (w) => w.wallet_id,
            render: (w) => (
              <span className="id-cell">
                <EntityLink type="wallet" id={w.wallet_id} level={w.risk_level} onSelect={onSelect} head={12} tail={8} />
                {seedSet.has(String(w.wallet_id)) && <span className="seed-tag">SEED</span>}
              </span>
            ),
          },
          { key: 'entity', label: 'Entity', sort: (w) => w.entity_id, render: (w) => w.entity_id || '—' },
          { key: 'risk', label: 'Risk', sort: (w) => w.risk_score, render: (w) => <RiskBadge score={w.risk_score} level={w.risk_level} compact /> },
        ]}
        rows={rows}
        rowKey={(w) => String(w.wallet_id)}
        onRowClick={(w) => onSelect({ type: 'wallet', id: String(w.wallet_id) })}
      />
      {isNum(total) && total > rows.length && (
        <p className="muted small">The {fmtInt(rows.length)} highest-risk wallets of {fmtInt(total)} are listed.</p>
      )}
    </div>
  );
}

function EntitiesTab({ entities, total, onSelect }) {
  const rows = asArray(entities);
  if (!rows.length) return <p className="muted small">No entities are listed for this actor.</p>;
  return (
    <div className="stack">
      {rows.length > 1 && (
        <p className="muted small">
          {ACTOR_RULE} One clue alone is never enough. The link that attached each entity is shown; the entity the actor was built around has none.
        </p>
      )}
      <DataTable
        caption="Entities of this actor"
        maxHeight={340}
        initialSort={{ key: 'risk', dir: 'desc' }}
        columns={[
          { key: 'entity', label: 'Entity', sort: (e) => e.entity_id, render: (e) => <strong>{e.entity_id}</strong> },
          { key: 'wallets', label: 'Addresses', className: 'num', sort: (e) => e.wallets, render: (e) => fmtInt(e.wallets) },
          { key: 'risk', label: 'Highest risk', className: 'num', sort: (e) => e.max_risk, render: (e) => fmtScore(e.max_risk) },
          {
            key: 'top',
            label: 'Riskiest wallet',
            render: (e) => (e.top_wallet ? <EntityLink type="wallet" id={e.top_wallet} onSelect={onSelect} head={8} tail={6} copy={false} /> : '—'),
          },
          {
            key: 'link',
            label: 'Linked to the actor by',
            render: (e) => (e.link
              ? <ActorLinkEvidence link={e.link} onSelect={onSelect} lead="with" />
              : <span className="muted">{rows.length > 1 ? 'first entity of the actor' : 'the only entity'}</span>),
          },
        ]}
        rows={rows}
        rowKey={(e) => String(e.entity_id)}
      />
      {isNum(total) && total > rows.length && (
        <p className="muted small">The {fmtInt(rows.length)} highest-risk entities of {fmtInt(total)} are listed.</p>
      )}
    </div>
  );
}

function InfrastructureTab({ ips, detail, onSelect }) {
  const rows = asArray(ips);
  if (!rows.length) {
    return <p className="muted small">None of this actor’s spends were attributed to an IP, so no infrastructure is known.</p>;
  }
  return (
    <div className="stack">
      <KeyValues
        items={[
          ['IPs', fmtInt(detail.ips)],
          ['Networks (ASNs)', fmtInt(detail.asns)],
          ['Countries', fmtInt(detail.countries)],
          ['Non-standard port share', isNum(detail.nonstandard_port_share) ? fmtPct(detail.nonstandard_port_share) : null],
        ]}
      />
      <DataTable
        caption="IPs this actor broadcast from"
        maxHeight={340}
        initialSort={{ key: 'count', dir: 'desc' }}
        columns={[
          { key: 'ip', label: 'IP', sort: (r) => r.ip, render: (r) => <IpLink ip={r.ip} level={r.risk_level} onSelect={onSelect} /> },
          { key: 'risk', label: 'IP risk', sort: (r) => r.risk_score, render: (r) => <RiskBadge score={r.risk_score} level={r.risk_level} compact /> },
          { key: 'where', label: 'Country · network', sort: (r) => r.country, render: (r) => <Where country={r.country} asn={r.asn} org={r.as_org} /> },
          { key: 'count', label: 'Broadcasts', className: 'num', title: 'Spends of this actor first seen from the IP', sort: (r) => r.count, render: (r) => fmtInt(r.count) },
          { key: 'users', label: 'Wallets on IP', className: 'num', title: 'All wallets that broadcast from this IP, this actor’s or not', sort: (r) => r.wallets_on_ip, render: (r) => fmtInt(r.wallets_on_ip) },
        ]}
        rows={rows}
        rowKey={(r) => r.ip}
        onRowClick={(r) => onSelect({ type: 'ip', id: String(r.ip) })}
      />
      {rows.length >= 40 && <p className="muted small">The 40 most used IPs are listed.</p>}
    </div>
  );
}

export default function ActorView({ datasetId, id, refresh, onSelect, seedSet, view, onViewChange }) {
  const detail = useApi(`actor|${datasetId}|${id}|${refresh}`, (signal) => getActor(datasetId, id, signal));
  const d = detail.data || {};
  const patterns = Object.entries(d.patterns || {}).sort((a, b) => b[1] - a[1]);

  return (
    <ApiState
      result={detail}
      loadingText="Loading actor…"
      operation="Loading actor"
      notFoundText={<>Actor <code>{id}</code> does not exist in this analysis. Actor ids change when a dataset is analysed again.</>}
    >
      <Header
        kind="Actor"
        id={String(d.actor_id ?? id)}
        score={d.risk_score}
        level={d.risk_level}
        chips={(
          <>
            {isNum(d.rank) && <span className="chip chip-small chip-muted">rank #{fmtInt(d.rank)}</span>}
            <span className="chip chip-small">{plural(d.wallets, 'wallet')} in {plural(d.entities, 'entity', 'entities')}</span>
            {d.flagged_wallets > 0 && <span className="chip chip-small chip-bad">{fmtInt(d.flagged_wallets)} HIGH or CRITICAL</span>}
            {d.seed_wallets > 0 && <span className="seed-tag" title="Seed wallets among its addresses">{fmtInt(d.seed_wallets)} SEED</span>}
            {d.service_score >= 0.5 && <span className="tag tag-service" title="Deals with many unrelated counterparties, as an exchange or merchant does">service-like</span>}
          </>
        )}
        actions={(
          <>
            {d.top_wallet && (
              <button type="button" className="btn btn-small" onClick={() => onSelect({ type: 'wallet', id: String(d.top_wallet) })}>
                Open its riskiest wallet
              </button>
            )}
            <ReportLink datasetId={datasetId} kind="actor" id={String(d.actor_id ?? id)} />
          </>
        )}
      >
        {d.top_reason && <p className="inv-summary">Riskiest wallet: {d.top_reason}</p>}
        <p className="muted small">
          {ACTOR_RULE} An actor’s risk is its riskiest wallet’s score, at full weight once a quarter of its
          wallets are MEDIUM or above and down to half weight when the evidence sits on a single wallet of a large actor.
        </p>
      </Header>

      <WhyFlagged
        detail={d}
        onSelect={onSelect}
        title="Why this actor stands out"
        scopeNote="The detectors compare this actor’s combined behaviour (all its wallets together) with every other actor in the dataset."
      />

      <Section title="Profile">
        <KeyValues
          items={[
            ['Highest wallet risk', isNum(d.max_wallet_risk) ? fmtScore(d.max_wallet_risk) : null],
            ['Mean wallet risk', isNum(d.mean_risk) ? fmtScore(d.mean_risk) : null],
            ['Flagged wallets', `${fmtInt(d.flagged_wallets)} of ${fmtInt(d.wallets)}`],
            ['Received from others', isNum(d.received_value) ? fmtBtc(d.received_value) : null, 'Value that came in from outside the actor'],
            ['Sent to others', isNum(d.sent_value) ? fmtBtc(d.sent_value) : null, 'Value that left the actor'],
            ['Behaviour outlier', isNum(d.anomaly_percentile) ? fmtTopPct(d.anomaly_percentile) : null, 'Where the anomaly ensemble places this actor among all actors'],
            ['Service-likeness', isNum(d.service_score) ? fmtNum(d.service_score, 2) : null, 'Close to 1 when it deals with many unrelated counterparties'],
            ['IPs / networks / countries', `${fmtInt(d.ips)} / ${fmtInt(d.asns)} / ${fmtInt(d.countries)}`],
          ]}
        />
        {patterns.length > 0 && (
          <ul className="chip-row" aria-label="Wallets inside detected structures">
            <li className="muted small">Wallets inside structures</li>
            {patterns.map(([type, count]) => <li key={type} className="chip chip-small">{patternLabel(type)} <strong>{fmtInt(count)}</strong></li>)}
          </ul>
        )}
      </Section>

      <GraphSection datasetId={datasetId} focusType="actor" focusId={id} refresh={refresh} onSelect={onSelect} view={view} onViewChange={onViewChange} />
      <EvidenceTabs
        active={view?.evidence}
        onChange={(tab) => onViewChange?.({ evidence: tab })}
        tabs={[
          { id: 'wallets', label: 'Wallets', count: d.wallets, render: () => <WalletsTab wallets={d.top_wallets} total={d.wallets} onSelect={onSelect} seedSet={seedSet} /> },
          { id: 'entities', label: 'Entities', count: d.entities, render: () => <EntitiesTab entities={d.entity_list} total={d.entities} onSelect={onSelect} /> },
          { id: 'infrastructure', label: 'Infrastructure', count: asArray(d.ip_list).length, render: () => <InfrastructureTab ips={d.ip_list} detail={d} onSelect={onSelect} /> },
          { id: 'features', label: 'Features', render: () => <FeatureList features={d.features} labels={d.feature_labels} /> },
        ]}
      />
    </ApiState>
  );
}
