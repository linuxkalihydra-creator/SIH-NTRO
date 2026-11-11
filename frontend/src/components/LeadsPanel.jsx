import { useMemo, useState } from 'react';
import DataTable from './DataTable.jsx';
import { ApiState, IdText, RiskBadge, Spinner, Tabs, Tags } from './common.jsx';
import {
  ACTOR_RULE,
  RISK_LEVELS,
  SIGNALS,
  SIGNAL_LABELS,
  asArray,
  countryName,
  fmtAsn,
  fmtBtc,
  fmtCompact,
  fmtInt,
  fmtPct,
  fmtScore,
  isNum,
  levelFromScore,
  normLevel,
  patternLabel,
  plural,
  sumValues,
} from '../lib/format.js';

const TABS = [
  { id: 'wallets', label: 'Wallets' },
  { id: 'transactions', label: 'Transactions' },
  { id: 'actors', label: 'Actors' },
  { id: 'ips', label: 'IPs' },
  { id: 'patterns', label: 'Patterns' },
  { id: 'communities', label: 'Communities' },
];

/** Tabs whose risk-level filter and row limit are applied by the server. */
const SERVER_TABS = ['wallets', 'transactions', 'actors', 'ips'];
const NEXT_LIMIT = [300, 500, 1000, 2000, 5000];

const NOUN = {
  wallets: 'wallets',
  transactions: 'transactions',
  actors: 'actors',
  ips: 'IPs',
  patterns: 'patterns',
  communities: 'communities',
};

const HELP = {
  wallets: 'Addresses ranked by fused risk',
  transactions: 'Transactions ranked by fused risk',
  actors: 'Entities operated together, ranked by their riskiest wallet',
  ips: 'IPs that originated transactions, ranked by the risk of that traffic',
  patterns: 'Detected laundering structures, strongest first',
  communities: 'Groups of entities that mostly trade among themselves',
};

const DISTRIBUTION = {
  wallets: 'risk_distribution',
  transactions: 'transaction_risk_distribution',
  actors: 'actor_risk_distribution',
  ips: 'ip_risk_distribution',
};

const LOCAL_FILTER = { level: '', type: '', q: '', hideSeeds: false };

const includes = (haystack, needle) => haystack.some((v) => String(v ?? '').toLowerCase().includes(needle));

function Reason({ text }) {
  return <span className="reason-cell" title={text || undefined}>{text || <span className="muted">—</span>}</span>;
}

/** Risk points by signal as a small stacked bar: its length is the score, its colours say why. */
function SignalBar({ contributions }) {
  const parts = SIGNALS
    .map((key) => ({ key, points: Math.max(0, Number(contributions?.[key]) || 0) }))
    .filter((part) => part.points > 0);
  if (!parts.length) return <span className="muted">—</span>;
  const text = parts.map((part) => `${SIGNAL_LABELS[part.key]} ${part.points.toFixed(1)}`).join(' · ');
  return (
    <span className="signal-bar" role="img" aria-label={`Risk points: ${text}`} title={`Risk points: ${text}`}>
      {parts.map((part) => <span key={part.key} className={`sig-${part.key}`} style={{ width: `${Math.min(100, part.points)}%` }} />)}
    </span>
  );
}

function useColumns(tab, seedSet) {
  return useMemo(() => {
    const rankCol = { key: 'rank', label: '#', className: 'num', sort: (r) => r.rank, defaultDir: 'asc', render: (r, i) => r.rank ?? i + 1 };
    const riskCol = {
      key: 'risk',
      label: 'Risk',
      sort: (r) => r.risk_score,
      render: (r) => <RiskBadge score={r.risk_score} level={r.risk_level} compact />,
    };
    const confCol = {
      key: 'conf',
      label: 'Conf.',
      className: 'num col-optional',
      title: 'Confidence: how much independent evidence supports the score',
      sort: (r) => r.confidence,
      render: (r) => fmtPct(r.confidence),
    };
    const signalCol = {
      key: 'signals',
      label: 'Signals',
      className: 'col-signals',
      title: 'Risk points by signal: anomaly, pattern, taint, network',
      render: (r) => <SignalBar contributions={r.contributions} />,
    };
    const reasonCol = { key: 'reason', label: 'Top reason', sort: (r) => r.top_reason, render: (r) => <Reason text={r.top_reason} /> };

    if (tab === 'wallets') {
      return [
        rankCol,
        {
          key: 'id',
          label: 'Wallet',
          sort: (r) => r.wallet_id,
          render: (r) => {
            const tags = [...new Set([...(r.is_seed || seedSet.has(String(r.wallet_id)) ? ['seed'] : []), ...asArray(r.tags)])];
            return (
              <span className="id-cell">
                <IdText id={r.wallet_id} head={7} tail={5} />
                <Tags tags={tags} single />
              </span>
            );
          },
        },
        riskCol,
        confCol,
        signalCol,
        reasonCol,
      ];
    }
    if (tab === 'transactions') {
      return [
        rankCol,
        { key: 'id', label: 'Transaction', sort: (r) => r.txid, render: (r) => <IdText id={r.txid} head={7} tail={5} /> },
        riskCol,
        confCol,
        signalCol,
        reasonCol,
      ];
    }
    if (tab === 'actors') {
      return [
        rankCol,
        {
          key: 'id',
          label: 'Actor',
          sort: (r) => r.actor_id,
          render: (r) => (
            <span className="id-cell">
              <IdText id={r.actor_id} head={12} tail={6} copy={false} />
              {r.seed_wallets > 0 && <span className="seed-tag" title={`${plural(r.seed_wallets, 'seed wallet')} among its addresses`}>SEED</span>}
            </span>
          ),
        },
        riskCol,
        { key: 'wallets', label: 'Wal.', className: 'num', title: 'Wallets (addresses) of this actor', sort: (r) => r.wallets, render: (r) => fmtInt(r.wallets) },
        { key: 'entities', label: 'Ent.', className: 'num col-optional', title: 'Entities operated together', sort: (r) => r.entities, render: (r) => fmtInt(r.entities) },
        {
          key: 'flagged',
          label: 'Flag.',
          className: 'num',
          title: 'Wallets at HIGH or CRITICAL risk',
          sort: (r) => r.flagged_wallets,
          render: (r) => (r.flagged_wallets > 0 ? <strong>{fmtInt(r.flagged_wallets)}</strong> : <span className="muted">0</span>),
        },
        { key: 'ips', label: 'IPs', className: 'num col-optional', title: 'IPs its spends were first seen from', sort: (r) => r.ips, render: (r) => fmtInt(r.ips) },
        { key: 'reason', label: 'Riskiest wallet: top reason', sort: (r) => r.top_reason, render: (r) => <Reason text={r.top_reason} /> },
      ];
    }
    if (tab === 'ips') {
      return [
        rankCol,
        { key: 'id', label: 'IP', sort: (r) => r.ip, render: (r) => <IdText id={r.ip} head={24} tail={12} copy={false} /> },
        riskCol,
        confCol,
        {
          key: 'where',
          label: 'Country · network',
          sort: (r) => `${r.country || ''} ${r.as_org || ''}`,
          render: (r) => {
            const network = fmtAsn(r.asn, r.as_org);
            const text = [r.country || null, network !== '—' ? network : null].filter(Boolean).join(' · ');
            return <span className="where-cell" title={text ? `${countryName(r.country)} · ${network}` : undefined}>{text || <span className="muted">—</span>}</span>;
          },
        },
        {
          key: 'role',
          label: 'Role',
          sort: (r) => r.role,
          render: (r) => <span className={`role-text${r.role === 'relay node' ? ' port-odd' : ''}`}>{r.role || '—'}</span>,
        },
        {
          key: 'flagged',
          label: 'Flagged',
          className: 'num',
          title: 'Wallets at HIGH or CRITICAL risk / all wallets that broadcast from the IP',
          sort: (r) => r.flagged_wallets,
          render: (r) => (
            <span className="nowrap">
              {r.flagged_wallets > 0 ? <strong>{fmtInt(r.flagged_wallets)}</strong> : <span className="muted">0</span>}
              <span className="muted"> / {fmtInt(r.wallets)}</span>
            </span>
          ),
        },
        { key: 'tx', label: 'Txs', className: 'num col-optional', title: 'Transactions first seen from this IP', sort: (r) => r.originated, render: (r) => fmtInt(r.originated) },
      ];
    }
    if (tab === 'patterns') {
      return [
        { key: 'type', label: 'Type', sort: (r) => r.type, render: (r) => <span className="chip chip-small">{patternLabel(r.type)}</span> },
        { key: 'strength', label: 'Strength', className: 'num', title: 'How closely it matches the textbook shape', sort: (r) => r.strength, render: (r) => fmtPct(r.strength) },
        { key: 'wallets', label: 'Wal.', className: 'num', title: 'Wallets', sort: (r) => asArray(r.wallets).length, render: (r) => asArray(r.wallets).length },
        { key: 'txs', label: 'Txs', className: 'num', title: 'Transactions', sort: (r) => asArray(r.transactions).length, render: (r) => asArray(r.transactions).length },
        { key: 'value', label: 'Value', className: 'num col-optional', sort: (r) => r.total_value, render: (r) => <span className="nowrap">{isNum(r.total_value) ? fmtBtc(Number(r.total_value.toPrecision(4))) : '—'}</span> },
        { key: 'summary', label: 'Summary', sort: (r) => r.summary, render: (r) => <Reason text={r.summary} /> },
      ];
    }
    return [
      {
        key: 'label',
        label: 'Community',
        sort: (r) => r.label ?? r.cluster_id,
        render: (r) => <span className="reason-cell" title={r.label || undefined}>{r.label || `Community ${r.cluster_id}`}</span>,
      },
      { key: 'wallets', label: 'Wal.', className: 'num', title: 'Wallets', sort: (r) => r.wallet_count, render: (r) => fmtInt(r.wallet_count) },
      { key: 'entities', label: 'Ent.', className: 'num', title: 'Entities', sort: (r) => r.entity_count, render: (r) => fmtInt(r.entity_count) },
      { key: 'avg', label: 'Avg', className: 'num', title: 'Average wallet risk', sort: (r) => r.avg_risk, render: (r) => fmtScore(r.avg_risk) },
      { key: 'max', label: 'Max', title: 'Highest wallet risk', sort: (r) => r.max_risk, render: (r) => <RiskBadge score={r.max_risk} level={levelFromScore(r.max_risk)} compact /> },
      {
        key: 'flagged',
        label: 'Flag.',
        className: 'num',
        title: 'Wallets at HIGH or CRITICAL risk',
        sort: (r) => r.flagged_count,
        render: (r) => (r.flagged_count > 0 ? <strong>{fmtInt(r.flagged_count)}</strong> : <span className="muted">0</span>),
      },
    ];
  }, [tab, seedSet]);
}

const rowKeys = {
  wallets: (r) => `wallet:${r.wallet_id}`,
  transactions: (r) => `transaction:${r.txid}`,
  actors: (r) => `actor:${r.actor_id}`,
  ips: (r) => `ip:${r.ip}`,
  patterns: (r) => `pattern:${r.pattern_id}`,
  communities: (r) => `community:${r.cluster_id}`,
};

const toSelection = {
  wallets: (r) => ({ type: 'wallet', id: String(r.wallet_id) }),
  transactions: (r) => ({ type: 'transaction', id: String(r.txid) }),
  actors: (r) => ({ type: 'actor', id: String(r.actor_id) }),
  ips: (r) => ({ type: 'ip', id: String(r.ip) }),
  patterns: (r) => ({ type: 'pattern', id: String(r.pattern_id), data: r }),
  communities: (r) => ({ type: 'community', id: String(r.cluster_id), data: r }),
};

function searchable(tab, r) {
  if (tab === 'wallets') return [r.wallet_id, r.top_reason, r.entity_id, r.actor_id, ...asArray(r.patterns), ...asArray(r.tags)];
  if (tab === 'transactions') return [r.txid, r.top_reason, r.src_ip, r.dst_ip, r.country, ...asArray(r.patterns)];
  if (tab === 'actors') return [r.actor_id, r.top_reason, r.top_wallet, ...Object.keys(r.patterns || {})];
  if (tab === 'ips') return [r.ip, r.country, countryName(r.country), r.as_org, r.asn ? `AS${r.asn}` : '', r.role, r.top_reason];
  if (tab === 'patterns') return [r.pattern_id, r.type, patternLabel(r.type), r.summary, ...asArray(r.wallets)];
  return [r.cluster_id, r.label, ...asArray(r.top_wallets).map((w) => w?.wallet_id)];
}

const PLACEHOLDER = {
  wallets: 'Filter by id, reason, entity…',
  transactions: 'Filter by id, reason, IP…',
  actors: 'Filter by id or reason…',
  ips: 'Filter by IP, country, network…',
  patterns: 'Filter by summary or wallet…',
  communities: 'Filter by label or wallet…',
};

function emptyText(tab, level, relays, hasIps) {
  if (tab === 'ips' && !hasIps) return 'The dataset records no source IPs, so there are no IP leads.';
  if (level) return `No ${level} ${NOUN[tab]} in this dataset.`;
  if (tab === 'ips') {
    return relays
      ? 'No IP announced a transaction in this dataset.'
      : 'No IP originated a transaction in this dataset. Tick “Include relays” to list the IPs that only passed transactions on.';
  }
  if (tab === 'patterns') return 'No laundering structure was detected in this dataset.';
  if (tab === 'communities') return 'No communities were found in this dataset.';
  return `No ${NOUN[tab]} in this dataset.`;
}

export default function LeadsPanel({ tab, onTabChange, leads, overview, selection, seedSet, onSelect, query, onQueryChange }) {
  const [filters, setFilters] = useState({});
  const filter = filters[tab] || LOCAL_FILTER;
  const setFilter = (patch) => setFilters((current) => ({ ...current, [tab]: { ...filter, ...patch } }));

  const serverSide = SERVER_TABS.includes(tab);
  const server = serverSide ? query[tab] : null;
  const level = serverSide ? server.level : filter.level;
  const result = leads[tab];
  const rows = asArray(result.data);
  const columns = useColumns(tab, seedSet);

  const filtered = useMemo(() => {
    const q = filter.q.trim().toLowerCase();
    return rows.filter((row) => {
      if (tab === 'patterns' && filter.type && row.type !== filter.type) return false;
      if (tab === 'communities' && filter.level && levelFromScore(row.max_risk) !== filter.level) return false;
      if (tab === 'wallets' && filter.hideSeeds && (row.is_seed || seedSet.has(String(row.wallet_id)))) return false;
      return !q || includes(searchable(tab, row), q);
    });
  }, [rows, filter, tab, seedSet]);

  const patternTypes = useMemo(() => {
    const fromOverview = Object.keys(overview.patterns || {});
    const fromRows = tab === 'patterns' ? rows.map((r) => r.type).filter(Boolean) : [];
    return [...new Set([...fromOverview, ...fromRows])];
  }, [overview.patterns, rows, tab]);

  const stats = overview.stats || {};
  const totals = {
    wallets: stats.wallets,
    transactions: stats.transactions,
    actors: stats.actors,
    ips: stats.origin_ips,
    patterns: sumValues(overview.patterns),
    communities: stats.communities,
  };
  const tabs = TABS.map((item) => ({
    ...item,
    count: isNum(totals[item.id]) ? fmtCompact(totals[item.id]) : undefined,
  }));

  // How many leads of this kind exist for the current server-side filter (unknown once relays are mixed in).
  const distribution = overview[DISTRIBUTION[tab]] || {};
  let available = null;
  if (serverSide && !(tab === 'ips' && server.relays)) {
    available = server.level ? distribution[normLevel(server.level)] : totals[tab];
    if (!isNum(available)) available = null;
  }
  const capped = serverSide && result.status === 'success' && rows.length >= server.limit && (available === null || available > rows.length);
  const nextLimit = serverSide ? NEXT_LIMIT.find((value) => value > server.limit) : null;
  const selectedKey = selection ? `${selection.type}:${selection.id}` : null;

  return (
    <section className="panel leads-panel" aria-labelledby="leads-title">
      <div className="panel-head">
        <h2 id="leads-title">Leads</h2>
        <span className="muted small" title={tab === 'actors' ? ACTOR_RULE : undefined}>{HELP[tab]}</span>
      </div>
      <Tabs tabs={tabs} active={tab} onChange={onTabChange} label="Lead type" />
      <div className="filter-row">
        {tab === 'patterns' ? (
          <label>
            <span className="sr-only">Pattern type</span>
            <select value={filter.type} onChange={(event) => setFilter({ type: event.target.value })}>
              <option value="">All types</option>
              {patternTypes.map((type) => (
                <option key={type} value={type}>{patternLabel(type)}{isNum(overview.patterns?.[type]) ? ` (${overview.patterns[type]})` : ''}</option>
              ))}
            </select>
          </label>
        ) : (
          <label>
            <span className="sr-only">Risk level</span>
            <select
              value={level}
              onChange={(event) => (serverSide ? onQueryChange(tab, { level: event.target.value }) : setFilter({ level: event.target.value }))}
            >
              <option value="">{tab === 'communities' ? 'Any highest risk' : 'All levels'}</option>
              {RISK_LEVELS.map((name) => (
                <option key={name} value={name}>
                  {name}{serverSide && isNum(distribution[name]) ? ` (${fmtInt(distribution[name])})` : ''}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="search-field">
          <span className="sr-only">Filter the listed leads</span>
          <input
            type="search"
            placeholder={PLACEHOLDER[tab]}
            value={filter.q}
            onChange={(event) => setFilter({ q: event.target.value })}
          />
        </label>
        {tab === 'wallets' && seedSet.size > 0 && (
          <label className="check-field" title="Seeds are already known to you: list only the leads the analysis found">
            <input type="checkbox" checked={Boolean(filter.hideSeeds)} onChange={(event) => setFilter({ hideSeeds: event.target.checked })} />
            Hide seeds
          </label>
        )}
        {tab === 'ips' && (
          <label className="check-field" title="Also list IPs that only passed on transactions first seen elsewhere">
            <input type="checkbox" checked={Boolean(server.relays)} onChange={(event) => onQueryChange('ips', { relays: event.target.checked })} />
            Include relays
          </label>
        )}
        {result.refreshing && <Spinner label="Updating the list" />}
        {result.status === 'success' && (
          <span className="muted small filter-count" title="Rows matching the text filter / rows loaded">
            {fmtInt(filtered.length)}{filtered.length !== rows.length ? ` / ${fmtInt(rows.length)}` : ''}
          </span>
        )}
      </div>
      <div className="leads-body">
        <ApiState
          result={result}
          loadingText={`Loading ${NOUN[tab]}…`}
          operation={`Loading ${NOUN[tab]}`}
          empty={rows.length === 0}
          emptyText={emptyText(tab, level, server?.relays, Number(stats.ips) > 0)}
        >
          <DataTable
            key={tab}
            caption={`${NOUN[tab]} ranked by risk`}
            columns={columns}
            rows={filtered}
            rowKey={rowKeys[tab]}
            selectedKey={selectedKey}
            onRowClick={(row) => onSelect(toSelection[tab](row))}
            emptyText="No listed lead matches the filter. The search box in the top bar looks through the whole dataset."
          />
        </ApiState>
      </div>
      {result.status === 'success' && rows.length > 0 && (
        <div className="leads-foot">
          {(tab === 'wallets' || tab === 'transactions') && (
            <ul className="legend-row" aria-label="Signal colours">
              {SIGNALS.map((key) => <li key={key}><span className={`dot sig-${key}`} aria-hidden="true" />{SIGNAL_LABELS[key]}</li>)}
            </ul>
          )}
          {capped && (
            <span className="muted small load-more">
              Top {fmtInt(rows.length)}{available !== null ? ` of ${fmtInt(available)}` : ''} loaded.
              {nextLimit && (
                <button type="button" className="link-button" onClick={() => onQueryChange(tab, { limit: nextLimit })}>
                  Load {fmtInt(available !== null ? Math.min(nextLimit, available) : nextLimit)}
                </button>
              )}
            </span>
          )}
        </div>
      )}
    </section>
  );
}
