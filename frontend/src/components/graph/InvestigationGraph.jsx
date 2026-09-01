/**
 * InvestigationGraph â€“ Cytoscape-based link-analysis graph for the Clusters page.
 *
 * Layout: uses Cytoscape's built-in `cose` layout (physics-based, produces
 * well-separated nodes). Zero external layout plugins required.
 * Falls back to `breadthfirst` when the graph is large (> 150 nodes) for performance.
 *
 * Controls: Fit, Center, Zoom In, Zoom Out, Reset Layout, Toggle Labels, Search Node.
 * Node click: shows a side panel with all properties returned by the API.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import CytoscapeComponent from 'react-cytoscapejs';
import './InvestigationGraph.css';

// â”€â”€â”€ Local error boundary â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// Prevents a Cytoscape initialization crash from blanking the entire Clusters page.
class GraphErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, message: '' };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, message: error?.message || 'Unknown error' };
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="graph-state">
          <span className="graph-state-icon">âš ï¸</span>
          <p>Graph rendering failed: {this.state.message}</p>
          <p style={{ fontSize: 12, color: '#4a7080' }}>
            The rest of the cluster data is still available above.
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}

// â”€â”€â”€ Node types â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const NODE_TYPES = ['Wallet', 'Transaction', 'IP', 'ASN', 'Country'];

// Colours per type â€“ bright but professional
const TYPE_COLORS = {
  Wallet:      { bg: '#e07b39', border: '#ffb07a', shape: 'ellipse' },
  Transaction: { bg: '#3a86cc', border: '#7ec8f5', shape: 'roundrectangle' },
  IP:          { bg: '#5aa05a', border: '#9de06d', shape: 'diamond' },
  ASN:         { bg: '#9b59b6', border: '#d8a9f0', shape: 'hexagon' },
  Country:     { bg: '#c9a227', border: '#f5d97a', shape: 'star' },
};

// â”€â”€â”€ Cytoscape stylesheet â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const buildStylesheet = (labelsVisible) => [
  {
    selector: 'node',
    style: {
      label: labelsVisible ? 'data(label)' : '',
      color: '#f0ece0',
      'font-size': 10,
      'font-family': '"Segoe UI", system-ui, sans-serif',
      'text-wrap': 'ellipsis',
      'text-max-width': 100,
      'text-valign': 'bottom',
      'text-halign': 'center',
      'text-margin-y': 6,
      'text-background-color': '#0d1b26',
      'text-background-opacity': 0.75,
      'text-background-padding': '3px',
      'text-background-shape': 'roundrectangle',
      'background-color': '#4a5a68',
      width: 32,
      height: 32,
      'border-width': 2,
      'border-color': '#6a7f8e',
      'transition-property': 'border-color, border-width, background-color, shadow-blur',
      'transition-duration': '180ms',
    },
  },
  // Per-type node overrides
  {
    selector: 'node[type="Wallet"]',
    style: {
      shape: 'ellipse',
      'background-color': TYPE_COLORS.Wallet.bg,
      'border-color': TYPE_COLORS.Wallet.border,
      width: 44,
      height: 44,
    },
  },
  {
    selector: 'node[type="Transaction"]',
    style: {
      shape: 'roundrectangle',
      'background-color': TYPE_COLORS.Transaction.bg,
      'border-color': TYPE_COLORS.Transaction.border,
      width: 38,
      height: 28,
    },
  },
  {
    selector: 'node[type="IP"]',
    style: {
      shape: 'diamond',
      'background-color': TYPE_COLORS.IP.bg,
      'border-color': TYPE_COLORS.IP.border,
      width: 34,
      height: 34,
    },
  },
  {
    selector: 'node[type="ASN"]',
    style: {
      shape: 'hexagon',
      'background-color': TYPE_COLORS.ASN.bg,
      'border-color': TYPE_COLORS.ASN.border,
      width: 32,
      height: 32,
    },
  },
  {
    selector: 'node[type="Country"]',
    style: {
      shape: 'star',
      'background-color': TYPE_COLORS.Country.bg,
      'border-color': TYPE_COLORS.Country.border,
      width: 34,
      height: 34,
    },
  },
  // Edges
  {
    selector: 'edge',
    style: {
      width: 1.5,
      'line-color': '#4a6070',
      'target-arrow-color': '#4a6070',
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.9,
      'curve-style': 'bezier',
      label: labelsVisible ? 'data(type)' : '',
      color: '#8da0ad',
      'font-size': 8,
      'text-background-color': '#0d1b26',
      'text-background-opacity': 0.8,
      'text-background-padding': '2px',
      'text-rotation': 'autorotate',
    },
  },
  // Interaction states
  {
    selector: '.focus',
    style: {
      'border-width': 4,
      'border-color': '#f5d35a',
      'shadow-blur': 18,
      'shadow-color': '#f5d35a',
      'shadow-opacity': 0.85,
      'z-index': 10,
    },
  },
  {
    selector: '.connected',
    style: {
      'line-color': '#f5d35a',
      'target-arrow-color': '#f5d35a',
      width: 2.5,
      opacity: 1,
    },
  },
  {
    selector: '.dimmed',
    style: { opacity: 0.12 },
  },
  {
    selector: '.search-hit',
    style: {
      'border-width': 5,
      'border-color': '#00e5ff',
      'shadow-blur': 22,
      'shadow-color': '#00e5ff',
      'shadow-opacity': 0.9,
      'z-index': 20,
    },
  },
];

// â”€â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function shortLabel(node) {
  const raw = String(node.label || node.id || '');
  return raw.length > 22 ? `${raw.slice(0, 19)}â€¦` : raw;
}

function chooseLayout(nodeCount) {
  if (nodeCount > 150) {
    return {
      name: 'breadthfirst',
      directed: true,
      animate: false,
      padding: 40,
      spacingFactor: 1.4,
    };
  }
  return {
    name: 'cose',          // built-in physics layout â€“ no plugin needed
    animate: false,
    padding: 50,
    nodeRepulsion: () => 8000,
    idealEdgeLength: () => 80,
    edgeElasticity: () => 100,
    gravity: 0.35,
    numIter: 1000,
    randomize: true,
    componentSpacing: 60,
    nestingFactor: 1.2,
    coolingFactor: 0.99,
    minTemp: 1.0,
  };
}

// Format a raw property value for display
function fmtValue(val) {
  if (val === null || val === undefined) return 'â€”';
  if (typeof val === 'number') return Number.isInteger(val) ? String(val) : val.toFixed(4);
  return String(val);
}

// â”€â”€â”€ Component â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export default function InvestigationGraph({ data, walletId, onNodeSelect }) {
  const cyRef = useRef(null);
  const containerRef = useRef(null);
  const tapHandlerRef = useRef(null);

  const [labelsVisible, setLabelsVisible] = useState(true);
  const [filters, setFilters] = useState(() => new Set(NODE_TYPES));
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedNodeData, setSelectedNodeData] = useState(null);

  // â”€â”€ Elements memo â€“ only recalculated when data changes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  // Validates and sanitises API data so Cytoscape never receives:
  //   â€¢ nodes with undefined/duplicate IDs (would throw "Can't create second element")
  //   â€¢ edges whose source/target reference a missing node
  //   â€¢ edges missing source or target fields entirely
  const elements = useMemo(() => {
    const rawNodes = Array.isArray(data?.nodes) ? data.nodes : [];
    const rawEdges = Array.isArray(data?.edges) ? data.edges : [];

    // Build validated node list (skip any node whose id is missing/null/undefined)
    const seenIds = new Set();
    const validNodes = [];
    for (const node of rawNodes) {
      const nodeId = node?.id != null ? String(node.id) : null;
      if (!nodeId || seenIds.has(nodeId)) continue; // skip missing/duplicate IDs
      seenIds.add(nodeId);
      // Flatten nested `properties` object into data so they appear in the details panel.
      const { properties, ...rest } = node;
      validNodes.push({
        data: {
          ...rest,
          ...(properties && typeof properties === 'object' ? properties : {}),
          id: nodeId,
          label: shortLabel({ ...rest, id: nodeId }),
        },
      });
    }

    // Build validated edge list (skip edges with missing source/target or orphaned refs)
    const seenEdgeIds = new Set();
    const validEdges = [];
    for (const edge of rawEdges) {
      const edgeId = edge?.id != null ? String(edge.id) : null;
      const src   = edge?.source != null ? String(edge.source) : null;
      const tgt   = edge?.target != null ? String(edge.target) : null;
      if (!src || !tgt) continue;              // must have source + target
      if (!seenIds.has(src) || !seenIds.has(tgt)) continue; // both endpoints must exist
      if (edgeId && seenEdgeIds.has(edgeId)) continue;       // no duplicate edge IDs
      if (edgeId) seenEdgeIds.add(edgeId);
      const { properties: edgeProps, ...edgeRest } = edge;
      validEdges.push({
        data: {
          ...edgeRest,
          ...(edgeProps && typeof edgeProps === 'object' ? edgeProps : {}),
          source: src,
          target: tgt,
          ...(edgeId ? { id: edgeId } : {}),
        },
      });
    }

    return [...validNodes, ...validEdges];
  }, [data]);

  const nodeCount = data?.nodes?.length ?? 0;

  // â”€â”€ Stylesheet memo â€“ recalculated only when labelsVisible changes â”€â”€â”€â”€â”€â”€â”€â”€
  const stylesheet = useMemo(() => buildStylesheet(labelsVisible), [labelsVisible]);

  // â”€â”€ Highlight wallet on prop change â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass('focus connected dimmed search-hit');
    if (!walletId) return;
    const root = cy.getElementById(walletId);
    if (root.length) {
      root.addClass('focus');
      root.connectedEdges().addClass('connected');
      root.neighborhood().nodes().not(root).addClass('focus');
      cy.elements().not(root.union(root.neighborhood())).addClass('dimmed');
    }
  }, [walletId, elements]);

  // â”€â”€ ResizeObserver for responsive canvas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  useEffect(() => {
    const el = containerRef.current;
    if (!el || !window.ResizeObserver) return undefined;
    const obs = new ResizeObserver(() => {
      cyRef.current?.resize();
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  // â”€â”€ Cleanup on unmount â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  useEffect(
    () => () => {
      const cy = cyRef.current;
      if (cy && tapHandlerRef.current) {
        cy.off('tap', 'node', tapHandlerRef.current);
      }
    },
    [],
  );

  // â”€â”€ Node tap handler â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const handleNodeTap = useCallback(
    (event) => {
      const node = event.target;
      const cy = cyRef.current;
      if (!cy) return;

      cy.elements().removeClass('focus connected dimmed search-hit');
      node.addClass('focus');
      node.connectedEdges().addClass('connected');
      node.neighborhood().nodes().not(node).addClass('focus');
      cy.elements().not(node.union(node.neighborhood())).addClass('dimmed');

      const nodeData = node.data();
      setSelectedNodeData(nodeData);
      onNodeSelect?.(nodeData);
    },
    [onNodeSelect],
  );

  // â”€â”€ cy callback â€“ wire up once, reuse same instance â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const handleCyInit = useCallback(
    (cy) => {
      if (cyRef.current === cy) return;
      // Remove old handler before switching instance
      if (cyRef.current && tapHandlerRef.current) {
        cyRef.current.off('tap', 'node', tapHandlerRef.current);
      }
      cyRef.current = cy;
      tapHandlerRef.current = handleNodeTap;
      cy.on('tap', 'node', tapHandlerRef.current);
    },
    [handleNodeTap],
  );

  // â”€â”€ Filter toggle â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const applyFilters = useCallback((nextFilters) => {
    setFilters(nextFilters);
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().forEach((n) => {
      n.toggleClass('dimmed', !nextFilters.has(n.data('type')));
    });
  }, []);

  // â”€â”€ Toolbar actions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const handleFit = useCallback(() => cyRef.current?.fit(undefined, 40), []);

  const handleCenter = useCallback(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.animate({ center: { eles: cy.elements() }, duration: 250 });
  }, []);

  const handleZoomIn = useCallback(() => {
    const cy = cyRef.current;
    if (cy) cy.zoom({ level: cy.zoom() * 1.25, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
  }, []);

  const handleZoomOut = useCallback(() => {
    const cy = cyRef.current;
    if (cy) cy.zoom({ level: cy.zoom() / 1.25, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
  }, []);

  const handleResetLayout = useCallback(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass('focus connected dimmed search-hit');
    setSelectedNodeData(null);
    cy.layout(chooseLayout(cy.nodes().length)).run();
  }, []);

  const handleToggleLabels = useCallback(() => setLabelsVisible((v) => !v), []);

  // â”€â”€ Search â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const handleSearch = useCallback(
    (e) => {
      e.preventDefault();
      const cy = cyRef.current;
      if (!cy || !searchQuery.trim()) return;

      const q = searchQuery.trim().toLowerCase();
      cy.elements().removeClass('focus connected dimmed search-hit');

      const hits = cy.nodes().filter((n) => {
        const id = String(n.data('id') || '').toLowerCase();
        const label = String(n.data('label') || '').toLowerCase();
        const address = String(n.data('address') || '').toLowerCase();
        return id.includes(q) || label.includes(q) || address.includes(q);
      });

      if (!hits.length) return;

      // Dim everything else
      cy.elements().addClass('dimmed');
      hits.forEach((n) => {
        n.removeClass('dimmed').addClass('search-hit');
        n.connectedEdges().removeClass('dimmed').addClass('connected');
        n.neighborhood().nodes().removeClass('dimmed');
      });

      // Centre on first hit
      cy.animate({ center: { eles: hits.first() }, zoom: Math.max(cy.zoom(), 1.2), duration: 400 });

      // If single hit, show its details
      if (hits.length === 1) {
        const nd = hits.first().data();
        setSelectedNodeData(nd);
        onNodeSelect?.(nd);
      }
    },
    [searchQuery, onNodeSelect],
  );

  const handleClearSearch = useCallback(() => {
    setSearchQuery('');
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass('focus connected dimmed search-hit');
    setSelectedNodeData(null);
  }, []);

  // â”€â”€ Dismiss details panel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const handleDismissDetails = useCallback(() => {
    setSelectedNodeData(null);
    cyRef.current?.elements().removeClass('focus connected dimmed search-hit');
    onNodeSelect?.(null);
  }, [onNodeSelect]);

  // â”€â”€ Guard: graph data unavailable â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  if (!data?.graph_available) {
    return (
      <div className="graph-state">
        <span className="graph-state-icon">âš ï¸</span>
        <p>Neo4j unavailable. The graph could not be retrieved.</p>
      </div>
    );
  }
  if (!data.nodes?.length) {
    return (
      <div className="graph-state">
        <span className="graph-state-icon">ðŸ”</span>
        <p>No graph data available for this cluster.</p>
      </div>
    );
  }

  const layout = chooseLayout(nodeCount);

  // â”€â”€ Render â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  return (
    <div className="investigation-graph">
      {/* â”€â”€ Toolbar â”€â”€ */}
      <div className="graph-toolbar">
        <div className="toolbar-section toolbar-actions">
          <button type="button" className="toolbar-btn" onClick={handleFit} title="Fit graph to view">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>
            Fit
          </button>
          <button type="button" className="toolbar-btn" onClick={handleCenter} title="Center graph">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v4M12 19v4M1 12h4M19 12h4"/></svg>
            Center
          </button>
          <button type="button" className="toolbar-btn icon-btn" onClick={handleZoomIn} title="Zoom in" aria-label="Zoom in">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M12 5v14M5 12h14"/></svg>
          </button>
          <button type="button" className="toolbar-btn icon-btn" onClick={handleZoomOut} title="Zoom out" aria-label="Zoom out">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M5 12h14"/></svg>
          </button>
          <button type="button" className="toolbar-btn" onClick={handleResetLayout} title="Re-run layout">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>
            Reset
          </button>
          <button
            type="button"
            className={`toolbar-btn${labelsVisible ? ' active' : ''}`}
            onClick={handleToggleLabels}
            title="Toggle labels"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>
            Labels
          </button>
        </div>

        {/* Search */}
        <form className="toolbar-section toolbar-search" onSubmit={handleSearch}>
          <input
            type="search"
            className="search-input"
            placeholder="Search nodeâ€¦"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            aria-label="Search node by name or address"
          />
          <button type="submit" className="toolbar-btn search-btn" title="Search">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
          </button>
          {searchQuery && (
            <button type="button" className="toolbar-btn clear-btn" onClick={handleClearSearch} title="Clear search">âœ•</button>
          )}
        </form>
      </div>

      {/* â”€â”€ Filters â”€â”€ */}
      <div className="graph-filters">
        {NODE_TYPES.map((type) => {
          const col = TYPE_COLORS[type];
          return (
            <label key={type} className={`filter-chip${filters.has(type) ? ' checked' : ''}`} style={{ '--chip-color': col.bg }}>
              <input
                type="checkbox"
                checked={filters.has(type)}
                onChange={() => {
                  const next = new Set(filters);
                  if (next.has(type)) next.delete(type);
                  else next.add(type);
                  applyFilters(next);
                }}
              />
              <span className="chip-dot" style={{ background: col.bg }} />
              {type}
            </label>
          );
        })}
        <span className="node-count">{nodeCount} nodes</span>
      </div>

      {/* â”€â”€ Canvas + side panel â”€â”€ */}
      <div className="graph-body">
        <div className="graph-canvas" ref={containerRef}>
          <GraphErrorBoundary>
            <CytoscapeComponent
              elements={elements}
              stylesheet={stylesheet}
              cy={handleCyInit}
              layout={layout}
              style={{ width: '100%', height: '100%' }}
              minZoom={0.1}
              maxZoom={4}
              wheelSensitivity={0.3}
            />
          </GraphErrorBoundary>
        </div>

        {/* â”€â”€ Node details panel â”€â”€ */}
        {selectedNodeData && (
          <div className="node-details-panel" role="complementary" aria-label="Node details">
            <div className="node-details-header">
              <span
                className="node-details-type"
                style={{ background: TYPE_COLORS[selectedNodeData.type]?.bg || '#4a5a68' }}
              >
                {selectedNodeData.type || 'Node'}
              </span>
              <button
                type="button"
                className="node-details-close"
                onClick={handleDismissDetails}
                aria-label="Close details"
              >
                âœ•
              </button>
            </div>
            <div className="node-details-body">
              {Object.entries(selectedNodeData)
                .filter(([k]) => !['label', 'source', 'target'].includes(k))
                .map(([key, val]) => (
                  <div key={key} className="node-prop">
                    <span className="node-prop-key">{key}</span>
                    <span className="node-prop-val" title={fmtValue(val)}>
                      {fmtValue(val)}
                    </span>
                  </div>
                ))}
            </div>
          </div>
        )}
      </div>

      {/* â”€â”€ Legend â”€â”€ */}
      <div className="graph-legend">
        {NODE_TYPES.map((type) => {
          const col = TYPE_COLORS[type];
          return (
            <span key={type} className="legend-item">
              <span className="legend-dot" style={{ background: col.bg, border: `1.5px solid ${col.border}` }} />
              {type}
            </span>
          );
        })}
        <span className="legend-item">
          <span className="legend-dot legend-edge" />
          Relationship
        </span>
      </div>
    </div>
  );
}
