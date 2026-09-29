import { useMemo, useState } from 'react';
import DataTable from './DataTable.jsx';
import { ApiState, IdText, RiskBadge, Tabs } from './common.jsx';
import {
  RISK_LEVELS,
  asArray,
  fmtBtc,
  fmtInt,
  fmtPct,
  fmtScore,
  levelFromScore,
  normLevel,
  patternLabel,
  sumValues,
} from '../lib/format.js';

const TABS = [
  { id: 'wallets', label: 'Wallets' },
  { id: 'transactions', label: 'Transactions' },
  { id: 'patterns', label: 'Patterns' },
  { id: 'communities', label: 'Communities' },
];

const DEFAULT_FILTER = { level: '', type: '', q: '' };

const includes = (haystack, needle) => haystack.some((v) => String(v ?? '').toLowerCase().includes(needle));

function Reason({ text }) {
  return <span className="reason-cell" title={text || undefined}>{text || <span className="muted">—</span>}</span>;
}

function useColumns(tab, seedSet) {
  return useMemo(() => {
    const riskCol = {
      key: 'risk',
      label: 'Risk',
      sort: (r) => r.risk_score,
      render: (r) => <RiskBadge score={r.risk_score} level={r.risk_level} compact />,
    };
    const confCol = {
      key: 'conf',
      label: 'Conf.',
      className: 'num',
      title: 'Confidence',
      sort: (r) => r.confidence,
      render: (r) => fmtPct(r.confidence),
    };
    const rankCol = { key: 'rank', label: '#', className: 'num', sort: (r) => r.rank, defaultDir: 'asc', render: (r, i) => r.rank ?? i + 1 };
    if (tab === 'wallets') {
      return [
        rankCol,
        {
          key: 'id',
          label: 'Wallet',
          sort: (r) => r.wallet_id,
          render: (r) => (
            <span className="id-cell">
              <IdText id={r.wallet_id} head={7} tail={5} />
              {(r.is_seed || seedSet.has(String(r.wallet_id))) && <span className="seed-tag" title="Seed wallet">SEED</span>}
            </span>
          ),
        },
        riskCol,
        confCol,
        { key: 'reason', label: 'Top reason', sort: (r) => r.top_reason, render: (r) => <Reason text={r.top_reason} /> },
      ];
    }
    if (tab === 'transactions') {
      return [
        rankCol,
        { key: 'id', label: 'Transaction', sort: (r) => r.txid, render: (r) => <IdText id={r.txid} head={7} tail={5} /> },
        riskCol,
        confCol,
        { key: 'reason', label: 'Top reason', sort: (r) => r.top_reason, render: (r) => <Reason text={r.top_reason} /> },
      ];
    }
    if (tab === 'patterns') {
      return [
        { key: 'type', label: 'Type', sort: (r) => r.type, render: (r) => <span className="chip chip-small">{patternLabel(r.type)}</span> },
        { key: 'strength', label: 'Strength', className: 'num', sort: (r) => r.strength, render: (r) => fmtPct(r.strength) },
        { key: 'wallets', label: 'Wal.', className: 'num', title: 'Wallets', sort: (r) => asArray(r.wallets).length, render: (r) => asArray(r.wallets).length },
        { key: 'txs', label: 'Txs', className: 'num', sort: (r) => asArray(r.transactions).length, render: (r) => asArray(r.transactions).length },
        { key: 'summary', label: 'Summary', sort: (r) => r.summary, render: (r) => <Reason text={r.summary || (r.total_value ? fmtBtc(r.total_value) : '')} /> },
      ];
    }
    return [
      {
        key: 'label',
        label: 'Community',
        sort: (r) => r.label ?? r.cluster_id,
        render: (r) => (
          <span className="reason-cell" title={`Cluster ${r.cluster_id}`}>
            {r.label || `Cluster ${r.cluster_id}`}
            {Number(r.cluster_id) === -1 && <span className="muted"> (unassigned)</span>}
          </span>
        ),
      },
      { key: 'wallets', label: 'Wal.', className: 'num', title: 'Wallets', sort: (r) => r.wallet_count, render: (r) => fmtInt(r.wallet_count) },
      { key: 'entities', label: 'Ent.', className: 'num', title: 'Entities', sort: (r) => r.entity_count, render: (r) => fmtInt(r.entity_count) },
      { key: 'avg', label: 'Avg', className: 'num', title: 'Average risk', sort: (r) => r.avg_risk, render: (r) => fmtScore(r.avg_risk) },
      { key: 'max', label: 'Max', title: 'Maximum risk', sort: (r) => r.max_risk, render: (r) => <RiskBadge score={r.max_risk} level={levelFromScore(r.max_risk)} compact /> },
      { key: 'flagged', label: 'Flag.', className: 'num', title: 'Flagged wallets (CRITICAL/HIGH)', sort: (r) => r.flagged_count, render: (r) => fmtInt(r.flagged_count) },
    ];
  }, [tab, seedSet]);
}

const rowKeys = {
  wallets: (r) => `wallet:${r.wallet_id}`,
  transactions: (r) => `transaction:${r.txid}`,
  patterns: (r) => `pattern:${r.pattern_id}`,
  communities: (r) => `community:${r.cluster_id}`,
};

const toSelection = {
  wallets: (r) => ({ type: 'wallet', id: String(r.wallet_id) }),
  transactions: (r) => ({ type: 'transaction', id: String(r.txid) }),
  patterns: (r) => ({ type: 'pattern', id: String(r.pattern_id), data: r }),
  communities: (r) => ({ type: 'community', id: String(r.cluster_id), data: r }),
};

function rowLevel(tab, row) {
  if (tab === 'communities') return levelFromScore(row.max_risk);
  return normLevel(row.risk_level);
}

function searchable(tab, r) {
  if (tab === 'wallets') return [r.wallet_id, r.top_reason, r.entity_id, ...asArray(r.patterns)];
  if (tab === 'transactions') return [r.txid, r.top_reason, r.src_ip, r.dst_ip, r.country, ...asArray(r.patterns)];
  if (tab === 'patterns') return [r.pattern_id, r.type, patternLabel(r.type), r.summary, ...asArray(r.wallets)];
  return [r.cluster_id, r.label, ...asArray(r.top_wallets).map((w) => w?.wallet_id)];
}

export default function LeadsPanel({ tab, onTabChange, leads, overview, selection, seedSet, onSelect }) {
  const [filters, setFilters] = useState({});
  const filter = filters[tab] || DEFAULT_FILTER;
  const setFilter = (patch) => setFilters((current) => ({ ...current, [tab]: { ...filter, ...patch } }));

  const result = leads[tab];
  const rows = asArray(result.data);
  const columns = useColumns(tab, seedSet);

  const filtered = useMemo(() => {
    const q = filter.q.trim().toLowerCase();
    return rows.filter((row) => {
      if (tab === 'patterns') {
        if (filter.type && row.type !== filter.type) return false;
      } else if (filter.level && rowLevel(tab, row) !== filter.level) {
        return false;
      }
      return !q || includes(searchable(tab, row), q);
    });
  }, [rows, filter, tab]);

  const patternTypes = useMemo(() => {
    const fromOverview = Object.keys(overview.patterns || {});
    const fromRows = rows.map((r) => r.type).filter(Boolean);
    return [...new Set([...fromOverview, ...(tab === 'patterns' ? fromRows : [])])];
  }, [overview.patterns, rows, tab]);

  const selectedKey = selection ? `${selection.type}:${selection.id}` : null;
  const stats = overview.stats || {};
  const tabs = TABS.map((t) => ({
    ...t,
    count: {
      wallets: leads.wallets.data ? asArray(leads.wallets.data).length : undefined,
      transactions: leads.transactions.data ? asArray(leads.transactions.data).length : undefined,
      patterns: leads.patterns.data ? asArray(leads.patterns.data).length : sumValues(overview.patterns) || undefined,
      communities: leads.communities.data ? asArray(leads.communities.data).length : stats.communities,
    }[t.id],
  }));

  return (
    <section className="panel leads-panel" aria-labelledby="leads-title">
      <div className="panel-head">
        <h2 id="leads-title">Leads</h2>
        <span className="muted small">ranked by risk · click a row to investigate</span>
      </div>
      <Tabs tabs={tabs} active={tab} onChange={onTabChange} label="Lead type" />
      <div className="filter-row">
        {tab === 'patterns' ? (
          <label>
            <span className="sr-only">Pattern type</span>
            <select value={filter.type} onChange={(e) => setFilter({ type: e.target.value })}>
              <option value="">All types</option>
              {patternTypes.map((t) => <option key={t} value={t}>{patternLabel(t)}</option>)}
            </select>
          </label>
        ) : (
          <label>
            <span className="sr-only">Risk level</span>
            <select value={filter.level} onChange={(e) => setFilter({ level: e.target.value })}>
              <option value="">{tab === 'communities' ? 'All max-risk levels' : 'All levels'}</option>
              {RISK_LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
          </label>
        )}
        <label className="search-field">
          <span className="sr-only">Search leads</span>
          <input
            type="search"
            placeholder={tab === 'communities' ? 'Search label or wallet…' : 'Search id or reason…'}
            value={filter.q}
            onChange={(e) => setFilter({ q: e.target.value })}
          />
        </label>
        {result.status === 'success' && (
          <span className="muted small filter-count">{filtered.length}/{rows.length}</span>
        )}
      </div>
      <div className="leads-body">
        <ApiState result={result} loadingText={`Loading ${tab}…`} operation={`Loading ${tab}`} empty={rows.length === 0} emptyText={`No ${tab} returned for this dataset.`}>
          <DataTable
            key={tab}
            caption={`${tab} leads`}
            columns={columns}
            rows={filtered}
            rowKey={rowKeys[tab]}
            selectedKey={selectedKey}
            onRowClick={(row) => onSelect(toSelection[tab](row))}
            emptyText="No rows match the filters."
          />
        </ApiState>
      </div>
    </section>
  );
}
