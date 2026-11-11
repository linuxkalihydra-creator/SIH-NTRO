/**
 * The workspace state that is worth sharing lives in the URL hash, so a link
 * restores the same dataset, lead tab, selected lead and open sections:
 *
 *   #dataset=dataset_ab12&tab=ips&type=wallet&id=bc1q…&graph=downstream&depth=2&evidence=network&open=activity,quality
 *
 * Nothing here is sent to the server (a hash never leaves the browser).
 */

import { LEAD_TYPES } from './format.js';

export const LEAD_TABS = ['wallets', 'transactions', 'actors', 'ips', 'patterns', 'communities'];
export const GRAPH_MODES = ['neighbourhood', 'downstream', 'upstream'];
export const SECTIONS = ['activity', 'quality', 'models'];
/** Sections that are open when neither the link nor a saved preference says otherwise. */
export const DEFAULT_SECTIONS = ['activity'];

const sameList = (a, b) => a.length === b.length && a.every((name) => b.includes(name));

const oneOf = (value, allowed) => (allowed.includes(value) ? value : null);

export function readHash() {
  let params;
  try {
    params = new URLSearchParams(window.location.hash.replace(/^#/, ''));
  } catch {
    params = new URLSearchParams();
  }
  const type = oneOf(params.get('type'), LEAD_TYPES);
  const id = params.get('id');
  const depth = Number(params.get('depth'));
  return {
    dataset: params.get('dataset') || null,
    tab: oneOf(params.get('tab'), LEAD_TABS),
    selection: type && id ? { type, id } : null,
    graph: oneOf(params.get('graph'), GRAPH_MODES),
    depth: depth >= 1 && depth <= 3 ? depth : null,
    evidence: params.get('evidence') || null,
    // null = the link does not say; [] = the link says "all folded" (open=none)
    open: params.has('open') ? params.get('open').split(',').filter((name) => SECTIONS.includes(name)) : null,
  };
}

export function buildHash({ dataset, tab, selection, graph, depth, evidence, open }) {
  const params = new URLSearchParams();
  if (dataset) params.set('dataset', dataset);
  if (tab && tab !== 'wallets') params.set('tab', tab);
  if (selection?.type && selection?.id !== undefined && selection?.id !== null) {
    params.set('type', selection.type);
    params.set('id', String(selection.id));
  }
  if (graph && graph !== 'neighbourhood') params.set('graph', graph);
  if (depth && depth !== 1) params.set('depth', String(depth));
  if (evidence) params.set('evidence', evidence);
  if (Array.isArray(open) && !sameList(open, DEFAULT_SECTIONS)) {
    params.set('open', open.length ? SECTIONS.filter((name) => open.includes(name)).join(',') : 'none');
  }
  const text = params.toString();
  return text ? `#${text}` : '';
}

/** Replace the hash without adding a history entry (the workspace keeps its own Back list). */
export function writeHash(state) {
  const next = buildHash(state);
  if (next === window.location.hash || (!next && !window.location.hash)) return;
  try {
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}${next}`);
  } catch {
    // Some embedded contexts forbid history changes; deep links are then simply unavailable.
  }
}
