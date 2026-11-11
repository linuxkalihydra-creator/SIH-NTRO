import { getTransaction } from '../../api/client.js';
import useApi from '../../lib/useApi.js';
import {
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
import { ApiState, ConfidenceMeter, EntityLink, RiskBadge } from '../common.jsx';
import DataTable from '../DataTable.jsx';
import {
  EvidenceTabs,
  GraphSection,
  Header,
  IpLink,
  KeyValues,
  PatternList,
  ReportLink,
  Where,
  WhyFlagged,
} from './parts.jsx';

function FlowTable({ caption, rows, onSelect, seedSet }) {
  const list = asArray(rows);
  if (!list.length) return <p className="muted small">No {caption.toLowerCase()} are recorded (coinbase or unknown source).</p>;
  return (
    <DataTable
      caption={caption}
      maxHeight={300}
      initialSort={{ key: 'amount', dir: 'desc' }}
      columns={[
        {
          key: 'wallet',
          label: 'Wallet',
          sort: (r) => r.wallet_id,
          render: (r) => (
            <span className="id-cell">
              <EntityLink type="wallet" id={r.wallet_id} level={r.risk_level} onSelect={onSelect} />
              {seedSet.has(String(r.wallet_id)) && <span className="seed-tag">SEED</span>}
            </span>
          ),
        },
        { key: 'amount', label: 'Amount', className: 'num', sort: (r) => r.amount, render: (r) => <span className="nowrap">{fmtBtc(r.amount)}</span> },
        { key: 'risk', label: 'Wallet risk', sort: (r) => r.risk_score ?? levelRank(r.risk_level), render: (r) => <RiskBadge score={r.risk_score} level={r.risk_level} compact /> },
      ]}
      rows={list}
      rowKey={(r, i) => `${r.wallet_id}-${i}`}
      onRowClick={(r) => r.wallet_id && onSelect({ type: 'wallet', id: String(r.wallet_id) })}
    />
  );
}

const ROLE_HINT = {
  'first seen': 'The earliest sighting: the inferred origin of the transaction',
  'origin announcing again': 'The first-seen IP announcing the same transaction again',
  relay: 'A later sighting from another IP: a node passing the transaction on',
};

/** `listening` marks the side whose port is the node's listening port; the other side's port is a throwaway one. */
function Endpoint({ ip, port, onSelect, listening }) {
  if (!ip) return <span className="muted">—</span>;
  const odd = listening && Number(port) > 0 && !isStandardPort(port);
  return (
    <span className="endpoint">
      {onSelect ? <IpLink ip={ip} onSelect={onSelect} /> : <code>{ip}</code>}
      {Number(port) > 0 && (
        <span className={odd ? 'port-odd' : 'muted'} title={odd ? 'Listening on a non-standard port' : listening ? 'Listening port' : 'Outgoing (ephemeral) port'}>:{port}</span>
      )}
    </span>
  );
}

/** The listening side of a connection: whichever uses the standard port, else the destination (the backend's rule). */
const listensOnSource = (o) => isStandardPort(o.src_port) && !isStandardPort(o.dst_port);

function NetworkTab({ detail, onSelect }) {
  const origin = detail.origin || {};
  const observations = asArray(detail.observations);
  if (!origin.ip && !observations.some((observation) => observation.src_ip)) {
    return <p className="muted small">No source IP was recorded for this transaction, so there is no network evidence to show.</p>;
  }
  const lead = isNum(origin.lead_seconds) ? `${fmtNum(origin.lead_seconds, 2)} s ahead of the next relay` : 'no later relay to compare with';
  return (
    <div className="stack">
      <div className="origin-box">
        <div className="origin-head">
          <span className="origin-label">Inferred origin</span>
          <IpLink ip={origin.ip} onSelect={onSelect} copy />
          <Where country={origin.country} asn={origin.asn} org={origin.as_org} />
          <span className={`chip chip-small ${origin.attributed ? 'chip-good' : 'chip-warn'}`}>
            {origin.attributed ? 'Attributed to the spending wallets' : 'Attribution withheld'}
          </span>
        </div>
        <div className="origin-facts">
          <ConfidenceMeter value={origin.confidence} label="Origin confidence" />
          <span><span className="muted">Lead time</span> <strong>{lead}</strong></span>
          <span><span className="muted">Sightings</span> <strong>{fmtInt(origin.observations ?? observations.length)}</strong></span>
        </div>
        {origin.explanation && <p className="small">{origin.explanation}</p>}
      </div>

      <h4>Every sighting of this transaction <span className="muted small">— earliest first</span></h4>
      {observations.length ? (
        <DataTable
          caption="Network observations of this transaction"
          maxHeight={320}
          columns={[
            { key: 'time', label: 'Seen at (UTC)', sort: (o) => o.timestamp, defaultDir: 'asc', render: (o) => <span className="nowrap">{fmtTime(o.timestamp)}</span> },
            { key: 'delta', label: 'After first', className: 'num', sort: (o) => o.seconds_after_first, defaultDir: 'asc', render: (o) => (o.seconds_after_first > 0 ? `+${fmtNum(o.seconds_after_first, 2)} s` : '—') },
            { key: 'from', label: 'Announced by', sort: (o) => o.src_ip, render: (o) => <Endpoint ip={o.src_ip} port={o.src_port} onSelect={onSelect} listening={listensOnSource(o)} /> },
            { key: 'to', label: 'To', sort: (o) => o.dst_ip, render: (o) => <Endpoint ip={o.dst_ip} port={o.dst_port} listening={!listensOnSource(o)} /> },
            { key: 'where', label: 'Country · network', sort: (o) => o.country, render: (o) => <Where country={o.country} asn={o.asn} org={o.as_org} /> },
            {
              key: 'role',
              label: 'Role',
              sort: (o) => o.role,
              render: (o) => (
                <span className={`chip chip-small${o.role === 'first seen' ? ' chip-accent' : ' chip-muted'}`} title={ROLE_HINT[o.role]}>{o.role || '—'}</span>
              ),
            },
            {
              key: 'relay',
              label: 'Relays others',
              className: 'num',
              title: 'Share of this IP’s sightings that pass on transactions first seen elsewhere; 50% or more marks a relay node',
              sort: (o) => o.relay_ratio,
              render: (o) => (isNum(o.relay_ratio) ? <span className={o.relay_ratio >= 0.5 ? 'port-odd' : undefined}>{fmtPct(o.relay_ratio)}</span> : '—'),
            },
          ]}
          rows={observations}
          rowKey={(o, i) => `${o.src_ip}-${o.timestamp}-${i}`}
          initialSort={{ key: 'time', dir: 'asc' }}
        />
      ) : (
        <p className="muted small">The dataset holds no separate sightings for this transaction.</p>
      )}
      {observations.length === 1 && (
        <p className="muted small">Seen once only: with no later relay to compare against, timing cannot confirm that this IP created the transaction.</p>
      )}
    </div>
  );
}

export default function TransactionView({ datasetId, id, refresh, onSelect, seedSet, view, onViewChange }) {
  const detail = useApi(`tx|${datasetId}|${id}|${refresh}`, (signal) => getTransaction(datasetId, id, signal));
  const d = detail.data || {};
  const inputs = asArray(d.inputs);
  const outputs = asArray(d.outputs);
  const sightings = asArray(d.observations).filter((observation) => observation.src_ip).length;

  return (
    <ApiState
      result={detail}
      loadingText="Loading transaction…"
      operation="Loading transaction"
      notFoundText={<>Transaction <code>{id}</code> does not appear in this dataset.</>}
    >
      <Header
        kind="Transaction"
        id={String(d.txid ?? id)}
        score={d.risk_score}
        level={d.risk_level}
        confidence={d.confidence}
        chips={(
          <>
            {isNum(d.rank) && <span className="chip chip-small chip-muted">rank #{fmtInt(d.rank)}</span>}
            {d.timestamp && <span className="chip chip-small chip-muted">{fmtTime(d.timestamp)} UTC</span>}
            {isNum(d.total_output) && <span className="chip chip-small">{fmtBtc(d.total_output)}</span>}
            {d.src_ip && (
              <button type="button" className="chip chip-small chip-button" onClick={() => onSelect({ type: 'ip', id: String(d.src_ip) })} title="Open the IP this transaction was first seen from">
                first seen from {d.src_ip}{d.country ? ` · ${d.country}` : ''}
              </button>
            )}
          </>
        )}
        actions={<ReportLink datasetId={datasetId} kind="transaction" id={String(d.txid ?? id)} />}
      >
        {d.top_reason && <p className="inv-summary">{d.top_reason}</p>}
      </Header>

      <WhyFlagged detail={d} onSelect={onSelect} />
      <GraphSection datasetId={datasetId} focusType="transaction" focusId={id} refresh={refresh} onSelect={onSelect} view={view} onViewChange={onViewChange} />
      <EvidenceTabs
        active={view?.evidence}
        onChange={(tab) => onViewChange?.({ evidence: tab })}
        tabs={[
          {
            id: 'flow',
            label: 'Inputs / outputs',
            count: inputs.length + outputs.length,
            render: () => (
              <div className="flow-grid">
                <div>
                  <h4>{plural(d.input_count ?? inputs.length, 'input')} · {fmtBtc(d.total_input)}</h4>
                  <FlowTable caption="Inputs" rows={inputs} onSelect={onSelect} seedSet={seedSet} />
                </div>
                <div>
                  <h4>{plural(d.output_count ?? outputs.length, 'output')} · {fmtBtc(d.total_output)}</h4>
                  <FlowTable caption="Outputs" rows={outputs} onSelect={onSelect} seedSet={seedSet} />
                </div>
              </div>
            ),
          },
          { id: 'network', label: 'Network', count: sightings, render: () => <NetworkTab detail={d} onSelect={onSelect} /> },
          { id: 'patterns', label: 'Patterns', count: asArray(d.patterns).length, render: () => <PatternList patterns={d.patterns} onSelect={onSelect} emptyText="Not part of any detected structure." /> },
          {
            id: 'details',
            label: 'Details',
            render: () => (
              <KeyValues
                items={[
                  ['Time (UTC)', d.timestamp ? fmtTime(d.timestamp) : null],
                  ['Inputs / outputs', `${fmtInt(d.input_count ?? inputs.length)} / ${fmtInt(d.output_count ?? outputs.length)}`],
                  ['Total in', isNum(d.total_input) ? fmtBtc(d.total_input) : null],
                  ['Total out', isNum(d.total_output) ? fmtBtc(d.total_output) : null],
                  ['Fee', isNum(d.fee) ? fmtBtc(d.fee) : null],
                  ['Fee rate', isNum(d.fee_rate) && d.fee_rate > 0 ? `${fmtNum(d.fee_rate, 1)} sat/vB` : null, 'Fee divided by the transaction size'],
                  ['Size', Number(d.size) > 0 ? `${fmtInt(Number(d.size))} vB` : null],
                  ['Script type', d.script_type || null],
                  ['Listening port', Number(d.dst_port) > 0 ? `${d.dst_port}${isStandardPort(d.dst_port) ? '' : ' (non-standard)'}` : null],
                  ['Sightings', isNum(d.relay_count) ? fmtInt(d.relay_count) : null],
                ]}
              />
            ),
          },
        ]}
      />
    </ApiState>
  );
}
