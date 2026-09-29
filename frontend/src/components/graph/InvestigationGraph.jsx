/**
 * InvestigationGraph — Cytoscape link-analysis graph for the /graph endpoint.
 *
 * Nodes: Wallet | Transaction | IP (risk_level, is_seed, is_focus).
 * Edges: Wallet -INPUT_FROM-> Transaction -OUTPUT_TO-> Wallet, Transaction -OBSERVED_IN-> IP.
 * Arrows point in the direction value flows. Uses Cytoscape directly (no wrapper)
 * so initialisation timing is under our control.
 *
 * Clicking a Wallet or Transaction calls onSelect({type, id}); IP nodes show a
 * details card instead.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import cytoscape from 'cytoscape';
import { fmtBtc, fmtScore, isNum, normLevel } from '../../lib/format.js';
import './InvestigationGraph.css';

const LEVELS = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'];

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
        'text-max-width': 90,
        'min-zoomed-font-size': 7,
        'background-color': p.none,
        'border-width': 1.5,
        'border-color': p.nodeBorder,
        width: 22,
        height: 22,
      },
    },
    { selector: 'node[type = "Wallet"]', style: { shape: 'ellipse', width: 24, height: 24 } },
    { selector: 'node[type = "Transaction"]', style: { shape: 'round-rectangle', width: 18, height: 14 } },
    { selector: 'node[type = "IP"]', style: { shape: 'diamond', width: 16, height: 16, 'background-color': p.ip } },
    ...levelStyles,
    { selector: 'node[?isSeed]', style: { 'border-width': 4.5, 'border-color': p.seed } },
    {
      selector: 'node[?isFocus]',
      style: {
        width: 40,
        height: 40,
        'font-size': 11,
        'font-weight': 'bold',
        'underlay-color': p.focus,
        'underlay-opacity': 0.3,
        'underlay-padding': 7,
        'underlay-shape': 'ellipse',
        'z-index': 20,
      },
    },
    { selector: 'node[?isFocus][type = "Transaction"]', style: { width: 36, height: 26 } },
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
        label: labelsVisible ? 'data(amountLabel)' : '',
        'font-size': 8,
        color: p.text,
        'text-background-color': p.textBg,
        'text-background-opacity': 0.75,
        'text-background-padding': '1px',
        'text-rotation': 'autorotate',
        'min-zoomed-font-size': 8,
      },
    },
    { selector: 'edge[type = "OBSERVED_IN"]', style: { 'line-style': 'dashed', 'target-arrow-shape': 'none', width: 1, opacity: 0.6 } },
    { selector: '.hl', style: { 'line-color': p.focus, 'target-arrow-color': p.focus, opacity: 1, 'z-index': 15 } },
    { selector: '.faded', style: { opacity: 0.15 } },
  ];
}

// Direction each edge type must have so arrows follow value flow.
const EXPECTED = {
  INPUT_FROM: ['Wallet', 'Transaction'],
  OUTPUT_TO: ['Transaction', 'Wallet'],
  OBSERVED_IN: ['Transaction', 'IP'],
};

function buildElements(data) {
  const rawNodes = Array.isArray(data?.nodes) ? data.nodes : [];
  const rawEdges = Array.isArray(data?.edges) ? data.edges : [];
  const types = new Map();
  const nodes = [];
  for (const node of rawNodes) {
    const id = node?.id !== null && node?.id !== undefined ? String(node.id) : '';
    if (!id || types.has(id)) continue;
    const type = ['Wallet', 'Transaction', 'IP'].includes(node.type) ? node.type : 'Wallet';
    types.set(id, type);
    const label = String(node.label || id);
    nodes.push({
      group: 'nodes',
      data: {
        id,
        type,
        label: label.length > 18 ? `${label.slice(0, 8)}…${label.slice(-6)}` : label,
        fullLabel: label,
        level: type === 'IP' ? '' : normLevel(node.risk_level) || '',
        riskScore: isNum(node.risk_score) ? node.risk_score : null,
        isSeed: Boolean(node.is_seed),
        isFocus: Boolean(node.is_focus),
        country: node.country,
        asn: node.asn,
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
    const w = amount !== null && maxAmount > 0 ? 1 + 3 * (Math.log1p(amount) / Math.log1p(maxAmount)) : 1.2;
    edges.push({
      group: 'edges',
      data: { id, source, target, type: edge.type || '', amount, amountLabel: amount !== null ? fmtBtc(amount) : '', w },
    });
  });
  return { nodes, edges };
}

/** Deterministic starting positions: BFS rings around the focus node(s). */
function initialPositions(nodes, edges) {
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

function layoutOptions(nodeCount) {
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
    numIter: 1200,
    initialTemp: 200,
    coolingFactor: 0.95,
    minTemp: 1.0,
    componentSpacing: 60,
  };
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

export default function InvestigationGraph({ data, onSelect }) {
  const containerRef = useRef(null);
  const cyRef = useRef(null);
  const onSelectRef = useRef(onSelect);

  const elements = useMemo(() => buildElements(data), [data]);
  const nodeCount = elements.nodes.length;
  const [labelsVisible, setLabelsVisible] = useState(() => nodeCount <= 80);
  const [tooltip, setTooltip] = useState(null);
  const schemeVersion = useColorSchemeVersion();

  // Everything the init effect reads lives in refs declared *after* the values
  // they mirror, so there is no temporal-dead-zone access during render.
  const labelsRef = useRef(labelsVisible);
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
      const positions = initialPositions(elements.nodes, elements.edges);
      const cy = cytoscape({
        container,
        elements: [
          ...elements.nodes.map((n) => ({ ...n, position: positions[n.data.id] })),
          ...elements.edges,
        ],
        style: buildStylesheet(labelsRef.current, readPalette(container)),
        minZoom: 0.15,
        maxZoom: 4,
        boxSelectionEnabled: false,
        autounselectify: true,
      });
      cyRef.current = cy;

      const showTip = (node, pinned) => {
        const pos = node.renderedPosition();
        setTooltip({ ...node.data(), x: pos.x, y: pos.y, pinned });
      };
      cy.on('mouseover', 'node', (event) => {
        const node = event.target;
        node.connectedEdges().addClass('hl');
        container.style.cursor = node.data('type') === 'IP' ? 'help' : 'pointer';
        setTooltip((current) => (current?.pinned ? current : { ...node.data(), ...node.renderedPosition(), pinned: false }));
      });
      cy.on('mouseout', 'node', (event) => {
        event.target.connectedEdges().removeClass('hl');
        container.style.cursor = '';
        setTooltip((current) => (current?.pinned ? current : null));
      });
      cy.on('tap', 'node', (event) => {
        const node = event.target;
        const type = node.data('type');
        if (type === 'Wallet' || type === 'Transaction') {
          setTooltip(null);
          onSelectRef.current?.({ type: type === 'Wallet' ? 'wallet' : 'transaction', id: node.id() });
        } else {
          showTip(node, true);
        }
      });
      cy.on('tap', (event) => {
        if (event.target === cy) setTooltip(null);
      });
      cy.on('pan zoom', () => setTooltip((current) => (current?.pinned ? null : current)));

      cy.layout(layoutOptions(nodeCount)).run();
      cy.fit(undefined, 24);
      if (cy.zoom() > 1.6) {
        cy.zoom(1.6);
        cy.center();
      }
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

  const fit = useCallback(() => cyRef.current?.fit(undefined, 24), []);
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

  const counts = elements.nodes.reduce((acc, n) => {
    acc[n.data.type] = (acc[n.data.type] || 0) + 1;
    return acc;
  }, {});

  return (
    <div className="inv-graph">
      <div className="graph-toolbar">
        <div className="graph-buttons">
          <button type="button" className="btn btn-small" onClick={fit} title="Fit graph to view">Fit</button>
          <button type="button" className="btn btn-small" onClick={focus} title="Centre on focus node">Focus</button>
          <button type="button" className="btn btn-small" onClick={() => zoomBy(1.25)} aria-label="Zoom in" title="Zoom in">+</button>
          <button type="button" className="btn btn-small" onClick={() => zoomBy(0.8)} aria-label="Zoom out" title="Zoom out">−</button>
          <button
            type="button"
            className={`btn btn-small${labelsVisible ? ' active' : ''}`}
            aria-pressed={labelsVisible}
            onClick={() => setLabelsVisible((v) => !v)}
          >
            Labels
          </button>
        </div>
        <span className="graph-meta">
          {counts.Wallet || 0} wallets · {counts.Transaction || 0} txs · {counts.IP || 0} IPs
          {data?.source && <> · source: {data.source}</>}
          {data?.limited && <strong className="graph-limited"> · truncated</strong>}
        </span>
      </div>

      <div className="graph-stage">
        <div className="graph-canvas" ref={containerRef} aria-label="Link analysis graph" role="img" />
        {tooltip && (
          <div
            className={`graph-tooltip${tooltip.pinned ? ' pinned' : ''}`}
            style={{ left: tooltip.x, top: tooltip.y }}
            role="status"
          >
            <div className="graph-tooltip-head">
              <strong>{tooltip.type}</strong>
              {tooltip.pinned && (
                <button type="button" className="icon-button" onClick={() => setTooltip(null)} aria-label="Close details">✕</button>
              )}
            </div>
            <code className="graph-tooltip-id">{tooltip.fullLabel || tooltip.id}</code>
            {tooltip.type !== 'IP' && (
              <div>
                Risk {fmtScore(tooltip.riskScore)} {tooltip.level && <span>({tooltip.level})</span>}
                {tooltip.isSeed && <span> · seed</span>}
              </div>
            )}
            {tooltip.type === 'IP' && (tooltip.country || tooltip.asn) && (
              <div>{[tooltip.country, tooltip.asn && `AS${tooltip.asn}`].filter(Boolean).join(' · ')}</div>
            )}
            {tooltip.type !== 'IP' && !tooltip.pinned && <div className="muted">Click to investigate</div>}
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
        <li><span className="lg-shape lg-seed" />Seed</li>
        <li><span className="lg-shape lg-focus" />Focus</li>
        <li><span className="lg-arrow" aria-hidden="true">→</span>Value flow</li>
        <li><span className="lg-dash" aria-hidden="true" />Observed at IP</li>
      </ul>
    </div>
  );
}
