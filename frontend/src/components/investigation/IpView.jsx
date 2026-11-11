/** An IP address as a lead: what it originated, who broadcast from it, and whether it is only a relay. */

import { getIp } from '../../api/client.js';
import useApi from '../../lib/useApi.js';
import { asArray, fmtBtc, fmtInt, fmtPct, fmtTime, isNum, isStandardPort, plural, standardPortsText } from '../../lib/format.js';
import { ApiState, EntityLink, RiskBadge } from '../common.jsx';
import DataTable from '../DataTable.jsx';
import {
  ActorLink,
  EvidenceTabs,
  GraphSection,
  Header,
  KeyValues,
  PortChips,
  Reasons,
  ReportLink,
  Section,
  Where,
} from './parts.jsx';

const ROLE_HINT = {
  'relay node': 'Most of its sightings pass on transactions first seen elsewhere, so what it announces first is not attributed to the spenders',
  'single actor': 'Every wallet that broadcast from it belongs to one actor',
  'seen only as a later relay': 'It never announced a transaction first',
  'no attributed traffic': 'No transaction was attributed to it',
};

function roleClass(role) {
  if (role === 'relay node') return 'chip-warn';
  if (role === 'seen only as a later relay' || role === 'no attributed traffic') return 'chip-muted';
  return '';
}

function WalletsTab({ wallets, total, onSelect, seedSet }) {
  const rows = asArray(wallets);
  if (!rows.length) {
    return <p className="muted small">No wallet’s spends were attributed to this IP: it only relayed transactions first seen elsewhere.</p>;
  }
  return (
    <div className="stack">
      <DataTable
        caption="Wallets that broadcast from this IP"
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
          { key: 'risk', label: 'Risk', sort: (w) => w.risk_score, render: (w) => <RiskBadge score={w.risk_score} level={w.risk_level} compact /> },
          { key: 'count', label: 'Broadcasts', className: 'num', title: 'Spends of this wallet first seen from the IP', sort: (w) => w.broadcasts, render: (w) => fmtInt(w.broadcasts) },
          { key: 'actor', label: 'Actor', sort: (w) => w.actor_id, render: (w) => <ActorLink id={w.actor_id} onSelect={onSelect} /> },
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

function TransactionsTab({ transactions, total, onSelect }) {
  const rows = asArray(transactions);
  if (!rows.length) return <p className="muted small">No transaction was first seen from this IP.</p>;
  return (
    <div className="stack">
      <DataTable
        caption="Transactions first seen from this IP"
        maxHeight={340}
        initialSort={{ key: 'risk', dir: 'desc' }}
        columns={[
          { key: 'tx', label: 'Transaction', sort: (t) => t.txid, render: (t) => <EntityLink type="transaction" id={t.txid} level={t.risk_level} onSelect={onSelect} head={8} tail={6} /> },
          { key: 'time', label: 'Time (UTC)', sort: (t) => t.timestamp, render: (t) => <span className="nowrap">{fmtTime(t.timestamp)}</span> },
          { key: 'risk', label: 'Risk', sort: (t) => t.risk_score, render: (t) => <RiskBadge score={t.risk_score} level={t.risk_level} compact /> },
          { key: 'value', label: 'Value', className: 'num', sort: (t) => t.total_output, render: (t) => <span className="nowrap">{fmtBtc(t.total_output)}</span> },
          {
            key: 'port',
            label: 'Port',
            className: 'num',
            sort: (t) => t.port,
            render: (t) => (Number(t.port) > 0 ? <span className={isStandardPort(t.port) ? undefined : 'port-odd'} title={isStandardPort(t.port) ? undefined : 'Non-standard port'}>{t.port}</span> : '—'),
          },
          { key: 'conf', label: 'Origin conf.', className: 'num', title: 'Confidence that this IP created the transaction', sort: (t) => t.origin_confidence, render: (t) => fmtPct(t.origin_confidence) },
          { key: 'io', label: 'In / out', className: 'num', title: 'Number of inputs and outputs', sort: (t) => t.inputs + t.outputs, render: (t) => `${fmtInt(t.inputs)} / ${fmtInt(t.outputs)}` },
        ]}
        rows={rows}
        rowKey={(t) => String(t.txid)}
        onRowClick={(t) => onSelect({ type: 'transaction', id: String(t.txid) })}
      />
      {isNum(total) && total > rows.length && (
        <p className="muted small">The {fmtInt(rows.length)} riskiest of {fmtInt(total)} originated transactions are listed.</p>
      )}
    </div>
  );
}

function ActorsTab({ actors, onSelect }) {
  const rows = asArray(actors);
  if (!rows.length) return <p className="muted small">No actor broadcast from this IP.</p>;
  return (
    <DataTable
      caption="Actors that broadcast from this IP"
      maxHeight={340}
      initialSort={{ key: 'onip', dir: 'desc' }}
      columns={[
        { key: 'actor', label: 'Actor', sort: (a) => a.actor_id, render: (a) => <ActorLink id={a.actor_id} level={a.risk_level} onSelect={onSelect} /> },
        { key: 'risk', label: 'Actor risk', sort: (a) => a.risk_score, render: (a) => <RiskBadge score={a.risk_score} level={a.risk_level} compact /> },
        { key: 'onip', label: 'Wallets on this IP', className: 'num', sort: (a) => a.wallets_on_ip, render: (a) => fmtInt(a.wallets_on_ip) },
        { key: 'all', label: 'Wallets in total', className: 'num', sort: (a) => a.wallets, render: (a) => fmtInt(a.wallets) },
      ]}
      rows={rows}
      rowKey={(a) => String(a.actor_id)}
      onRowClick={(a) => onSelect({ type: 'actor', id: String(a.actor_id) })}
    />
  );
}

function PortsTab({ ports, share }) {
  const rows = asArray(ports);
  if (!rows.length) return <p className="muted small">No ports are recorded for this IP.</p>;
  return (
    <div className="stack">
      <p className="muted small">
        Listening port of each sighting. Bitcoin nodes normally listen on {standardPortsText()}; anything else is highlighted
        {isNum(share) && <> ({fmtPct(share)} of the transactions it originated)</>}.
      </p>
      <PortChips ports={rows} />
    </div>
  );
}

export default function IpView({ datasetId, id, refresh, onSelect, seedSet, view, onViewChange }) {
  const detail = useApi(`ip|${datasetId}|${id}|${refresh}`, (signal) => getIp(datasetId, id, signal));
  const d = detail.data || {};
  const reasons = asArray(d.reasons);

  return (
    <ApiState
      result={detail}
      loadingText="Loading IP…"
      operation="Loading IP"
      notFoundText={<>IP <code>{id}</code> does not appear in this dataset.</>}
    >
      <Header
        kind="IP address"
        id={String(d.ip ?? id)}
        score={d.risk_score}
        level={d.risk_level}
        confidence={d.confidence}
        chips={(
          <>
            {isNum(d.rank) && <span className="chip chip-small chip-muted">rank #{fmtInt(d.rank)}</span>}
            {(d.country || Number(d.asn) > 0 || d.as_org) && (
              <span className="chip chip-small" title="Where the address is registered"><Where country={d.country} asn={d.asn} org={d.as_org} /></span>
            )}
            {d.role && <span className={`chip chip-small ${roleClass(d.role)}`} title={ROLE_HINT[d.role] || 'Wallets of several actors broadcast from it'}>{d.role}</span>}
            {d.scope && d.scope !== 'global' && (
              <span className="chip chip-small chip-warn" title="Not a publicly routable address, so no GeoIP database can place it">
                {String(d.scope).replace(/_/g, ' ')} address
              </span>
            )}
          </>
        )}
        actions={<ReportLink datasetId={datasetId} kind="ip" id={String(d.ip ?? id)} />}
      >
        {d.top_reason && <p className="inv-summary">{d.top_reason}</p>}
        <p className="muted small">
          An IP’s risk is the risk of the traffic it originated: each wallet’s score weighted by how often that wallet broadcast from it.
          Confidence grows with the number of transactions and with how clearly this IP was their first sighting.
        </p>
      </Header>

      <Section title="Why flagged">
        {reasons.length ? <Reasons reasons={reasons} onSelect={onSelect} /> : (
          <p className="muted small">Nothing about this IP stands out: none of the wallets that broadcast from it is HIGH risk or above.</p>
        )}
      </Section>

      <Section title="Profile">
        <KeyValues
          items={[
            ['Transactions originated', fmtInt(d.originated), 'Transactions first seen from this IP'],
            ['Relayed for others', fmtInt(d.relayed), 'Sightings of transactions first seen elsewhere'],
            ['Relay share', isNum(d.relay_ratio) ? fmtPct(d.relay_ratio) : null, '50% or more (and at least three relays) marks a relay node'],
            ['Flagged wallets', `${fmtInt(d.flagged_wallets)} of ${fmtInt(d.wallets)}`],
            ['Actors', fmtInt(d.actors)],
            ['Risky share of its traffic', isNum(d.flagged_share) ? fmtPct(d.flagged_share) : null, 'Risk-weighted share of its broadcasts that come from wallets with direct evidence'],
            ['Value originated', isNum(d.value) ? fmtBtc(d.value) : null],
            ['Non-standard port share', isNum(d.nonstandard_port_share) ? fmtPct(d.nonstandard_port_share) : null],
            ['First seen (UTC)', d.first_seen ? fmtTime(d.first_seen) : null],
            ['Last seen (UTC)', d.last_seen ? fmtTime(d.last_seen) : null],
          ]}
        />
      </Section>

      <GraphSection datasetId={datasetId} focusType="ip" focusId={id} refresh={refresh} onSelect={onSelect} view={view} onViewChange={onViewChange} />
      <EvidenceTabs
        active={view?.evidence}
        onChange={(tab) => onViewChange?.({ evidence: tab })}
        tabs={[
          { id: 'wallets', label: 'Wallets', count: d.wallets, render: () => <WalletsTab wallets={d.wallet_list} total={d.wallets} onSelect={onSelect} seedSet={seedSet} /> },
          { id: 'transactions', label: 'Transactions', count: d.originated, render: () => <TransactionsTab transactions={d.transactions} total={d.originated} onSelect={onSelect} /> },
          { id: 'actors', label: 'Actors', count: d.actors, render: () => <ActorsTab actors={d.actor_list} onSelect={onSelect} /> },
          { id: 'ports', label: 'Ports', count: asArray(d.ports).length, render: () => <PortsTab ports={d.ports} share={d.nonstandard_port_share} /> },
        ]}
      />
      {d.wallets > 0 && (
        <p className="muted small">
          {plural(d.wallets, 'wallet')} broadcasting from one address is a lead to shared infrastructure, not proof of common ownership:
          a VPN exit, a hosting provider or a public node serves unrelated users.
        </p>
      )}
    </ApiState>
  );
}
