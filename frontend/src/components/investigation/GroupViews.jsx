/** Pattern and community views. Neither has a detail endpoint, so the object comes
 * from the selection (clicked row) or is looked up in the corresponding list. */

import { getClusters, getPatterns } from '../../api/client.js';
import useApi from '../../lib/useApi.js';
import {
  asArray,
  fmtBtc,
  fmtInt,
  fmtNum,
  fmtPct,
  fmtScore,
  fmtTime,
  isNum,
  levelFromScore,
  patternLabel,
  truncateId,
} from '../../lib/format.js';
import { ApiState, EntityLink, RiskBadge, StatusBox } from '../common.jsx';
import DataTable from '../DataTable.jsx';
import { EvidenceTabs, GraphSection, Header, KeyValues, Section } from './parts.jsx';

function useResolved(selection, lead, fetchList, datasetId, refresh, kind, match) {
  const fromLead = asArray(lead?.data).find(match);
  const provided = selection.data && match(selection.data) ? selection.data : null;
  const needFetch = !provided && !fromLead && lead?.status !== 'loading';
  const list = useApi(needFetch ? `${kind}|${datasetId}|${refresh}` : null, (signal) => fetchList(signal));
  const found = provided || fromLead || asArray(list.data).find(match) || null;
  return { found, list, needFetch };
}

function IdList({ ids, type, onSelect, empty }) {
  const list = asArray(ids);
  if (!list.length) return <p className="muted small">{empty}</p>;
  return (
    <ul className="link-list columns">
      {list.map((id) => <li key={id}><EntityLink type={type} id={id} onSelect={onSelect} head={10} tail={6} /></li>)}
    </ul>
  );
}

function formatDetail(value) {
  if (value === null || value === undefined) return '—';
  if (isNum(value)) return fmtNum(value, 4);
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (Array.isArray(value)) return value.map((v) => (typeof v === 'object' ? JSON.stringify(v) : String(v))).join(', ');
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function Resolving({ state, what }) {
  if (state.found) return null;
  if (state.list.status === 'error') return <ApiState result={state.list} operation={`Loading ${what}`} />;
  if (state.needFetch && state.list.status === 'success') {
    return <StatusBox kind="empty" title={`${what} not found`}>It may no longer exist after re-scoring.</StatusBox>;
  }
  return <StatusBox kind="loading" title={`Loading ${what}…`} />;
}

export function PatternView({ datasetId, selection, refresh, onSelect, patternsLead }) {
  const state = useResolved(
    selection,
    patternsLead,
    (signal) => getPatterns(datasetId, {}, signal),
    datasetId,
    refresh,
    'patterns',
    (p) => String(p?.pattern_id) === String(selection.id),
  );
  const p = state.found;
  if (!p) return <Resolving state={state} what="Pattern" />;
  const details = p.details && typeof p.details === 'object' ? Object.entries(p.details) : [];

  return (
    <>
      <Header
        kind={patternLabel(p.type)}
        id={String(p.pattern_id)}
        chips={(
          <>
            <span className="chip chip-small" title="Pattern strength">strength {fmtPct(p.strength)}</span>
            {isNum(p.total_value) && <span className="chip chip-small chip-muted">{fmtBtc(p.total_value)}</span>}
            {p.start_time && <span className="chip chip-small chip-muted">{fmtTime(p.start_time)} → {fmtTime(p.end_time)}</span>}
          </>
        )}
      >
        {p.summary && <p className="inv-summary">{p.summary}</p>}
      </Header>
      <Section title="Pattern details">
        <KeyValues
          items={[
            ['Wallets', fmtInt(asArray(p.wallets).length)],
            ['Transactions', fmtInt(asArray(p.transactions).length)],
            ...details.map(([k, v]) => [k.replace(/_/g, ' '), formatDetail(v)]),
          ]}
        />
      </Section>
      <GraphSection datasetId={datasetId} focusType="pattern" focusId={String(p.pattern_id)} refresh={refresh} onSelect={onSelect} />
      <EvidenceTabs
        tabs={[
          { id: 'wallets', label: 'Wallets', count: asArray(p.wallets).length, render: () => <IdList ids={p.wallets} type="wallet" onSelect={onSelect} empty="No wallets." /> },
          { id: 'txs', label: 'Transactions', count: asArray(p.transactions).length, render: () => <IdList ids={p.transactions} type="transaction" onSelect={onSelect} empty="No transactions." /> },
        ]}
      />
    </>
  );
}

export function CommunityView({ datasetId, selection, refresh, onSelect, communitiesLead }) {
  const state = useResolved(
    selection,
    communitiesLead,
    (signal) => getClusters(datasetId, signal),
    datasetId,
    refresh,
    'clusters',
    (c) => String(c?.cluster_id) === String(selection.id),
  );
  const c = state.found;
  if (!c) return <Resolving state={state} what="Community" />;
  const topWallets = asArray(c.top_wallets);
  const patternCounts = Object.entries(c.pattern_counts || {});
  const unassigned = Number(c.cluster_id) === -1;

  return (
    <>
      <Header
        kind="Community"
        id={c.label ? `${c.label}` : `Cluster ${c.cluster_id}`}
        score={c.max_risk}
        level={levelFromScore(c.max_risk)}
        chips={(
          <>
            <span className="chip chip-small chip-muted">cluster {c.cluster_id}</span>
            <span className="chip chip-small">avg risk {fmtScore(c.avg_risk)}</span>
          </>
        )}
      >
        {unassigned && <p className="inv-summary">Entities HDBSCAN could not assign to a community.</p>}
      </Header>
      <Section title="Community profile">
        <KeyValues
          items={[
            ['Wallets', fmtInt(c.wallet_count)],
            ['Entities', fmtInt(c.entity_count)],
            ['Average risk', fmtScore(c.avg_risk)],
            ['Maximum risk', fmtScore(c.max_risk)],
            ['Flagged wallets', fmtInt(c.flagged_count)],
          ]}
        />
      </Section>
      <GraphSection datasetId={datasetId} focusType="cluster" focusId={String(c.cluster_id)} refresh={refresh} onSelect={onSelect} />
      <EvidenceTabs
        tabs={[
          {
            id: 'wallets',
            label: 'Top wallets',
            count: topWallets.length,
            render: () => (topWallets.length ? (
              <DataTable
                caption="Top wallets in community"
                initialSort={{ key: 'risk', dir: 'desc' }}
                columns={[
                  { key: 'wallet', label: 'Wallet', sort: (w) => w.wallet_id, render: (w) => <code title={w.wallet_id}>{truncateId(w.wallet_id, 12, 8)}</code> },
                  { key: 'risk', label: 'Risk', sort: (w) => w.risk_score, render: (w) => <RiskBadge score={w.risk_score} level={w.risk_level} compact /> },
                ]}
                rows={topWallets}
                rowKey={(w) => String(w.wallet_id)}
                onRowClick={(w) => onSelect({ type: 'wallet', id: String(w.wallet_id) })}
              />
            ) : <p className="muted small">No wallets listed.</p>),
          },
          {
            id: 'patterns',
            label: 'Patterns',
            count: patternCounts.reduce((acc, [, n]) => acc + (Number(n) || 0), 0),
            render: () => (patternCounts.length ? (
              <KeyValues items={patternCounts.map(([type, n]) => [patternLabel(type), fmtInt(n)])} />
            ) : <p className="muted small">No detected patterns in this community.</p>),
          },
        ]}
      />
    </>
  );
}
