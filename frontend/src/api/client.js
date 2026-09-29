/**
 * API client for the Bitcoin Investigation Platform backend.
 *
 * One function per endpoint documented in docs/api.md. Every function resolves
 * to the response body (not the axios response) and accepts an optional
 * AbortSignal as its last argument so callers can cancel stale requests.
 */

import axios from 'axios';

export const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

const http = axios.create({ baseURL: API_BASE_URL, timeout: 30000 });

// Analysis is synchronous on the backend and can take a minute or more on
// large datasets; axios treats 0 as "no timeout".
const ANALYSIS_TIMEOUT_MS = 0;
const UPLOAD_TIMEOUT_MS = 300000;

const body = (promise) => promise.then((response) => response.data);
const enc = encodeURIComponent;

/** True when the request was cancelled through an AbortSignal. */
export const isCancelled = (error) => axios.isCancel(error) || error?.code === 'ERR_CANCELED';

/** True when the backend reports that the dataset has not been analysed yet. */
export const isNotAnalysed = (error) => error?.response?.status === 409;

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

export const analyzeDataset = (datasetId, seedWallets, signal) =>
  body(http.post(
    '/analyze',
    { dataset_id: datasetId, ...(Array.isArray(seedWallets) ? { seed_wallets: seedWallets } : {}) },
    { signal, timeout: ANALYSIS_TIMEOUT_MS },
  ));

export const updateSeeds = (datasetId, seedWallets, signal) =>
  body(http.put(`/datasets/${enc(datasetId)}/seeds`, { seed_wallets: seedWallets }, { signal, timeout: ANALYSIS_TIMEOUT_MS }));

// ── Overview ──────────────────────────────────────────────────────────────
export const getOverview = (datasetId, signal) =>
  body(http.get('/overview', { params: { dataset_id: datasetId }, signal }));

// ── Leads ─────────────────────────────────────────────────────────────────
export const getWalletAlerts = (datasetId, { limit, level } = {}, signal) =>
  body(http.get('/alerts', { params: { dataset_id: datasetId, limit, level }, signal }));

export const getTransactionAlerts = (datasetId, { limit, level } = {}, signal) =>
  body(http.get('/alerts/transactions', { params: { dataset_id: datasetId, limit, level }, signal }));

export const getPatterns = (datasetId, { type } = {}, signal) =>
  body(http.get('/patterns', { params: { dataset_id: datasetId, type }, signal }));

export const getClusters = (datasetId, signal) =>
  body(http.get('/clusters', { params: { dataset_id: datasetId }, signal }));

// ── Detail ────────────────────────────────────────────────────────────────
export const getWallet = (datasetId, walletId, signal) =>
  body(http.get(`/wallets/${enc(walletId)}`, { params: { dataset_id: datasetId }, signal }));

export const getTransaction = (datasetId, txid, signal) =>
  body(http.get(`/transactions/${enc(txid)}`, { params: { dataset_id: datasetId }, signal }));

// ── Graph ─────────────────────────────────────────────────────────────────
export const getGraph = (datasetId, { focusType, focusId, depth } = {}, signal) =>
  body(http.get('/graph', {
    params: { dataset_id: datasetId, focus_type: focusType, focus_id: focusId, depth },
    signal,
  }));
