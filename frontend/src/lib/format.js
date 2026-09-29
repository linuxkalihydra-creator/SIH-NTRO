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

export const isNum = (value) => typeof value === 'number' && Number.isFinite(value);

export function fmtInt(value) {
  return isNum(value) ? Math.round(value).toLocaleString() : '—';
}

export function fmtNum(value, digits = 2) {
  if (!isNum(value)) return '—';
  return value.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
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
  return `${Number(value.toFixed(digits)).toLocaleString(undefined, { maximumFractionDigits: digits })} BTC`;
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
