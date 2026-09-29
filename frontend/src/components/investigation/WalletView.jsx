import { getWallet } from '../../api/client.js';
import useApi from '../../lib/useApi.js';
import { asArray, fmtBtc, fmtInt, fmtNum, fmtPct, fmtTime, isNum, patternLabel } from '../../lib/format.js';
import { ApiState, EntityLink, IdText, RiskBadge } from '../common.jsx';
import DataTable from '../DataTable.jsx';
import { EvidenceTabs, GraphSection, Header, KeyValues, WhyFlagged } from './parts.jsx';

export function PatternList({ patterns, onSelect }) {
  const list = asArray(patterns).map((p) => (typeof p === 'string' ? { type: p } : p)).filter(Boolean);
  if (!list.length) return <p className="muted small">Not part of any detected pattern.</p>;
  const withId = list.filter((p) => p.pattern_id !== undefined && p.pattern_id !== null);
  const typeOnly = list.filter((p) => p.pattern_id === undefined || p.pattern_id === null);
  return (
    <>
      {withId.length > 0 && (
        <DataTable
          caption="Patterns"
          columns={[
            { key: 'type', label: 'Type', sort: (p) => p.type, render: (p) => <span className="chip chip-small">{patternLabel(p.type)}</span> },
            { key: 'strength', label: 'Strength', className: 'num', sort: (p) => p.strength, render: (p) => fmtPct(p.strength) },
            { key: 'summary', label: 'Summary', render: (p) => <span className="reason-cell" title={p.summary}>{p.summary || '—'}</span> },
          ]}
          rows={withId}
          rowKey={(p) => String(p.pattern_id)}
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

function Counterparties({ ids, onSelect }) {
  const list = asArray(ids);
  if (!list.length) return <span className="muted">—</span>;
  return (
    <span className="counterparties">
      {list.slice(0, 2).map((id) => <EntityLink key={id} type="wallet" id={id} onSelect={onSelect} head={6} tail={4} />)}
      {list.length > 2 && <span className="muted" title={list.slice(2).join('\n')}>+{list.length - 2}</span>}
    </span>
  );
}

function TransactionsTab({ transactions, onSelect }) {
  const rows = asArray(transactions);
  if (!rows.length) return <p className="muted small">No transactions recorded for this wallet.</p>;
  return (
    <DataTable
      caption="Wallet transaction timeline"
      maxHeight={360}
      initialSort={{ key: 'time', dir: 'desc' }}
      columns={[
        { key: 'time', label: 'Time', sort: (t) => t.timestamp, render: (t) => <span className="nowrap">{fmtTime(t.timestamp)}</span> },
        { key: 'dir', label: 'Dir', sort: (t) => t.direction, render: (t) => <span className={`dir dir-${t.direction || 'na'}`}>{t.direction || '—'}</span> },
        { key: 'amount', label: 'Amount', className: 'num', sort: (t) => t.amount, render: (t) => <span className="nowrap">{fmtBtc(t.amount)}</span> },
        { key: 'cp', label: 'Counterparties', render: (t) => <Counterparties ids={t.counterparties} onSelect={onSelect} /> },
        { key: 'ip', label: 'Src IP', sort: (t) => t.src_ip, render: (t) => (t.src_ip ? <code>{t.src_ip}</code> : '—') },
        { key: 'port', label: 'Port', className: 'num', sort: (t) => t.dst_port, render: (t) => t.dst_port ?? '—' },
        { key: 'risk', label: 'Risk', sort: (t) => ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].indexOf(t.risk_level), render: (t) => <RiskBadge level={t.risk_level} compact /> },
        { key: 'tx', label: 'Txid', render: (t) => <IdText id={t.txid} head={6} tail={4} /> },
      ]}
      rows={rows}
      rowKey={(t, i) => `${t.txid}-${i}`}
      onRowClick={(t) => t.txid && onSelect({ type: 'transaction', id: String(t.txid) })}
    />
  );
}

function CountTable({ caption, rows, columns }) {
  if (!rows.length) return null;
  return <DataTable caption={caption} columns={columns} rows={rows} rowKey={(r, i) => `${caption}-${i}`} maxHeight={260} />;
}

export function NetworkTab({ network }) {
  if (!network) return <p className="muted small">No network observations for this wallet.</p>;
  const ips = asArray(network.ips);
  const ports = asArray(network.ports);
  const countries = asArray(network.countries);
  const asns = asArray(network.asns);
  return (
    <div className="stack">
      <KeyValues
        items={[
          ['Distinct IPs', fmtInt(ips.length)],
          ['Non-standard port ratio', isNum(network.nonstandard_port_ratio) ? fmtPct(network.nonstandard_port_ratio) : null],
          ['Wallets sharing an IP', isNum(network.shared_ip_wallets) ? fmtInt(network.shared_ip_wallets) : null],
          ['Countries', countries.length ? countries.map((c) => `${c.country} (${c.count})`).join(', ') : null],
          ['ASNs', asns.length ? asns.map((a) => `AS${a.asn} (${a.count})`).join(', ') : null],
        ]}
      />
      {ports.length > 0 && (
        <div className="chip-row" aria-label="Destination ports">
          {ports.map((p) => (
            <span key={p.port} className={`chip chip-small${Number(p.port) !== 8333 ? ' chip-warn' : ''}`} title={Number(p.port) !== 8333 ? 'Non-standard port' : 'Standard Bitcoin port'}>
              :{p.port} × {p.count}
            </span>
          ))}
        </div>
      )}
      <CountTable
        caption="Observed IPs"
        rows={ips}
        columns={[
          { key: 'ip', label: 'IP', sort: (r) => r.ip, render: (r) => <code>{r.ip}</code> },
          { key: 'country', label: 'Country', sort: (r) => r.country, render: (r) => r.country || '—' },
          { key: 'asn', label: 'ASN', sort: (r) => r.asn, render: (r) => (r.asn ? `AS${r.asn}` : '—') },
          { key: 'count', label: 'Count', className: 'num', sort: (r) => r.count, render: (r) => fmtInt(r.count) },
        ]}
      />
    </div>
  );
}

function EntityTab({ entity, walletId, onSelect }) {
  const wallets = asArray(entity?.wallets);
  if (!entity) return <p className="muted small">No entity information.</p>;
  return (
    <div className="stack">
      <KeyValues items={[['Entity', entity.entity_id], ['Co-owned wallets', fmtInt(entity.size ?? wallets.length)]]} />
      <p className="muted small">Wallets grouped by common-input ownership heuristics (likely controlled by the same party).</p>
      {wallets.length ? (
        <ul className="link-list">
          {wallets.map((id) => (
            <li key={id}>
              {String(id) === String(walletId)
                ? <><IdText id={id} /> <span className="muted small">(this wallet)</span></>
                : <EntityLink type="wallet" id={id} onSelect={onSelect} head={12} tail={8} />}
            </li>
          ))}
        </ul>
      ) : <p className="muted small">Single-address entity.</p>}
    </div>
  );
}

function TaintTab({ taint, onSelect }) {
  if (!taint || (!isNum(taint.score) && !asArray(taint.path).length)) {
    return <p className="muted small">No taint from seed wallets reaches this wallet.</p>;
  }
  const path = asArray(taint.path);
  return (
    <div className="stack">
      <KeyValues
        items={[
          ['Taint score', isNum(taint.score) ? fmtPct(taint.score, 1) : null],
          ['Tainted value', isNum(taint.tainted_value) ? fmtBtc(taint.tainted_value) : null],
          ['Hops from seed', isNum(taint.hops) ? fmtInt(taint.hops) : null],
          ['Seed', taint.seed ? <EntityLink key="seed" type="wallet" id={taint.seed} onSelect={onSelect} /> : null],
        ]}
      />
      {path.length > 0 && (
        <>
          <h4>Taint path</h4>
          <ol className="taint-path">
            {path.map((step, i) => (
              <li key={`${step.wallet_id}-${step.txid}-${i}`}>
                {step.wallet_id && <EntityLink type="wallet" id={step.wallet_id} onSelect={onSelect} head={6} tail={4} />}
                {step.txid && (
                  <span className="taint-hop">
                    <span aria-hidden="true">→</span>
                    <EntityLink type="transaction" id={step.txid} onSelect={onSelect} head={6} tail={4} />
                    <span aria-hidden="true">→</span>
                  </span>
                )}
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}

export default function WalletView({ datasetId, id, refresh, onSelect, seedSet, onToggleSeed, busy }) {
  const detail = useApi(`wallet|${datasetId}|${id}|${refresh}`, (signal) => getWallet(datasetId, id, signal));
  const d = detail.data || {};
  const isSeed = Boolean(d.is_seed) || seedSet.has(String(id));

  return (
    <ApiState result={detail} loadingText="Loading wallet…" operation="Loading wallet">
      <Header
        kind="Wallet"
        id={String(d.wallet_id ?? id)}
        score={d.risk_score}
        level={d.risk_level}
        confidence={d.confidence}
        isSeed={isSeed}
        chips={(
          <>
            {isNum(d.rank) && <span className="chip chip-small chip-muted">rank #{d.rank}</span>}
            {d.entity_id && <span className="chip chip-small" title="Entity (co-owned wallets)">Entity {d.entity_id}{isNum(d.entity_size) ? ` · ${d.entity_size}` : ''}</span>}
            {(d.cluster_id !== undefined && d.cluster_id !== null) && (
              <button type="button" className="chip chip-small chip-button" onClick={() => onSelect({ type: 'community', id: String(d.cluster_id) })} title="Open community">
                {d.cluster?.label || `Community ${d.cluster_id}`}
              </button>
            )}
          </>
        )}
        actions={(
          isSeed ? (
            <button type="button" className="btn btn-small" disabled={busy} onClick={() => onToggleSeed(String(id), false)}>Remove seed</button>
          ) : (
            <button type="button" className="btn btn-small btn-danger" disabled={busy} onClick={() => onToggleSeed(String(id), true)}>Mark as illicit seed</button>
          )
        )}
      >
        {d.top_reason && <p className="inv-summary">{d.top_reason}</p>}
      </Header>

      <WhyFlagged detail={d} />
      <GraphSection datasetId={datasetId} focusType="wallet" focusId={id} refresh={refresh} allowDepth onSelect={onSelect} />
      <EvidenceTabs
        tabs={[
          { id: 'tx', label: 'Transactions', count: asArray(d.transactions).length, render: () => <TransactionsTab transactions={d.transactions} onSelect={onSelect} /> },
          { id: 'net', label: 'Network', render: () => <NetworkTab network={d.network} /> },
          { id: 'entity', label: 'Entity', count: asArray(d.entity?.wallets).length || undefined, render: () => <EntityTab entity={d.entity} walletId={id} onSelect={onSelect} /> },
          { id: 'taint', label: 'Taint', render: () => <TaintTab taint={d.taint} onSelect={onSelect} /> },
          { id: 'patterns', label: 'Patterns', count: asArray(d.patterns).length, render: () => <PatternList patterns={d.patterns} onSelect={onSelect} /> },
          d.features && Object.keys(d.features).length > 0 && {
            id: 'features',
            label: 'Features',
            render: () => (
              <KeyValues items={Object.entries(d.features).map(([k, v]) => [k.replace(/_/g, ' '), isNum(v) ? fmtNum(v, 4) : String(v)])} />
            ),
          },
        ]}
      />
    </ApiState>
  );
}
