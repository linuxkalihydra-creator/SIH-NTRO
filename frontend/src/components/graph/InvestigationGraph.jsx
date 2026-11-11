/**
 * InvestigationGraph — Cytoscape link-analysis graph for the /graph endpoint.
 *
 * Nodes: Wallet | Transaction | IP. Colour and size both carry the risk level, a thick
 * border marks a seed, a ring a service-like wallet, a dashed ring a wallet where a
 * trace was stopped, a dashed outline an IP that behaves as a relay node.
 * Edges: Wallet -INPUT_FROM-> Transaction -OUTPUT_TO-> Wallet (arrows follow the value),
 * Transaction -OBSERVED_IN- IP (first sighting, dashed) and Transaction -RELAYED_BY- IP
 * (later relay, dotted and lighter).
 *
 * Two layouts: "flow" lays the money out step by step in layers (traces, transactions,
 * laundering structures); "force" is a force-directed layout for neighbourhoods.
 * Clicking any node calls onSelect({type, id}). Uses Cytoscape directly (no wrapper)
 * so initialisation timing is under our control.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import cytoscape from 'cytoscape';
import { fmtAsn, fmtBtc, fmtBtcShort, fmtPct, fmtScore, fmtTime, isNum, normLevel } from '../../lib/format.js';
import './InvestigationGraph.css';

const LEVELS = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'];
const NODE_TYPES = ['Wallet', 'Transaction', 'IP'];
const SELECT_TYPE = { Wallet: 'wallet', Transaction: 'transaction', IP: 'ip' };
const FLOW_EDGES = new Set(['INPUT_FROM', 'OUTPUT_TO']);
const SIZE = { CRITICAL: 32, HIGH: 28, MEDIUM: 24, LOW: 20 };
const LAYER_GAP = 86;      // between successive steps of a flow
const LAYER_GAP_WIDE = 150; // the same for a short flow, which is drawn zoomed in
const ROW_GAP = 46;        // between nodes of one step
const ROW_BLOCK_GAP = 64;  // between the rows of a wrapped flow
const IP_BAND = 70;        // from a row of the flow down to its IPs
const IP_LANE = 44;
const IP_GAP = 92;
const FOLD_AT = 9;         // a step with more nodes than this is folded into sub-columns
const SUB_GAP = 76;        // between those sub-columns

// Colours come from CSS custom properties so the canvas follows light/dark mode.
function readPalette(element) {
  const css = getComputedStyle(element);
  const v = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  return {
    CRITICAL: v('--risk-critical', '#d03b3b'),
    HIGH: v('--risk-high', '#ec835a'),
    MEDIUM: v('--risk-medium', '#fab219'),
    LOW: v('--risk-low', '#8a94a3'),
    none: v('--graph-node', '#9aa3ae'),
    ip: v('--graph-ip', '#6b7785'),
    text: v('--text', '#1d2330'),
    muted: v('--text-muted', '#5d6677'),
    textBg: v('--surface', '#ffffff'),
    edge: v('--graph-edge', '#9aa3ae'),
    seed: v('--seed-border', '#111111'),
    focus: v('--accent', '#2a78d6'),
    nodeBorder: v('--surface', '#ffffff'),
  };
}

function buildStylesheet(labelsVisible, p) {
  const levelStyles = LEVELS.map((level) => ({
    selector: `node[level = "${level}"]`,
    style: { 'background-color': p[level] },
  }));
  return [
    {
      selector: 'node',
      style: {
        label: labelsVisible ? 'data(label)' : '',
        color: p.text,
        'font-size': 9,
        'font-family': 'system-ui, -apple-system, "Segoe UI", sans-serif',
        'text-valign': 'bottom',
        'text-halign': 'center',
        'text-margin-y': 4,
        'text-background-color': p.textBg,
        'text-background-opacity': 0.8,
        'text-background-padding': '1px',
        'text-wrap': 'ellipsis',
        'text-max-width': 96,
        'min-zoomed-font-size': 7,
        'background-color': p.none,
        'border-width': 1.5,
        'border-color': p.nodeBorder,
        width: 'data(size)',
        height: 'data(size)',
      },
    },
    { selector: 'node[type = "Wallet"]', style: { shape: 'ellipse' } },
    { selector: 'node[type = "Transaction"]', style: { shape: 'round-rectangle', height: 'data(sizeShort)' } },
    { selector: 'node[type = "IP"]', style: { shape: 'diamond', 'background-color': p.ip } },
    ...levelStyles,
    // A LOW-risk IP stays in the neutral IP tone so it is not mistaken for a wallet.
    { selector: 'node[type = "IP"][level = "LOW"]', style: { 'background-color': p.ip } },
    {
      selector: 'node[?isRelay]',
      style: { 'background-color': p.none, 'border-style': 'dashed', 'border-width': 1.5, 'border-color': p.muted },
    },
    {
      selector: 'node[?service]',
      style: { 'outline-width': 2, 'outline-style': 'solid', 'outline-color': p.muted, 'outline-offset': 2 },
    },
    {
      selector: 'node[?traceStopped]',
      style: { 'outline-width': 2.5, 'outline-style': 'dashed', 'outline-color': p.text, 'outline-offset': 2 },
    },
    { selector: 'node[?isSeed]', style: { 'border-width': 4.5, 'border-style': 'solid', 'border-color': p.seed } },
    {
      selector: 'node[?isFocus]',
      style: {
        width: 40,
        height: 40,
        'font-size': 11,
        'font-weight': 'bold',
        // Above the node: the row of labels under its neighbours stays clear of the longer, bolder focus label.
        'text-valign': 'top',
        'text-margin-y': -10,
        'underlay-color': p.focus,
        'underlay-opacity': 0.3,
        'underlay-padding': 7,
        'underlay-shape': 'ellipse',
        'z-index': 20,
      },
    },
    { selector: 'node[?isFocus][type = "Transaction"]', style: { width: 36, height: 26 } },
    { selector: 'node[?isFocus][type = "IP"]', style: { width: 34, height: 34 } },
    {
      selector: 'edge',
      style: {
        width: 'data(w)',
        'line-color': p.edge,
        'target-arrow-color': p.edge,
        'target-arrow-shape': 'triangle',
        'arrow-scale': 0.8,
        'curve-style': 'bezier',
        opacity: 0.85,
        label: labelsVisible ? 'data(edgeLabel)' : '',
        'font-size': 8,
        color: p.text,
        'text-background-color': p.textBg,
        'text-background-opacity': 0.75,
        'text-background-padding': '1px',
        'text-rotation': 'autorotate',
        'min-zoomed-font-size': 8,
      },
    },
    {
      selector: 'edge[type = "OBSERVED_IN"]',
      style: { 'line-style': 'dashed', 'target-arrow-shape': 'none', width: 1.2, opacity: 0.7 },
    },
    {
      selector: 'edge[type = "RELAYED_BY"]',
      style: { 'line-style': 'dotted', 'target-arrow-shape': 'none', width: 1, opacity: 0.45 },
    },
    { selector: '.hl', style: { 'line-color': p.focus, 'target-arrow-color': p.focus, opacity: 1, 'z-index': 15 } },
    { selector: 'node.dim', style: { opacity: 0.25 } },
    { selector: 'edge.dim', style: { opacity: 0.12 } },
  ];
}

// Direction each edge type must have so arrows follow value flow.
const EXPECTED = {
  INPUT_FROM: ['Wallet', 'Transaction'],
  OUTPUT_TO: ['Transaction', 'Wallet'],
  OBSERVED_IN: ['Transaction', 'IP'],
  RELAYED_BY: ['Transaction', 'IP'],
};

function buildElements(data) {
  const rawNodes = Array.isArray(data?.nodes) ? data.nodes : [];
  const rawEdges = Array.isArray(data?.edges) ? data.edges : [];
  const types = new Map();
  const nodes = [];
  for (const node of rawNodes) {
    const id = node?.id !== null && node?.id !== undefined ? String(node.id) : '';
    if (!id || types.has(id)) continue;
    const type = NODE_TYPES.includes(node.type) ? node.type : 'Wallet';
    types.set(id, type);
    const label = String(node.label || id);
    const level = normLevel(node.risk_level) || '';
    const scale = type === 'Wallet' ? 1 : type === 'Transaction' ? 0.78 : 0.82;
    const size = Math.round((SIZE[level] || 22) * scale);
    nodes.push({
      group: 'nodes',
      data: {
        id,
        type,
        // A transaction id says little on the canvas (the tooltip and the tables carry it); only the focus keeps it.
        label: type === 'Transaction' && !node.is_focus ? '' : (label.length > 22 ? `${label.slice(0, 10)}…${label.slice(-6)}` : label),
        level,
        size,
        sizeShort: Math.round(size * 0.76),
        riskScore: isNum(node.risk_score) ? node.risk_score : null,
        isSeed: Boolean(node.is_seed),
        isFocus: Boolean(node.is_focus),
        service: Boolean(node.service),
        traceStopped: Boolean(node.trace_stopped),
        isRelay: Boolean(node.is_relay),
        country: node.country || '',
        asn: node.asn || 0,
        asOrg: node.as_org || '',
        timestamp: node.timestamp || '',
        entityId: node.entity_id || '',
        actorId: node.actor_id || '',
      },
    });
  }

  const maxAmount = rawEdges.reduce((m, e) => (isNum(e?.amount) && e.amount > m ? e.amount : m), 0);
  const edgeIds = new Set();
  const edges = [];
  rawEdges.forEach((edge, index) => {
    let source = edge?.source !== null && edge?.source !== undefined ? String(edge.source) : '';
    let target = edge?.target !== null && edge?.target !== undefined ? String(edge.target) : '';
    if (!types.has(source) || !types.has(target)) return;
    const expected = EXPECTED[edge.type];
    if (expected && types.get(source) === expected[1] && types.get(target) === expected[0]) {
      [source, target] = [target, source];
    }
    let id = edge.id !== null && edge.id !== undefined ? String(edge.id) : `${source}->${target}#${index}`;
    if (edgeIds.has(id) || types.has(id)) id = `${id}#${index}`;
    edgeIds.add(id);
    const amount = isNum(edge.amount) ? edge.amount : null;
    const delay = isNum(edge.delay) ? edge.delay : null;
    const w = amount !== null && maxAmount > 0 ? 1 + 3 * (Math.log1p(amount) / Math.log1p(maxAmount)) : 1.2;
    let edgeLabel = '';
    if (amount !== null) edgeLabel = fmtBtcShort(amount);
    else if (edge.type === 'RELAYED_BY' && delay !== null) edgeLabel = `+${delay < 10 ? delay.toFixed(1) : Math.round(delay)} s`;
    edges.push({
      group: 'edges',
      data: {
        id,
        source,
        target,
        type: edge.type || '',
        amount,
        delay,
        confidence: isNum(edge.confidence) ? edge.confidence : null,
        attributed: edge.attributed !== false,
        edgeLabel,
        w,
      },
    });
  });
  return { nodes, edges };
}

/** Deterministic starting positions for the force layout: BFS rings around the focus node(s). */
function ringPositions(nodes, edges) {
  const adjacency = new Map(nodes.map((n) => [n.data.id, []]));
  for (const e of edges) {
    adjacency.get(e.data.source)?.push(e.data.target);
    adjacency.get(e.data.target)?.push(e.data.source);
  }
  let roots = nodes.filter((n) => n.data.isFocus).map((n) => n.data.id);
  if (!roots.length && nodes.length) {
    const best = [...nodes].sort((a, b) => (b.data.riskScore ?? -1) - (a.data.riskScore ?? -1))[0];
    roots = [best.data.id];
  }
  const depth = new Map(roots.map((id) => [id, 0]));
  const queue = [...roots];
  while (queue.length) {
    const id = queue.shift();
    for (const next of adjacency.get(id) || []) {
      if (!depth.has(next)) {
        depth.set(next, depth.get(id) + 1);
        queue.push(next);
      }
    }
  }
  const maxDepth = Math.max(0, ...depth.values());
  const rings = new Map();
  for (const n of nodes) {
    const d = depth.has(n.data.id) ? depth.get(n.data.id) : maxDepth + 1;
    if (!rings.has(d)) rings.set(d, []);
    rings.get(d).push(n.data.id);
  }
  const positions = {};
  [...rings.keys()].sort((a, b) => a - b).forEach((d) => {
    const ids = rings.get(d).sort();
    const radius = d === 0 && ids.length === 1 ? 0 : Math.max(d * 110, (ids.length * 34) / (2 * Math.PI));
    ids.forEach((id, i) => {
      const angle = (2 * Math.PI * i) / ids.length + d * 0.35;
      positions[id] = { x: radius * Math.cos(angle), y: radius * Math.sin(angle) };
    });
  });
  return positions;
}

const mean = (values) => values.reduce((acc, value) => acc + value, 0) / values.length;

/** Keep the order and at least `gap` between neighbours while staying as close as possible to `wanted`. */
function spread(wanted, gap) {
  const placed = [];
  wanted.forEach((value, index) => {
    placed[index] = index === 0 ? value : Math.max(value, placed[index - 1] + gap);
  });
  const shift = mean(placed.map((value, index) => value - wanted[index]));
  return placed.map((value) => value - shift);
}

/**
 * Layered positions for value flows: every value edge points along the reading direction,
 * so the picture reads as "where the money went". The largest flow runs along the top of
 * each step and the smaller ones hang below it. A chain too long for the canvas continues
 * on the next row in the opposite direction (the turn stays a short edge), and the IPs sit
 * in a band under the transactions they announced.
 */
function flowPositions(nodes, edges, viewport) {
  const info = new Map(nodes.map((n) => [n.data.id, n.data]));
  const out = new Map(nodes.map((n) => [n.data.id, []]));
  const inc = new Map(nodes.map((n) => [n.data.id, []]));
  const amounts = new Map();
  const ipLinks = new Map();
  for (const e of edges) {
    const { source, target, type } = e.data;
    if (FLOW_EDGES.has(type)) {
      out.get(source).push(target);
      inc.get(target).push(source);
      amounts.set(`${source}|${target}`, e.data.amount || 0);
    } else if (info.get(target)?.type === 'IP') {
      if (!ipLinks.has(target)) ipLinks.set(target, []);
      ipLinks.get(target).push(source);
    }
  }

  // Connected components of the value graph, the one holding the focus first.
  const rank = (data) => [data.isFocus ? 0 : 1, -(data.riskScore ?? -1), data.id];
  const compare = (a, b) => {
    const [fa, ra, ia] = rank(info.get(a));
    const [fb, rb, ib] = rank(info.get(b));
    return fa - fb || ra - rb || ia.localeCompare(ib);
  };
  const valueNodes = nodes.map((n) => n.data.id).filter((id) => info.get(id).type !== 'IP').sort(compare);
  const componentOf = new Map();
  const components = [];
  for (const root of valueNodes) {
    if (componentOf.has(root)) continue;
    const members = [];
    componentOf.set(root, components.length);
    const queue = [root];
    while (queue.length) {
      const id = queue.shift();
      members.push(id);
      for (const next of [...out.get(id), ...inc.get(id)]) {
        if (!componentOf.has(next)) {
          componentOf.set(next, components.length);
          queue.push(next);
        }
      }
    }
    components.push(members);
  }

  const positions = {};
  let offsetY = 0;
  components.forEach((members, componentIndex) => {
    // 1. Layers: longest path (Kahn); a cycle is broken at the node with the fewest unmet inputs.
    const unmet = new Map(members.map((id) => [id, inc.get(id).length]));
    const layer = new Map();
    const done = new Set();
    const order = [];
    let ready = members.filter((id) => unmet.get(id) === 0);
    while (order.length < members.length) {
      if (!ready.length) {
        let best = null;
        for (const id of members) {
          if (!done.has(id) && (best === null || unmet.get(id) < unmet.get(best))) best = id;
        }
        ready = [best];
      }
      const id = ready.shift();
      if (done.has(id)) continue;
      const placed = inc.get(id).filter((prev) => done.has(prev));
      layer.set(id, placed.length ? Math.max(...placed.map((prev) => layer.get(prev))) + 1 : 0);
      done.add(id);
      order.push(id);
      for (const next of out.get(id)) {
        if (done.has(next)) continue;
        unmet.set(next, unmet.get(next) - 1);
        if (unmet.get(next) <= 0) ready.push(next);
      }
    }
    // A wallet that only feeds a transaction sits right before it, not at the far start.
    for (const id of [...order].reverse()) {
      const before = inc.get(id).filter((prev) => layer.get(prev) < layer.get(id));
      if (before.length) continue;
      const after = out.get(id).map((next) => layer.get(next)).filter((value) => value > layer.get(id));
      if (after.length) layer.set(id, Math.min(...after) - 1);
    }
    const first = Math.min(...members.map((id) => layer.get(id)));
    const layers = [];
    for (const id of members) {
      const index = layer.get(id) - first;
      layer.set(id, index);
      if (!layers[index]) layers[index] = [];
      layers[index].push(id);
    }
    for (let index = 0; index < layers.length; index += 1) layers[index] = (layers[index] || []).sort(compare);

    // 2. Position inside each layer. Sweep away from the focus (forward for "where it went",
    //    backward for "where it came from"): a node sits level with what it is attached to,
    //    the largest flow first, the others stacked below.
    const focus = members.find((id) => info.get(id).isFocus);
    const forward = !focus || layer.get(focus) <= (layers.length - 1) / 2;
    const across = new Map();
    const folds = [];          // per layer: {columns, side} when it was folded
    const column = new Map();  // node -> its sub-column inside a folded layer
    const sequence = layers.map((_, index) => (forward ? index : layers.length - 1 - index));
    for (const index of sequence) {
      const items = layers[index].map((id, position) => {
        const anchors = (forward ? inc.get(id) : out.get(id)).filter((other) => across.has(other));
        const flow = anchors.reduce((most, other) => Math.max(most, amounts.get(forward ? `${other}|${id}` : `${id}|${other}`) || 0), 0);
        return { id, position, flow, wanted: anchors.length ? mean(anchors.map((other) => across.get(other))) : null };
      });
      const anchored = items.filter((item) => item.wanted !== null).sort((a, b) => a.wanted - b.wanted || b.flow - a.flow || a.position - b.position);
      const loose = items.filter((item) => item.wanted === null);
      let cursor = null;
      const sorted = [...anchored, ...loose];
      for (const item of sorted) {
        let value = 0;
        if (item.wanted === null) value = cursor === null ? 0 : cursor + ROW_GAP;
        else value = cursor === null ? item.wanted : Math.max(item.wanted, cursor + ROW_GAP);
        across.set(item.id, value);
        cursor = value;
      }
      // A big fan (the many inputs of a CoinJoin, a batch payout): zig-zag it over a few sub-columns
      // so it takes a third of the height. The sub-columns grow away from the side it is attached to.
      if (sorted.length > FOLD_AT) {
        const columns = Math.min(3, Math.ceil(sorted.length / FOLD_AT));
        const start = across.get(sorted[0].id);
        sorted.forEach((item, position) => {
          across.set(item.id, start + (position * ROW_GAP) / columns);
          column.set(item.id, position % columns);
        });
        const feeding = sorted.filter((item) => out.get(item.id).some((other) => layer.get(other) > index)).length;
        folds[index] = { columns, side: feeding * 2 >= sorted.length ? -1 : 1 };
      }
    }

    // 3. Rows: wrap a long chain when that lets the whole picture be drawn clearly larger.
    const linkedIps = [...ipLinks.entries()]
      .filter(([ip, sources]) => !positions[ip] && componentOf.get(sources[0]) === componentIndex)
      .map(([ip, sources]) => ({ ip, sources: sources.filter((source) => componentOf.get(source) === componentIndex) }));
    const band = linkedIps.length ? IP_BAND + 2 * IP_LANE : 0;
    const extent = (from, to) => {
      let top = Infinity;
      let bottom = -Infinity;
      for (let index = from; index < to; index += 1) {
        for (const id of layers[index]) {
          top = Math.min(top, across.get(id));
          bottom = Math.max(bottom, across.get(id));
        }
      }
      return [top, bottom];
    };
    let rows = 1;
    let bestZoom = 0;
    for (let candidate = 1; candidate <= 6; candidate += 1) {
      const span = Math.ceil(layers.length / candidate);
      if (candidate > 1 && span < 6) break;
      let height = 0;
      for (let from = 0; from < layers.length; from += span) {
        const [top, bottom] = extent(from, Math.min(layers.length, from + span));
        height += bottom - top + band + ROW_BLOCK_GAP;
      }
      const width = (span - 1) * LAYER_GAP + 150;
      const zoom = Math.min(viewport.width / width, viewport.height / height, 1.6);
      if (zoom > bestZoom * 1.2) {
        bestZoom = zoom;
        rows = candidate;
      }
    }
    const per = Math.ceil(layers.length / rows);
    // A short flow is zoomed in, so give its steps more room; a long one keeps the compact step.
    const stepWidth = Math.max(LAYER_GAP, Math.min(LAYER_GAP_WIDE, (viewport.width / 1.3 - 150) / Math.max(1, per - 1)));
    // x of every layer within its row; a folded layer pushes its neighbour away by the width of its sub-columns.
    const base = [];
    const rowWidth = [];
    for (let from = 0; from < layers.length; from += per) {
      let cursor = 0;
      for (let index = from; index < Math.min(layers.length, from + per); index += 1) {
        if (index > from) {
          const before = folds[index - 1];
          const here = folds[index];
          cursor += stepWidth
            + (before && before.side === 1 ? (before.columns - 1) * SUB_GAP : 0)
            + (here && here.side === -1 ? (here.columns - 1) * SUB_GAP : 0);
        }
        base[index] = cursor;
      }
      rowWidth.push(cursor);
    }
    const widest = Math.max(...rowWidth);
    const xOf = (id) => {
      const index = layer.get(id);
      const fold = folds[index];
      const x = base[index] + (fold ? fold.side * (column.get(id) || 0) * SUB_GAP : 0);
      return Math.floor(index / per) % 2 === 0 ? x : widest - x;   // odd rows run the other way
    };

    let blockTop = offsetY;
    for (let row = 0; row * per < layers.length; row += 1) {
      const from = row * per;
      const to = Math.min(layers.length, from + per);
      const [top, bottom] = extent(from, to);
      for (let index = from; index < to; index += 1) {
        for (const id of layers[index]) positions[id] = { x: xOf(id), y: across.get(id) - top + blockTop };
      }
      const rowBottom = bottom - top + blockTop;
      // IPs that first announced a transaction of this row: under it, side by side, in as few lanes as fit.
      const here = linkedIps
        .map((item) => ({ ...item, firstLayer: Math.min(...item.sources.map((source) => layer.get(source))) }))
        .filter((item) => item.firstLayer >= from && item.firstLayer < to)
        .map((item) => {
          const inRow = item.sources.filter((source) => layer.get(source) >= from && layer.get(source) < to);
          return { ip: item.ip, x: mean(inRow.map((source) => positions[source].x)) };
        })
        .sort((a, b) => a.x - b.x || a.ip.localeCompare(b.ip));
      const lanes = Math.max(1, Math.ceil((here.length * IP_GAP) / Math.max((to - from - 1) * stepWidth + 150, 3 * IP_GAP)));
      for (let lane = 0; lane < lanes; lane += 1) {
        const inLane = here.filter((_, index) => index % lanes === lane);
        const xs = spread(inLane.map((item) => item.x), IP_GAP);
        inLane.forEach((item, index) => {
          positions[item.ip] = { x: xs[index], y: rowBottom + IP_BAND + lane * IP_LANE };
        });
      }
      blockTop = rowBottom + (here.length ? IP_BAND + lanes * IP_LANE : 0) + ROW_BLOCK_GAP;
    }
    offsetY = blockTop + 30;
  });

  // Anything still unplaced (an IP without a transaction in view) goes in a last row.
  let spare = 0;
  for (const n of nodes) {
    if (!positions[n.data.id]) {
      positions[n.data.id] = { x: spare * IP_GAP, y: offsetY };
      spare += 1;
    }
  }
  return positions;
}

function forceOptions(nodeCount) {
  return {
    name: 'cose',
    randomize: false,
    animate: false,
    fit: true,
    padding: 24,
    nodeDimensionsIncludeLabels: false,
    nodeRepulsion: () => (nodeCount > 120 ? 3500 : 6000),
    nodeOverlap: 10,
    idealEdgeLength: () => (nodeCount > 120 ? 45 : 65),
    edgeElasticity: () => 100,
    nestingFactor: 1.2,
    gravity: 0.35,
    numIter: nodeCount > 120 ? 900 : 1200,
    initialTemp: 200,
    coolingFactor: 0.95,
    minTemp: 1.0,
    componentSpacing: 60,
  };
}

function applyLayout(cy, elements, mode) {
  if (mode === 'flow') {
    const viewport = { width: Math.max(200, cy.width() - 56), height: Math.max(160, cy.height() - 56) };
    const positions = flowPositions(elements.nodes, elements.edges, viewport);
    cy.layout({ name: 'preset', positions: (node) => positions[node.id()], fit: true, padding: 28, animate: false }).run();
  } else {
    const positions = ringPositions(elements.nodes, elements.edges);
    cy.nodes().positions((node) => positions[node.id()]);
    cy.layout(forceOptions(elements.nodes.length)).run();
  }
  cy.fit(undefined, 28);
  if (cy.zoom() > 1.6) {
    cy.zoom(1.6);
    cy.center();
  }
}

function useColorSchemeVersion() {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const media = window.matchMedia?.('(prefers-color-scheme: dark)');
    if (!media) return undefined;
    const onChange = () => setVersion((v) => v + 1);
    media.addEventListener?.('change', onChange);
    return () => media.removeEventListener?.('change', onChange);
  }, []);
  return version;
}

/** Above the pointer target, flipped below near the top edge and pulled inward near the sides. */
function tooltipStyle(tip) {
  const below = tip.y - tip.offset < 110;
  const shiftX = tip.x < 150 ? '-14px' : tip.x > (tip.stage || Infinity) - 150 ? 'calc(-100% + 14px)' : '-50%';
  return {
    left: tip.x,
    top: below ? tip.y + tip.offset : tip.y - tip.offset,
    transform: `translate(${shiftX}, ${below ? '10px' : 'calc(-100% - 8px)'})`,
  };
}

function NodeTip({ tip }) {
  const flags = [
    tip.isFocus && 'focus of this graph',
    tip.isSeed && 'seed wallet',
    tip.service && 'service-like wallet',
    tip.isRelay && 'relay node',
  ].filter(Boolean);
  const where = [tip.country, fmtAsn(tip.asn, tip.asOrg) !== '—' ? fmtAsn(tip.asn, tip.asOrg) : ''].filter(Boolean).join(' · ');
  return (
    <>
      <div className="graph-tooltip-head">
        <strong>{tip.type === 'IP' ? 'IP address' : tip.type}</strong>
        {flags.length > 0 && <span className="muted">{flags.join(' · ')}</span>}
      </div>
      <code className="graph-tooltip-id">{tip.id}</code>
      <div>
        Risk <strong>{fmtScore(tip.riskScore)}</strong>{tip.level && <span> ({tip.level})</span>}
      </div>
      {tip.type === 'Transaction' && tip.timestamp && <div>{fmtTime(tip.timestamp)} UTC</div>}
      {tip.type === 'Wallet' && tip.actorId && <div className="muted">Actor {tip.actorId}{tip.entityId ? ` · entity ${tip.entityId}` : ''}</div>}
      {tip.type === 'IP' && where && <div>{where}</div>}
      {tip.isRelay && <div className="muted">Mostly passes on other parties’ transactions; not treated as their creator.</div>}
      {tip.traceStopped && <div><strong>Trace stops:</strong> service pools funds</div>}
      {!tip.isFocus && <div className="muted">Click to open</div>}
    </>
  );
}

function EdgeTip({ tip }) {
  if (tip.type === 'INPUT_FROM') return <div>Spent <strong>{fmtBtc(tip.amount)}</strong> in this transaction</div>;
  if (tip.type === 'OUTPUT_TO') return <div>Received <strong>{fmtBtc(tip.amount)}</strong> from this transaction</div>;
  if (tip.type === 'OBSERVED_IN') {
    return (
      <>
        <div><strong>First seen</strong> from this IP</div>
        {isNum(tip.confidence) && <div>Origin confidence {fmtPct(tip.confidence)}</div>}
        <div className="muted">{tip.attributed ? 'Attributed to the spending wallets' : 'Not attributed: the creator was probably not observed'}</div>
      </>
    );
  }
  if (tip.type === 'RELAYED_BY') {
    return <div><strong>Relayed</strong> by this IP{isNum(tip.delay) ? ` ${tip.delay.toFixed(2)} s after the first sighting` : ' after the first sighting'}</div>;
  }
  return <div>{tip.type}</div>;
}

export default function InvestigationGraph({ data, layout = 'force', onSelect }) {
  const containerRef = useRef(null);
  const cyRef = useRef(null);
  const onSelectRef = useRef(onSelect);

  const elements = useMemo(() => buildElements(data), [data]);
  const nodeCount = elements.nodes.length;
  const [labelsVisible, setLabelsVisible] = useState(() => nodeCount <= 80);
  const [layoutMode, setLayoutMode] = useState(layout === 'flow' ? 'flow' : 'force');
  const [tall, setTall] = useState(() => nodeCount > 60);
  const [tooltip, setTooltip] = useState(null);
  const schemeVersion = useColorSchemeVersion();

  // Everything the init effect reads lives in refs declared *after* the values
  // they mirror, so there is no temporal-dead-zone access during render.
  const labelsRef = useRef(labelsVisible);
  const layoutRef = useRef(layoutMode);
  useEffect(() => { onSelectRef.current = onSelect; }, [onSelect]);
  useEffect(() => { labelsRef.current = labelsVisible; }, [labelsVisible]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || nodeCount === 0) return undefined;
    let cancelled = false;
    let rafId = 0;

    const init = () => {
      if (cancelled || cyRef.current) return;
      const { width, height } = container.getBoundingClientRect();
      if (!width || !height) return;
      const cy = cytoscape({
        container,
        elements: [...elements.nodes, ...elements.edges],
        style: buildStylesheet(labelsRef.current, readPalette(container)),
        layout: { name: 'null' },
        minZoom: 0.1,
        maxZoom: 4,
        boxSelectionEnabled: false,
        autounselectify: true,
      });
      cyRef.current = cy;

      cy.on('mouseover', 'node', (event) => {
        const node = event.target;
        const near = node.closedNeighborhood();
        cy.elements().not(near).addClass('dim');
        node.connectedEdges().addClass('hl');
        container.style.cursor = node.data('isFocus') ? 'default' : 'pointer';
        setTooltip({ kind: 'node', ...node.data(), ...node.renderedPosition(), offset: node.renderedHeight() / 2, stage: container.clientWidth });
      });
      cy.on('mouseout', 'node', () => {
        cy.elements().removeClass('dim hl');
        container.style.cursor = '';
        setTooltip(null);
      });
      cy.on('mouseover', 'edge', (event) => {
        const edge = event.target;
        edge.addClass('hl');
        setTooltip({ kind: 'edge', ...edge.data(), ...edge.renderedMidpoint(), offset: 4, stage: container.clientWidth });
      });
      cy.on('mouseout', 'edge', (event) => {
        event.target.removeClass('hl');
        setTooltip(null);
      });
      cy.on('tap', 'node', (event) => {
        const node = event.target;
        const type = SELECT_TYPE[node.data('type')];
        if (!type || node.data('isFocus')) return;
        setTooltip(null);
        onSelectRef.current?.({ type, id: node.id() });
      });
      cy.on('pan zoom', () => setTooltip(null));

      applyLayout(cy, elements, layoutRef.current);
    };

    rafId = requestAnimationFrame(init);
    const observer = window.ResizeObserver
      ? new ResizeObserver(() => {
        if (cyRef.current) cyRef.current.resize();
        else init();
      })
      : null;
    observer?.observe(container);

    return () => {
      cancelled = true;
      cancelAnimationFrame(rafId);
      observer?.disconnect();
      cyRef.current?.destroy();
      cyRef.current = null;
    };
  }, [elements, nodeCount]);

  // Restyle on label toggle or colour-scheme change without rebuilding the graph.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !containerRef.current) return;
    cy.style().fromJson(buildStylesheet(labelsVisible, readPalette(containerRef.current))).update();
  }, [labelsVisible, schemeVersion]);

  // Re-run the layout when it is switched; the first layout is run by the init effect.
  useEffect(() => {
    if (layoutRef.current === layoutMode) return;
    layoutRef.current = layoutMode;
    if (cyRef.current) applyLayout(cyRef.current, elements, layoutMode);
  }, [layoutMode, elements]);

  // The canvas changed height: let Cytoscape re-measure, then lay the flow out for the new shape.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return undefined;
    const id = requestAnimationFrame(() => {
      cy.resize();
      if (layoutRef.current === 'flow') applyLayout(cy, elements, 'flow');
      else cy.fit(undefined, 28);
    });
    return () => cancelAnimationFrame(id);
  }, [tall, elements]);

  const fit = useCallback(() => cyRef.current?.fit(undefined, 28), []);
  const zoomBy = useCallback((factor) => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.zoom({ level: cy.zoom() * factor, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
  }, []);
  const focus = useCallback(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const target = cy.nodes('[?isFocus]');
    if (target.length) cy.animate({ center: { eles: target }, zoom: Math.max(cy.zoom(), 1.2), duration: 250 });
  }, []);

  if (nodeCount === 0) {
    return <div className="graph-empty">No linked wallets, transactions or IPs for this selection.</div>;
  }

  const counts = { Wallet: 0, Transaction: 0, IP: 0 };
  const present = { focus: false, seed: false, service: false, stopped: false, relayNode: false, observed: false, relayed: false };
  for (const n of elements.nodes) {
    counts[n.data.type] += 1;
    present.focus = present.focus || n.data.isFocus;
    present.seed = present.seed || n.data.isSeed;
    present.service = present.service || n.data.service;
    present.stopped = present.stopped || n.data.traceStopped;
    present.relayNode = present.relayNode || n.data.isRelay;
  }
  for (const e of elements.edges) {
    present.observed = present.observed || e.data.type === 'OBSERVED_IN';
    present.relayed = present.relayed || e.data.type === 'RELAYED_BY';
  }
  const summary = `${counts.Wallet} wallet${counts.Wallet === 1 ? '' : 's'}, ${counts.Transaction} transaction${counts.Transaction === 1 ? '' : 's'}, ${counts.IP} IP${counts.IP === 1 ? '' : 's'}`;

  return (
    <div className="inv-graph">
      <div className="graph-toolbar">
        <div className="graph-buttons">
          <button type="button" className="btn btn-small" onClick={fit} title="Fit the whole graph in view">Fit</button>
          {present.focus && <button type="button" className="btn btn-small" onClick={focus} title="Centre on the focus node">Focus</button>}
          <button type="button" className="btn btn-small" onClick={() => zoomBy(1.25)} aria-label="Zoom in" title="Zoom in">+</button>
          <button type="button" className="btn btn-small" onClick={() => zoomBy(0.8)} aria-label="Zoom out" title="Zoom out">−</button>
          <button
            type="button"
            className={`btn btn-small${labelsVisible ? ' active' : ''}`}
            aria-pressed={labelsVisible}
            onClick={() => setLabelsVisible((v) => !v)}
            title="Show ids on nodes and amounts on edges (amounts appear when zoomed in)"
          >
            Labels
          </button>
          <button
            type="button"
            className={`btn btn-small${layoutMode === 'flow' ? ' active' : ''}`}
            aria-pressed={layoutMode === 'flow'}
            onClick={() => setLayoutMode((mode) => (mode === 'flow' ? 'force' : 'flow'))}
            title="Lay the value flow out step by step (a long chain continues on the next row), or switch back to a force-directed layout"
          >
            Flow layout
          </button>
          <button
            type="button"
            className={`btn btn-small${tall ? ' active' : ''}`}
            aria-pressed={tall}
            onClick={() => setTall((value) => !value)}
            title="Give the graph more room"
          >
            Taller
          </button>
        </div>
        <span className="graph-meta">
          {summary}
          {data?.limited && <strong className="graph-limited"> · cut at {nodeCount} nodes</strong>}
        </span>
      </div>

      <div className="graph-stage">
        <div
          className={`graph-canvas${tall ? ' tall' : ''}`}
          ref={containerRef}
          role="img"
          aria-label={`Link-analysis graph with ${summary}. The evidence tables below list the same wallets, transactions and IPs.`}
        />
        {tooltip && (
          <div className="graph-tooltip" style={tooltipStyle(tooltip)} role="status">
            {tooltip.kind === 'edge' ? <EdgeTip tip={tooltip} /> : <NodeTip tip={tooltip} />}
          </div>
        )}
      </div>

      <ul className="graph-legend" aria-label="Graph legend">
        {LEVELS.map((level) => (
          <li key={level}><span className={`lg-dot risk-bg-${level.toLowerCase()}`} />{level}</li>
        ))}
        <li><span className="lg-shape lg-wallet" />Wallet</li>
        <li><span className="lg-shape lg-tx" />Transaction</li>
        <li><span className="lg-shape lg-ip" />IP</li>
        {present.focus && <li><span className="lg-shape lg-focus" />Focus</li>}
        {present.seed && <li><span className="lg-shape lg-seed" />Seed</li>}
        {present.service && <li title="Its entity deals with many counterparties (exchange, merchant, processor)"><span className="lg-shape lg-service" />Service-like wallet</li>}
        {present.stopped && <li title="A service pools the funds of many users, so the trace is not followed through it"><span className="lg-shape lg-stopped" />Trace stops: service pools funds</li>}
        {present.relayNode && <li title="An IP that mostly passes on other parties’ transactions"><span className="lg-shape lg-relay" />Relay node</li>}
        <li><span className="lg-arrow" aria-hidden="true">→</span>Value flow (labels in BTC)</li>
        {present.observed && <li><span className="lg-line lg-dash" aria-hidden="true" />First seen from IP</li>}
        {present.relayed && <li><span className="lg-line lg-dots" aria-hidden="true" />Later relay</li>}
      </ul>
    </div>
  );
}
