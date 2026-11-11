/** Formatting and small data helpers shared across the workspace. */

export const RISK_LEVELS = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'];
export const SIGNALS = ['anomaly', 'pattern', 'taint', 'network'];

export const SIGNAL_LABELS = {
  anomaly: 'Anomaly',
  pattern: 'Pattern',
  taint: 'Taint',
  network: 'Network',
};

export const PATTERN_LABELS = {
  peeling_chain: 'Peeling chain',
  coinjoin: 'CoinJoin',
  layering: 'Layering',
  rapid_passthrough: 'Rapid pass-through',
};

export const patternLabel = (type) =>
  PATTERN_LABELS[type] || String(type || 'Unknown').replace(/_/g, ' ');

/** What a wallet does inside a pattern, in words. */
export const ROLE_LABELS = {
  chain_hop: 'chain hop',
  chain_exit: 'holds the remainder',
  peel_recipient: 'received a peel',
  layering_source: 'source',
  layering_intermediate: 'intermediate',
  layering_sink: 'sink',
  layering_change: 'kept a leftover',
  layering_exit: 'received the reassembled funds',
  pass_through: 'pass-through wallet',
  pass_through_change: 'kept the dust',
  pass_through_recipient: 'received the forward',
  coinjoin_input: 'CoinJoin input',
  coinjoin_output: 'CoinJoin output',
  coinjoin_change: 'CoinJoin change',
};

export const roleLabel = (role) => ROLE_LABELS[role] || String(role || '').replace(/_/g, ' ');

/** How entities become one actor (the backend rule, said once so every view words it the same way). */
export const ACTOR_RULE = 'Entities are joined into one actor when two of three clues agree: the same broadcast IP, a direct payment between them, deposits into the same service address.';
export const ACTOR_RULE_SHORT = 'two of three clues agree: same broadcast IP, a direct payment, deposits into the same service address';

/** Lead kinds the investigation panel can open, and how they are named in the UI. */
export const LEAD_TYPES = ['wallet', 'transaction', 'actor', 'ip', 'pattern', 'community'];
export const LEAD_LABELS = { wallet: 'Wallet', transaction: 'Transaction', actor: 'Actor', ip: 'IP', pattern: 'Pattern', community: 'Community' };

/** Level from a 0-100 score, using the thresholds in docs/api.md. */
export function levelFromScore(score) {
  const value = Number(score);
  if (!Number.isFinite(value)) return null;
  if (value >= 80) return 'CRITICAL';
  if (value >= 60) return 'HIGH';
  if (value >= 35) return 'MEDIUM';
  return 'LOW';
}

export const normLevel = (level) => {
  const upper = String(level || '').toUpperCase();
  return RISK_LEVELS.includes(upper) ? upper : null;
};

/** Sort key for a risk level: CRITICAL highest. */
export const levelRank = (level) => ({ CRITICAL: 3, HIGH: 2, MEDIUM: 1, LOW: 0 }[normLevel(level)] ?? -1);

export const isNum = (value) => typeof value === 'number' && Number.isFinite(value);

export function fmtInt(value) {
  return isNum(value) ? Math.round(value).toLocaleString('en-US') : '—';
}

export function fmtNum(value, digits = 2) {
  if (!isNum(value)) return '—';
  return value.toLocaleString('en-US', { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

/** 1284 → "1,284" · 12940 → "12.9K" · 4200000 → "4.2M" */
export function fmtCompact(value) {
  if (!isNum(value)) return '—';
  const abs = Math.abs(value);
  if (abs < 10000) return Math.round(value).toLocaleString('en-US');
  if (abs < 1e6) return `${(value / 1e3).toFixed(abs < 1e5 ? 1 : 0).replace(/\.0$/, '')}K`;
  if (abs < 1e9) return `${(value / 1e6).toFixed(abs < 1e7 ? 2 : 1).replace(/\.0+$/, '')}M`;
  return `${(value / 1e9).toFixed(2)}B`;
}

export function fmtScore(value) {
  return isNum(value) ? value.toFixed(1) : '—';
}

/** 0-1 → "93%" */
export function fmtPct(value, digits = 0) {
  if (!isNum(value)) return '—';
  return `${(value * 100).toFixed(digits)}%`;
}

export function fmtBtc(value) {
  if (!isNum(value)) return '—';
  const digits = Math.abs(value) >= 100 ? 2 : Math.abs(value) >= 1 ? 4 : 8;
  return `${Number(value.toFixed(digits)).toLocaleString('en-US', { maximumFractionDigits: digits })} BTC`;
}

/** Short amount for graph labels and tight cells: three significant digits, no unit. */
export function fmtBtcShort(value) {
  if (!isNum(value)) return '';
  if (value === 0) return '0';
  const abs = Math.abs(value);
  if (abs >= 1000) return fmtCompact(value);
  if (abs < 0.0001) return value.toExponential(1);
  return String(Number(value.toPrecision(3)));
}

/** Percentile (0-1) → "top 0.3%" */
export function fmtTopPct(percentile) {
  if (!isNum(percentile)) return '—';
  const top = Math.max(0, (1 - percentile) * 100);
  if (top < 0.1) return 'top <0.1%';
  return `top ${top < 10 ? top.toFixed(1) : Math.round(top)}%`;
}

export function fmtTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toISOString().replace('T', ' ').slice(0, 19);
}

export const fmtDate = (value) => (value ? fmtTime(value).slice(0, 10) : '—');

/** Seconds → "0.42 s" · "12.3 s" · "4 min 5 s" */
export function fmtSeconds(value) {
  if (!isNum(value)) return '—';
  if (value < 10) return `${value.toFixed(2)} s`;
  if (value < 90) return `${value.toFixed(1)} s`;
  return `${Math.floor(value / 60)} min ${Math.round(value % 60)} s`;
}

export function fmtBytes(value) {
  if (!isNum(value)) return '';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export function truncateId(id, head = 8, tail = 6) {
  const text = String(id ?? '');
  if (text.length <= head + tail + 1) return text;
  return `${text.slice(0, head)}…${text.slice(-tail)}`;
}

/** "AS15169 Google LLC" (either part may be missing). */
export function fmtAsn(asn, org) {
  const number = Number(asn);
  const name = String(org || '').trim();
  if (!number && !name) return '—';
  return [number ? `AS${number}` : null, name || null].filter(Boolean).join(' ');
}

/**
 * Ports a Bitcoin node normally listens on. 8333 unless the backend was configured otherwise
 * (BITCOIN_STANDARD_PORTS); the overview reports the configured set and App passes it in.
 */
let standardPorts = new Set([8333]);
export function setStandardPorts(ports) {
  const list = (Array.isArray(ports) ? ports : []).map(Number).filter((port) => Number.isInteger(port) && port > 0);
  if (list.length) standardPorts = new Set(list);
}
export const isStandardPort = (port) => standardPorts.has(Number(port));
/** "8333" or "8333 or 18333", for explanatory text. */
export const standardPortsText = () => [...standardPorts].sort((a, b) => a - b).join(' or ');

let regionNames = null;
try {
  regionNames = new Intl.DisplayNames(['en'], { type: 'region' });
} catch {
  regionNames = null; // very old browsers: the ISO code is shown instead
}

/** "DE" → "Germany" (falls back to the code when it is not an ISO region). */
export function countryName(code) {
  const text = String(code || '').trim();
  if (!text) return 'Unknown';
  if (!regionNames || !/^[A-Za-z]{2}$/.test(text)) return text;
  try {
    const name = regionNames.of(text.toUpperCase());
    return name && name !== text.toUpperCase() ? name : text.toUpperCase();
  } catch {
    return text;
  }
}

export const asArray = (value) => (Array.isArray(value) ? value : []);

/** Split pasted text / file contents into unique wallet ids. */
export function parseIds(text) {
  const ids = String(text || '')
    .split(/[\s,;]+/)
    .map((part) => part.trim().replace(/^["']|["']$/g, ''))
    .filter(Boolean);
  return [...new Set(ids)];
}

export function sumValues(obj) {
  return Object.values(obj || {}).reduce((acc, value) => acc + (isNum(value) ? value : 0), 0);
}

/** "3 wallets" / "1 wallet" / "2 entities" (pass the plural when it is not just an added s). */
export const plural = (count, noun, many = `${noun}s`) => `${fmtInt(count)} ${count === 1 ? noun : many}`;
