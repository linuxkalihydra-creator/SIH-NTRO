/**
 * API client for the Bitcoin Investigation Platform backend.
 *
 * One function per endpoint documented in docs/api.md. Every function resolves
 * to the response body (not the axios response) and accepts an optional
 * AbortSignal as its last argument so callers can cancel stale requests.
 *
 * The backend serves the same routes under `/api`, and serves this dashboard
 * from `/`, so the default base URL is the relative `/api`: one process, one
 * origin, nothing to configure. During `npm run dev` Vite proxies `/api` to the
 * backend (see vite.config.js). Set VITE_API_URL to talk to a backend elsewhere.
 */

import axios from 'axios';

export const API_BASE_URL = String(import.meta.env.VITE_API_URL || '/api').replace(/\/+$/, '');

const http = axios.create({ baseURL: API_BASE_URL, timeout: 30000 });

// Re-scoring walks the whole flow graph; uploads can be large captures.
const LONG_TIMEOUT_MS = 0; // axios treats 0 as "no timeout"
const UPLOAD_TIMEOUT_MS = 0;

const body = (promise) => promise.then((response) => response.data);
const enc = encodeURIComponent;

/** File extensions the backend ingests (each may also be gzip-compressed). */
export const DATASET_EXTENSIONS = ['.csv', '.tsv', '.json', '.jsonl', '.ndjson', '.xml'];
export const DATASET_ACCEPT = [...DATASET_EXTENSIONS, ...DATASET_EXTENSIONS.map((ext) => `${ext}.gz`), '.gz'].join(',');

/** True when the request was cancelled through an AbortSignal. */
export const isCancelled = (error) => axios.isCancel(error) || error?.code === 'ERR_CANCELED';

/** True when the backend reports that the dataset has not been analysed yet. */
export const isNotAnalysed = (error) => error?.response?.status === 409;

/** True when the backend does not know the dataset / lead at all. */
export const isNotFound = (error) => error?.response?.status === 404;

/** Human-readable message that prefers the backend's `detail`. */
export function apiErrorMessage(error, operation = 'Request') {
  if (!error) return '';
  if (error.code === 'ECONNABORTED') return `${operation} timed out.`;
  if (!error.response) return 'Backend unreachable. Check that the API is running.';
  const { status, data } = error.response;
  const detail = data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((item) => item?.msg || JSON.stringify(item)).join('; ');
  }
  if (status === 404) return 'Not found.';
  if (status === 409) return 'This dataset has not been analysed yet.';
  if (status === 413) return 'File is too large to upload.';
  if (status >= 500) return `${operation} failed on the server (HTTP ${status}).`;
  return `${operation} failed (HTTP ${status}).`;
}

// ── Datasets ──────────────────────────────────────────────────────────────
export const getHealth = (signal) => body(http.get('/health', { signal }));

export const listDatasets = (signal) => body(http.get('/datasets', { signal }));

export function uploadDataset(file, signal) {
  const form = new FormData();
  form.append('file', file);
  return body(http.post('/datasets/upload', form, { signal, timeout: UPLOAD_TIMEOUT_MS }));
}

export const deleteDataset = (datasetId, signal) => body(http.delete(`/datasets/${enc(datasetId)}`, { signal }));

/** Starts the analysis in the background; poll `getProgress` until it stops running. */
export const startAnalysis = (datasetId, seedWallets, signal) =>
  body(http.post(
    '/analyze',
    { dataset_id: datasetId, wait: false, ...(Array.isArray(seedWallets) ? { seed_wallets: seedWallets } : {}) },
    { signal },
  ));

/** `{status: idle|running|completed|failed, stage, fraction, elapsed_seconds, error}` */
export const getProgress = (datasetId, signal) => body(http.get(`/datasets/${enc(datasetId)}/progress`, { signal }));

export const updateSeeds = (datasetId, seedWallets, signal) =>
  body(http.put(`/datasets/${enc(datasetId)}/seeds`, { seed_wallets: seedWallets }, { signal, timeout: LONG_TIMEOUT_MS }));

// ── Overview ──────────────────────────────────────────────────────────────
export const getOverview = (datasetId, signal) =>
  body(http.get('/overview', { params: { dataset_id: datasetId }, signal }));

/** Data-quality report of the last analysis (also embedded in the overview as `ingestion`). */
export const getIngestion = (datasetId, signal) =>
  body(http.get('/ingestion', { params: { dataset_id: datasetId }, signal }));

// ── Leads ─────────────────────────────────────────────────────────────────
export const getWalletAlerts = (datasetId, { limit, level } = {}, signal) =>
  body(http.get('/alerts', { params: { dataset_id: datasetId, limit, level }, signal }));

export const getTransactionAlerts = (datasetId, { limit, level } = {}, signal) =>
  body(http.get('/alerts/transactions', { params: { dataset_id: datasetId, limit, level }, signal }));

export const getActorAlerts = (datasetId, { limit, level } = {}, signal) =>
  body(http.get('/alerts/actors', { params: { dataset_id: datasetId, limit, level }, signal }));

export const getIpAlerts = (datasetId, { limit, level, includeRelays } = {}, signal) =>
  body(http.get('/alerts/ips', { params: { dataset_id: datasetId, limit, level, include_relays: includeRelays || undefined }, signal }));

export const getPatterns = (datasetId, { type } = {}, signal) =>
  body(http.get('/patterns', { params: { dataset_id: datasetId, type }, signal }));

export const getClusters = (datasetId, signal) =>
  body(http.get('/clusters', { params: { dataset_id: datasetId }, signal }));

/** `[{type: wallet|transaction|ip|actor|pattern, id, label, risk_score, risk_level}]` */
export const searchLeads = (datasetId, query, { limit = 12 } = {}, signal) =>
  body(http.get('/search', { params: { dataset_id: datasetId, q: query, limit }, signal }));

// ── Detail ────────────────────────────────────────────────────────────────
export const getWallet = (datasetId, walletId, signal) =>
  body(http.get(`/wallets/${enc(walletId)}`, { params: { dataset_id: datasetId }, signal }));

export const getTransaction = (datasetId, txid, signal) =>
  body(http.get(`/transactions/${enc(txid)}`, { params: { dataset_id: datasetId }, signal }));

export const getActor = (datasetId, actorId, signal) =>
  body(http.get(`/actors/${enc(actorId)}`, { params: { dataset_id: datasetId }, signal }));

export const getIp = (datasetId, ip, signal) =>
  body(http.get(`/ips/${enc(ip)}`, { params: { dataset_id: datasetId }, signal }));

// ── Graph ─────────────────────────────────────────────────────────────────
/** focusType: wallet|transaction|pattern|cluster|actor|ip · mode (wallets): neighbourhood|downstream|upstream */
export const getGraph = (datasetId, { focusType, focusId, depth, mode } = {}, signal) =>
  body(http.get('/graph', {
    params: { dataset_id: datasetId, focus_type: focusType, focus_id: focusId, depth, mode },
    signal,
  }));

// ── Downloads (plain links: the browser saves the response) ───────────────
export const EXPORT_KINDS = ['wallets', 'transactions', 'actors', 'ips', 'patterns'];

/** Ranked leads of one kind as a spreadsheet. */
export const exportUrl = (datasetId, kind, limit = 5000) =>
  `${API_BASE_URL}/export/${enc(kind)}.csv?dataset_id=${enc(datasetId)}&limit=${limit}`;

/** Markdown evidence sheet for one wallet | transaction | actor | ip lead. */
export const reportUrl = (datasetId, kind, id) =>
  `${API_BASE_URL}/report/${enc(kind)}/${enc(id)}?dataset_id=${enc(datasetId)}`;
