import { getWallet } from '../../api/client.js';
import useApi from '../../lib/useApi.js';
import {
  ACTOR_RULE,
  ACTOR_RULE_SHORT,
  asArray,
  fmtBtc,
  fmtInt,
  fmtNum,
  fmtPct,
  fmtTime,
  isNum,
  isStandardPort,
  levelRank,
  plural,
} from '../../lib/format.js';
import { ApiState, EntityLink, IdText, RiskBadge, Tags } from '../common.jsx';
import DataTable from '../DataTable.jsx';
import {
  ActorLink,
  ActorLinkEvidence,
  EvidenceTabs,
  FeatureList,
  GraphSection,
  Header,
  IpLink,
  KeyValues,
  PatternList,
  PortChips,
  ReportLink,
  Where,
  WhyFlagged,
} from './parts.jsx';

function Counterparties({ ids, onSelect }) {
  const list = asArray(ids);
  if (!list.length) return <span className="muted">—</span>;
  return (
    <span className="counterparties">
      {list.slice(0, 2).map((id) => <EntityLink key={id} type="wallet" id={id} onSelect={onSelect} head={6} tail={4} copy={false} />)}
      {list.length > 2 && <span className="muted" title={list.slice(2).join('\n')}>+{list.length - 2}</span>}
    </span>
  );
}

function Port({ port }) {
  if (!Number(port)) return <span className="muted">—</span>;
  return isStandardPort(port) ? port : <span className="port-odd" title="Non-standard port">{port}</span>;
}

function TransactionsTab({ transactions, onSelect }) {
  const rows = asArray(transactions);
  if (!rows.length) return <p className="muted small">No transactions are recorded for this wallet.</p>;
  return (
    <div className="stack">
      <DataTable
        caption="Wallet transactions"
        maxHeight={360}
        initialSort={{ key: 'time', dir: 'desc' }}
        columns={[
          { key: 'time', label: 'Time (UTC)', sort: (t) => t.timestamp, render: (t) => <span className="nowrap">{fmtTime(t.timestamp)}</span> },
          { key: 'dir', label: 'Dir', sort: (t) => t.direction, render: (t) => <span className={`dir dir-${t.direction || 'na'}`}>{t.direction || '—'}</span> },
          { key: 'amount', label: 'Amount', className: 'num', sort: (t) => t.amount, render: (t) => <span className="nowrap">{fmtBtc(t.amount)}</span> },
          { key: 'cp', label: 'Counterparties', render: (t) => <Counterparties ids={t.counterparties} onSelect={onSelect} /> },
          { key: 'ip', label: 'First seen from', sort: (t) => t.src_ip, render: (t) => <IpLink ip={t.src_ip} onSelect={onSelect} /> },
          { key: 'port', label: 'Port', className: 'num', sort: (t) => t.dst_port, render: (t) => <Port port={t.dst_port} /> },
          { key: 'risk', label: 'Tx risk', sort: (t) => t.risk_score ?? levelRank(t.risk_level), render: (t) => <RiskBadge score={t.risk_score} level={t.risk_level} compact /> },
          { key: 'tx', label: 'Txid', render: (t) => <IdText id={t.txid} head={6} tail={4} /> },
        ]}
        rows={rows}
        rowKey={(t, i) => `${t.txid}-${i}`}
        onRowClick={(t) => t.txid && onSelect({ type: 'transaction', id: String(t.txid) })}
      />
      {rows.length >= 200 && <p className="muted small">The 200 most recent transactions are listed.</p>}
    </div>
  );
}

function NetworkTab({ network, actor, onSelect }) {
  if (!network) return <p className="muted small">No network observations are recorded for this wallet.</p>;
  const ips = asArray(network.ips);
  const countries = asArray(network.countries);
  const asns = asArray(network.asns);
  const association = network.association;
  const footprint = network.actor;
  const actorHasTraffic = Number(footprint?.broadcasts) > 0;
  if (!ips.length && !actorHasTraffic) {
    return (
      <p className="muted small">
        None of this wallet’s spends could be attributed to an IP: it never spent, its transactions were first seen from relay nodes,
        or the dataset records no source IPs. It has no network footprint.
      </p>
    );
  }
  return (
    <div className="stack">
      {association && (
        <p className="note small">
          <strong>{fmtPct(association.share_of_spends)}</strong> of this wallet’s spends were first seen from{' '}
          <IpLink ip={association.ip} onSelect={onSelect} />, where{' '}
          <strong>{fmtInt(association.flagged_wallets_on_ip)} of {fmtInt(association.wallets_on_ip)}</strong> wallets are flagged
          and {fmtPct(association.ip_flagged_share)} of the other traffic is risky.
          {isNum(association.signal) && <> Network association signal: <strong>{fmtNum(association.signal, 2)}</strong>.</>}
        </p>
      )}
      {ips.length > 0 ? (
        <>
          <h4>IPs this wallet’s spends were first seen from</h4>
          <DataTable
            caption="IPs this wallet broadcast from"
            maxHeight={300}
            columns={[
              { key: 'ip', label: 'IP', sort: (r) => r.ip, render: (r) => <IpLink ip={r.ip} level={r.risk_level} onSelect={onSelect} /> },
              { key: 'risk', label: 'IP risk', sort: (r) => r.risk_score, render: (r) => <RiskBadge score={r.risk_score} level={r.risk_level} compact /> },
              { key: 'where', label: 'Country · network', sort: (r) => r.country, render: (r) => <Where country={r.country} asn={r.asn} org={r.as_org} /> },
              { key: 'count', label: 'Spends', className: 'num', title: 'Spends of this wallet first seen from the IP', sort: (r) => r.count, render: (r) => fmtInt(r.count) },
              { key: 'share', label: 'Share', className: 'num', title: 'Share of this wallet’s spends', sort: (r) => r.share, render: (r) => fmtPct(r.share) },
              { key: 'users', label: 'Wallets on IP', className: 'num', title: 'How many wallets broadcast from this IP', sort: (r) => r.wallets_on_ip, render: (r) => fmtInt(r.wallets_on_ip) },
              { key: 'link', label: 'Link strength', className: 'num', title: 'Share of spends ÷ wallets on the IP: high when the IP carries most of this wallet’s spends and nobody else’s', sort: (r) => r.association, render: (r) => fmtNum(r.association, 2) },
            ]}
            rows={ips}
            rowKey={(r) => r.ip}
            onRowClick={(r) => onSelect({ type: 'ip', id: String(r.ip) })}
            initialSort={{ key: 'count', dir: 'desc' }}
          />
        </>
      ) : (
        <p className="muted small">None of this wallet’s own spends were attributed to an IP; the footprint below belongs to its actor.</p>
      )}
      <KeyValues
        items={[
          ['Distinct IPs', ips.length ? fmtInt(ips.length) : null],
          ['Non-standard port share', ips.length && isNum(network.nonstandard_port_ratio) ? fmtPct(network.nonstandard_port_ratio) : null],
          ['Other wallets on its IPs', isNum(network.shared_ip_wallets) ? fmtInt(network.shared_ip_wallets) : null],
          ['Countries', countries.length ? countries.map((c) => `${c.country || 'unknown'} (${c.count})`).join(', ') : null],
          ['Networks', asns.length ? asns.map((a) => `AS${a.asn} (${a.count})`).join(', ') : null],
        ]}
      />
      <PortChips ports={network.ports} />
      {footprint && (
        <>
          <h4>
            Actor-level footprint{' '}
            <span className="muted small">
              {actor?.actor_id ? <>({actor.actor_id}, {plural(actor.wallets, 'wallet')}) </> : null}
              — the network signal is computed for the whole actor
            </span>
          </h4>
          <KeyValues
            items={[
              ['Attributed broadcasts', fmtInt(footprint.broadcasts)],
              ['IPs', fmtInt(footprint.ips)],
              ['Networks (ASNs)', fmtInt(footprint.asns)],
              ['Countries', fmtInt(footprint.countries)],
              ['Non-standard port share', isNum(footprint.nonstandard_port_share) ? fmtPct(footprint.nonstandard_port_share) : null],
            ]}
          />
        </>
      )}
    </div>
  );
}

function MemberList({ wallets, walletId, onSelect, total }) {
  const list = asArray(wallets);
  if (!list.length) return null;
  return (
    <>
      <ul className="link-list columns">
        {list.map((item) => {
          const id = typeof item === 'string' ? item : item.wallet_id;
          const level = typeof item === 'string' ? undefined : item.risk_level;
          return (
            <li key={id}>
              <EntityLink type="wallet" id={id} level={level} onSelect={onSelect} head={12} tail={8} />
              {String(id) === String(walletId) && <span className="muted small"> this wallet</span>}
              {typeof item !== 'string' && isNum(item.risk_score) && <span className="muted small"> {item.risk_score.toFixed(1)}</span>}
            </li>
          );
        })}
      </ul>
      {isNum(total) && total > list.length && (
        <p className="muted small">Showing {fmtInt(list.length)} of {fmtInt(total)} wallets, highest risk first.</p>
      )}
    </>
  );
}

function OwnershipTab({ entity, actor, walletId, onSelect }) {
  if (!entity && !actor) return <p className="muted small">No ownership information is stored for this wallet.</p>;
  const link = entity?.link;
  const structural = String(link?.heuristic || '').startsWith('same laundering structure');
  const actorLink = actor?.link;
  return (
    <div className="stack">
      {entity && (
        <div className="stack-tight">
          <h4>
            Entity {entity.entity_id}{' '}
            <span className="muted small">— addresses the ownership heuristics attribute to one owner</span>
          </h4>
          <KeyValues
            items={[
              ['Addresses', fmtInt(entity.size)],
              ['Counterparty entities', isNum(entity.counterparties) ? fmtInt(entity.counterparties) : null],
              ['Service-likeness', isNum(entity.service_score) ? fmtNum(entity.service_score, 2) : null,
                'Close to 1 when the entity deals with many unrelated counterparties, as an exchange or merchant does'],
            ]}
          />
          {link ? (
            <p className="small">
              This address joined the entity by <strong>{link.heuristic}</strong>{' '}
              {link.txid && (structural
                ? <>(<EntityLink type="pattern" id={link.txid} onSelect={onSelect} head={14} tail={6} copy={false} />).</>
                : <>in transaction <EntityLink type="transaction" id={link.txid} onSelect={onSelect} head={8} tail={6} />.</>)}
            </p>
          ) : (
            <p className="muted small">
              {entity.size > 1
                ? 'This is the address the entity was built around; the other members were attached to it.'
                : 'No heuristic links this address to any other: it is an entity of its own.'}
            </p>
          )}
          {entity.size > 1 && <MemberList wallets={entity.wallets} walletId={walletId} onSelect={onSelect} total={entity.size} />}
        </div>
      )}
      {actor && (
        <div className="stack-tight">
          <h4>
            Actor <ActorLink id={actor.actor_id} level={actor.risk_level} onSelect={onSelect} />{' '}
            <span className="muted small" title={ACTOR_RULE}>— entities operated together ({ACTOR_RULE_SHORT})</span>
          </h4>
          <KeyValues
            items={[
              ['Wallets', fmtInt(actor.wallets)],
              ['Entities', fmtInt(actor.entities)],
              ['Actor risk', <RiskBadge key="risk" score={actor.risk_score} level={actor.risk_level} compact />],
            ]}
          />
          {actorLink ? (
            <p className="small"><ActorLinkEvidence link={actorLink} onSelect={onSelect} lead="This wallet’s entity was joined with entity" /></p>
          ) : (
            <p className="muted small">
              {actor.entities > 1
                ? 'This wallet’s entity is the one the actor was built around.'
                : 'The actor is this wallet’s entity alone: no other entity matches it on two of the three clues.'}
            </p>
          )}
          {actor.wallets > 1 && <MemberList wallets={actor.top_wallets} walletId={walletId} onSelect={onSelect} total={actor.wallets} />}
        </div>
      )}
    </div>
  );
}

/** A path as stored runs from this wallet back to its source; show it in the direction the money moved. */
function MoneyPath({ path, onSelect, sourceLabel }) {
  const steps = asArray(path).filter((step) => step?.wallet_id);
  if (steps.length < 2) return null;
  const forward = [...steps].reverse();
  return (
    <ol className="taint-path" aria-label="Path of the funds">
      {forward.map((step, index) => {
        const next = forward[index + 1];
        return (
          <li key={`${step.wallet_id}-${index}`}>
            {index === 0 && <span className="path-end">{sourceLabel}</span>}
            <EntityLink type="wallet" id={step.wallet_id} onSelect={onSelect} head={6} tail={4} copy={false} />
            {index === forward.length - 1 && <span className="path-end">this wallet</span>}
            {next && (
              <span className="taint-hop">
                <span aria-hidden="true">→</span>
                {next.txid
                  ? <EntityLink type="transaction" id={next.txid} onSelect={onSelect} head={5} tail={3} copy={false} />
                  : <span className="muted small">same owner</span>}
                <span aria-hidden="true">→</span>
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

const TAINT_REASON = {
  seed: 'This wallet is a seed.',
  co_owned: 'Owned by the same entity as a seed wallet.',
  downstream: 'Received funds that trace back to a seed wallet.',
};

function TaintTab({ taint, proceeds, exposure, onSelect }) {
  if (!taint && !proceeds && !exposure) {
    return (
      <p className="muted small">
        No funds from a seed wallet or from a detected laundering structure reach this wallet, and it did not feed one.
      </p>
    );
  }
  return (
    <div className="stack">
      {taint && (
        <div className="stack-tight">
          <h4>Taint from seed wallets</h4>
          {TAINT_REASON[taint.reason] && <p className="small">{TAINT_REASON[taint.reason]}</p>}
          <KeyValues
            items={[
              ['Taint', isNum(taint.score) ? fmtPct(taint.score, 1) : null, 'Value-weighted share of what it received that traces back to a seed'],
              ['Tainted value', isNum(taint.tainted_value) ? fmtBtc(taint.tainted_value) : null],
              ['Of received', isNum(taint.received_value) ? fmtBtc(taint.received_value) : null],
              ['Hops from the seed', isNum(taint.hops) ? fmtInt(taint.hops) : null],
              ['Seed', taint.seed ? <EntityLink key="seed" type="wallet" id={taint.seed} onSelect={onSelect} /> : null],
            ]}
          />
          <MoneyPath path={taint.path} onSelect={onSelect} sourceLabel="seed" />
        </div>
      )}
      {proceeds && (
        <div className="stack-tight">
          <h4>Proceeds of a laundering structure <span className="muted small">— needs no seed</span></h4>
          <KeyValues
            items={[
              ['Share from the structure', isNum(proceeds.score) ? fmtPct(proceeds.score, 1) : null, 'Value-weighted share of what it received that came out of a detected structure'],
              ['Value from the structure', isNum(proceeds.tainted_value) ? fmtBtc(proceeds.tainted_value) : null],
              ['Of received', isNum(proceeds.received_value) ? fmtBtc(proceeds.received_value) : null],
              ['Hops from the structure', isNum(proceeds.hops) ? fmtInt(proceeds.hops) : null],
              ['Structure wallet', proceeds.seed ? <EntityLink key="src" type="wallet" id={proceeds.seed} onSelect={onSelect} /> : null],
            ]}
          />
          <MoneyPath path={proceeds.path} onSelect={onSelect} sourceLabel="structure" />
        </div>
      )}
      {exposure && (
        <div className="stack-tight">
          <h4>Upstream exposure <span className="muted small">— this wallet fed a laundering structure</span></h4>
          <KeyValues
            items={[
              ['Share of its value sent in', isNum(exposure.fraction) ? fmtPct(exposure.fraction, 1) : null],
              ['Exposure signal', isNum(exposure.score) ? fmtNum(exposure.score, 2) : null],
              ['Hops to the structure', isNum(exposure.hops) ? fmtInt(exposure.hops) : null],
              ['Structure wallet', exposure.target ? <EntityLink key="target" type="wallet" id={exposure.target} onSelect={onSelect} /> : null],
              ['Sent through', exposure.child_txid ? <EntityLink key="tx" type="transaction" id={exposure.child_txid} onSelect={onSelect} /> : null],
              ['To', exposure.child && exposure.child !== exposure.target ? <EntityLink key="child" type="wallet" id={exposure.child} onSelect={onSelect} /> : null],
            ]}
          />
        </div>
      )}
    </div>
  );
}

export default function WalletView({ datasetId, id, refresh, onSelect, seedSet, onToggleSeed, busy, view, onViewChange }) {
  const detail = useApi(`wallet|${datasetId}|${id}|${refresh}`, (signal) => getWallet(datasetId, id, signal));
  const d = detail.data || {};
  const isSeed = Boolean(d.is_seed) || seedSet.has(String(id));
  const actor = d.actor;
  const ipCount = asArray(d.network?.ips).length;
  const taintCount = [d.taint, d.proceeds, d.upstream_exposure].filter(Boolean).length;
  const openEvidence = (tab) => {
    onViewChange?.({ evidence: tab });
    requestAnimationFrame(() => document.getElementById('evidence')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
  };

  return (
    <ApiState
      result={detail}
      loadingText="Loading wallet…"
      operation="Loading wallet"
      notFoundText={<>Wallet <code>{id}</code> does not appear in this dataset.</>}
    >
      <Header
        kind="Wallet"
        id={String(d.wallet_id ?? id)}
        score={d.risk_score}
        level={d.risk_level}
        confidence={d.confidence}
        isSeed={isSeed}
        chips={(
          <>
            {isNum(d.rank) && <span className="chip chip-small chip-muted">rank #{fmtInt(d.rank)}</span>}
            <Tags tags={d.tags} skipSeed />
            {d.entity_id && (
              <button type="button" className="chip chip-small chip-button" onClick={() => openEvidence('ownership')} title="Addresses attributed to the same owner: show the evidence">
                Entity {d.entity_id}{isNum(d.entity_size) ? ` · ${plural(d.entity_size, 'address', 'addresses')}` : ''}
              </button>
            )}
            {d.actor_id && (
              <button type="button" className="chip chip-small chip-button" onClick={() => onSelect({ type: 'actor', id: String(d.actor_id) })} title="Entities operated together: open the actor">
                Actor {d.actor_id}{isNum(d.actor_size) ? ` · ${plural(d.actor_size, 'wallet')}` : ''}
              </button>
            )}
            {(d.cluster_id !== undefined && d.cluster_id !== null && Number(d.cluster_id) >= 0) && (
              <button type="button" className="chip chip-small chip-button" onClick={() => onSelect({ type: 'community', id: String(d.cluster_id) })} title="Open the community this wallet’s entity trades in">
                {d.cluster?.label || `Community ${d.cluster_id}`}
              </button>
            )}
          </>
        )}
        actions={(
          <>
            {isSeed ? (
              <button type="button" className="btn btn-small" disabled={busy} onClick={() => onToggleSeed(String(id), false)}>Remove seed</button>
            ) : (
              <button type="button" className="btn btn-small btn-danger" disabled={busy} onClick={() => onToggleSeed(String(id), true)}>Mark as illicit seed</button>
            )}
            <ReportLink datasetId={datasetId} kind="wallet" id={String(d.wallet_id ?? id)} />
          </>
        )}
      >
        {d.top_reason && <p className="inv-summary">{d.top_reason}</p>}
      </Header>

      <WhyFlagged
        detail={d}
        onSelect={onSelect}
        scopeNote={actor && actor.wallets > 1
          ? <>The detectors and feature attributions below describe this wallet’s <strong>actor</strong> ({actor.actor_id}, {plural(actor.wallets, 'wallet')}): behaviour is modelled per actor, because one owner spreads activity over many addresses.</>
          : 'The detectors and feature attributions below describe this wallet’s actor; here the actor is this wallet alone.'}
      />
      <GraphSection datasetId={datasetId} focusType="wallet" focusId={id} refresh={refresh} onSelect={onSelect} view={view} onViewChange={onViewChange} />
      <EvidenceTabs
        active={view?.evidence}
        onChange={(tab) => onViewChange?.({ evidence: tab })}
        tabs={[
          { id: 'transactions', label: 'Transactions', count: asArray(d.transactions).length, render: () => <TransactionsTab transactions={d.transactions} onSelect={onSelect} /> },
          { id: 'network', label: 'Network', count: ipCount, render: () => <NetworkTab network={d.network} actor={actor} onSelect={onSelect} /> },
          { id: 'ownership', label: 'Ownership', render: () => <OwnershipTab entity={d.entity} actor={actor} walletId={id} onSelect={onSelect} /> },
          { id: 'taint', label: 'Taint', count: taintCount, render: () => <TaintTab taint={d.taint} proceeds={d.proceeds} exposure={d.upstream_exposure} onSelect={onSelect} /> },
          { id: 'patterns', label: 'Patterns', count: asArray(d.patterns).length, render: () => <PatternList patterns={d.patterns} onSelect={onSelect} withRole /> },
          { id: 'features', label: 'Features', render: () => <FeatureList features={d.features} labels={d.feature_labels} /> },
        ]}
      />
    </ApiState>
  );
}
