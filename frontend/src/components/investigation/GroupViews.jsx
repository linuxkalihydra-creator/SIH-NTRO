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
  roleLabel,
} from '../../lib/format.js';
import { ApiState, EntityLink, RiskBadge, StatusBox } from '../common.jsx';
import DataTable from '../DataTable.jsx';
import { EvidenceTabs, GraphSection, Header, KeyValues, Section } from './parts.jsx';

function useResolved(selection, lead, fetchList, datasetId, refresh, kind, match) {
  const fromLead = asArray(lead?.data).find(match);
  const provided = selection.data && match(selection.data) ? selection.data : null;
  const needFetch = !provided && !fromLead && lead?.status !== 'loading';
  const list = useApi(needFetch ? `${kind}|${datasetId}|${refresh}` : null, (signal) => fetchList(signal));
  const found = fromLead || provided || asArray(list.data).find(match) || null;
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
  if (Array.isArray(value)) {
    if (!value.length) return '—';
    const shown = value.slice(0, 6).map((v) => (typeof v === 'object' ? JSON.stringify(v) : isNum(v) ? fmtNum(v, 4) : String(v))).join(', ');
    return value.length > 6 ? `${shown}, … (${value.length})` : shown;
  }
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function Resolving({ state, what }) {
  if (state.found) return null;
  if (state.list.status === 'error') return <ApiState result={state.list} operation={`Loading ${what.toLowerCase()}`} />;
  if (state.needFetch && state.list.status === 'success') {
    return (
      <StatusBox kind="empty" title={`${what} not found`}>
        It is not part of the current analysis. Patterns and communities are renumbered when a dataset is analysed again.
      </StatusBox>
    );
  }
  return <StatusBox kind="loading" title={`Loading ${what.toLowerCase()}…`} />;
}

// Wallets that carry the structure come first, recipients last.
const ROLE_ORDER = [
  'layering_source', 'chain_hop', 'pass_through', 'layering_intermediate', 'coinjoin_input', 'layering_sink',
  'chain_exit', 'layering_exit', 'layering_change', 'pass_through_change', 'coinjoin_change', 'coinjoin_output',
  'peel_recipient', 'pass_through_recipient',
];
const roleRank = (role) => {
  const index = ROLE_ORDER.indexOf(role);
  return index < 0 ? ROLE_ORDER.length : index;
};

function PatternWallets({ pattern, onSelect, seedSet }) {
  const roles = pattern.roles && typeof pattern.roles === 'object' ? pattern.roles : {};
  const rows = asArray(pattern.wallets).map((wallet) => ({ wallet: String(wallet), role: roles[wallet] || '' }));
  if (!rows.length) return <p className="muted small">No wallets are recorded for this structure.</p>;
  return (
    <DataTable
      caption="Wallets in this structure and what each one does"
      maxHeight={340}
      initialSort={{ key: 'role', dir: 'asc' }}
      columns={[
        {
          key: 'wallet',
          label: 'Wallet',
          sort: (r) => r.wallet,
          render: (r) => (
            <span className="id-cell">
              <EntityLink type="wallet" id={r.wallet} onSelect={onSelect} head={12} tail={8} />
              {seedSet?.has(r.wallet) && <span className="seed-tag">SEED</span>}
            </span>
          ),
        },
        { key: 'role', label: 'Role in the structure', sort: (r) => roleRank(r.role), defaultDir: 'asc', render: (r) => (r.role ? roleLabel(r.role) : '—') },
      ]}
      rows={rows}
      rowKey={(r) => r.wallet}
      onRowClick={(r) => onSelect({ type: 'wallet', id: r.wallet })}
    />
  );
}

export function PatternView({ datasetId, selection, refresh, onSelect, patternsLead, seedSet, view, onViewChange }) {
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
            <span className="chip chip-small" title="How closely the structure matches the textbook shape (0–100%)">strength {fmtPct(p.strength)}</span>
            {isNum(p.total_value) && <span className="chip chip-small chip-muted">{fmtBtc(p.total_value)} moved</span>}
            {p.start_time && <span className="chip chip-small chip-muted">{fmtTime(p.start_time)} → {fmtTime(p.end_time)} UTC</span>}
          </>
        )}
      >
        {p.summary && <p className="inv-summary">{p.summary}</p>}
      </Header>
      <Section title="Structure details">
        <KeyValues
          items={[
            ['Wallets', fmtInt(asArray(p.wallets).length)],
            ['Transactions', fmtInt(asArray(p.transactions).length)],
            ...details.map(([k, v]) => [k.replace(/_/g, ' '), formatDetail(v)]),
          ]}
        />
      </Section>
      <GraphSection datasetId={datasetId} focusType="pattern" focusId={String(p.pattern_id)} refresh={refresh} onSelect={onSelect} view={view} onViewChange={onViewChange} />
      <EvidenceTabs
        active={view?.evidence}
        onChange={(tab) => onViewChange?.({ evidence: tab })}
        tabs={[
          { id: 'wallets', label: 'Wallets and roles', count: asArray(p.wallets).length, render: () => <PatternWallets pattern={p} onSelect={onSelect} seedSet={seedSet} /> },
          { id: 'transactions', label: 'Transactions', count: asArray(p.transactions).length, render: () => <IdList ids={p.transactions} type="transaction" onSelect={onSelect} empty="No transactions are recorded for this structure." /> },
        ]}
      />
    </>
  );
}

export function CommunityView({ datasetId, selection, refresh, onSelect, communitiesLead, seedSet, view, onViewChange }) {
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
  const patternCounts = Object.entries(c.pattern_counts || {}).sort((a, b) => b[1] - a[1]);
  const unassigned = Number(c.cluster_id) === -1;

  return (
    <>
      <Header
        kind="Community"
        id={c.label ? `${c.label}` : `Community ${c.cluster_id}`}
        score={c.max_risk}
        level={levelFromScore(c.max_risk)}
        chips={(
          <>
            <span className="chip chip-small chip-muted">highest wallet risk</span>
            <span className="chip chip-small">average risk {fmtScore(c.avg_risk)}</span>
            {c.flagged_count > 0 && <span className="chip chip-small chip-bad">{fmtInt(c.flagged_count)} HIGH or CRITICAL</span>}
          </>
        )}
      >
        <p className="muted small">
          {unassigned
            ? 'Entities the density clustering could not assign to any community: they trade too little, or too evenly, with everyone.'
            : 'Entities that trade with each other more than with the rest of the network, found by clustering embeddings of the payment graph.'}
        </p>
      </Header>
      <Section title="Community profile">
        <KeyValues
          items={[
            ['Wallets', fmtInt(c.wallet_count)],
            ['Entities', fmtInt(c.entity_count)],
            ['Average risk', fmtScore(c.avg_risk)],
            ['Highest risk', fmtScore(c.max_risk)],
            ['Flagged wallets', fmtInt(c.flagged_count)],
          ]}
        />
      </Section>
      <GraphSection datasetId={datasetId} focusType="cluster" focusId={String(c.cluster_id)} refresh={refresh} onSelect={onSelect} view={view} onViewChange={onViewChange} />
      <EvidenceTabs
        active={view?.evidence}
        onChange={(tab) => onViewChange?.({ evidence: tab })}
        tabs={[
          {
            id: 'wallets',
            label: 'Riskiest wallets',
            count: topWallets.length,
            render: () => (topWallets.length ? (
              <DataTable
                caption="Riskiest wallets in this community"
                initialSort={{ key: 'risk', dir: 'desc' }}
                columns={[
                  {
                    key: 'wallet',
                    label: 'Wallet',
                    sort: (w) => w.wallet_id,
                    render: (w) => (
                      <span className="id-cell">
                        <EntityLink type="wallet" id={w.wallet_id} level={w.risk_level} onSelect={onSelect} head={12} tail={8} />
                        {seedSet?.has(String(w.wallet_id)) && <span className="seed-tag">SEED</span>}
                      </span>
                    ),
                  },
                  { key: 'risk', label: 'Risk', sort: (w) => w.risk_score, render: (w) => <RiskBadge score={w.risk_score} level={w.risk_level} compact /> },
                ]}
                rows={topWallets}
                rowKey={(w) => String(w.wallet_id)}
                onRowClick={(w) => onSelect({ type: 'wallet', id: String(w.wallet_id) })}
              />
            ) : <p className="muted small">No wallets are listed for this community.</p>),
          },
          {
            id: 'patterns',
            label: 'Structures',
            count: patternCounts.reduce((acc, [, n]) => acc + (Number(n) || 0), 0),
            render: () => (patternCounts.length ? (
              <>
                <p className="muted small">Wallets of this community inside each kind of detected structure.</p>
                <KeyValues items={patternCounts.map(([type, n]) => [patternLabel(type), fmtInt(n)])} />
              </>
            ) : <p className="muted small">No detected structure involves this community.</p>),
          },
        ]}
      />
    </>
  );
}
