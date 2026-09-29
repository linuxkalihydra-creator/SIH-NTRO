import { getTransaction } from '../../api/client.js';
import useApi from '../../lib/useApi.js';
import { asArray, fmtBtc, fmtInt, fmtTime, isNum } from '../../lib/format.js';
import { ApiState, EntityLink, RiskBadge } from '../common.jsx';
import DataTable from '../DataTable.jsx';
import { EvidenceTabs, GraphSection, Header, KeyValues, WhyFlagged } from './parts.jsx';
import { PatternList } from './WalletView.jsx';

function FlowTable({ caption, rows, onSelect, seedSet }) {
  const list = asArray(rows);
  if (!list.length) return <p className="muted small">No {caption.toLowerCase()}.</p>;
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
        { key: 'risk', label: 'Risk', sort: (r) => ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].indexOf(r.risk_level), render: (r) => <RiskBadge level={r.risk_level} compact /> },
      ]}
      rows={list}
      rowKey={(r, i) => `${r.wallet_id}-${i}`}
      onRowClick={(r) => r.wallet_id && onSelect({ type: 'wallet', id: String(r.wallet_id) })}
    />
  );
}

export default function TransactionView({ datasetId, id, refresh, onSelect, seedSet }) {
  const detail = useApi(`tx|${datasetId}|${id}|${refresh}`, (signal) => getTransaction(datasetId, id, signal));
  const d = detail.data || {};
  const inputs = asArray(d.inputs);
  const outputs = asArray(d.outputs);

  return (
    <ApiState result={detail} loadingText="Loading transaction…" operation="Loading transaction">
      <Header
        kind="Transaction"
        id={String(d.txid ?? id)}
        score={d.risk_score}
        level={d.risk_level}
        confidence={d.confidence}
        chips={(
          <>
            {isNum(d.rank) && <span className="chip chip-small chip-muted">rank #{d.rank}</span>}
            {d.timestamp && <span className="chip chip-small chip-muted">{fmtTime(d.timestamp)}</span>}
            {d.country && <span className="chip chip-small">{d.country}</span>}
          </>
        )}
      >
        {d.top_reason && <p className="inv-summary">{d.top_reason}</p>}
      </Header>

      <WhyFlagged detail={d} />
      <GraphSection datasetId={datasetId} focusType="transaction" focusId={id} refresh={refresh} onSelect={onSelect} />
      <EvidenceTabs
        tabs={[
          {
            id: 'flow',
            label: 'Inputs / outputs',
            count: inputs.length + outputs.length,
            render: () => (
              <div className="flow-grid">
                <div>
                  <h4>Inputs ({d.input_count ?? inputs.length}) · {fmtBtc(d.total_input)}</h4>
                  <FlowTable caption="Inputs" rows={inputs} onSelect={onSelect} seedSet={seedSet} />
                </div>
                <div>
                  <h4>Outputs ({d.output_count ?? outputs.length}) · {fmtBtc(d.total_output)}</h4>
                  <FlowTable caption="Outputs" rows={outputs} onSelect={onSelect} seedSet={seedSet} />
                </div>
              </div>
            ),
          },
          {
            id: 'net',
            label: 'Network',
            render: () => (
              <KeyValues
                items={[
                  ['Source IP', d.src_ip ? <code key="src">{d.src_ip}</code> : null],
                  ['Destination IP', d.dst_ip ? <code key="dst">{d.dst_ip}</code> : null],
                  ['Destination port', isNum(d.dst_port) ? `${d.dst_port}${d.dst_port !== 8333 ? ' (non-standard)' : ''}` : d.dst_port],
                  ['Country', d.country],
                  ['Fee', isNum(d.fee) ? fmtBtc(d.fee) : null],
                  ['Inputs / outputs', `${fmtInt(d.input_count ?? inputs.length)} / ${fmtInt(d.output_count ?? outputs.length)}`],
                ]}
              />
            ),
          },
          { id: 'patterns', label: 'Patterns', count: asArray(d.patterns).length, render: () => <PatternList patterns={d.patterns} onSelect={onSelect} /> },
        ]}
      />
    </ApiState>
  );
}
