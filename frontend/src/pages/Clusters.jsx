/**
 * Clusters page – shows DBSCAN clustering results with a summary bar,
 * per-cluster cards, and an integrated graph visualization.
 *
 * Data source: existing /clusters and /alerts APIs (unchanged).
 * Graph data source: existing /datasets/:id/clusters/:cid/graph API (unchanged).
 */

import React, { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import api from '../api/client.js';
import InvestigationGraph from '../components/graph/InvestigationGraph.jsx';
import { useDataset } from '../context/DatasetContext.jsx';
import './Clusters.css';

// ── Risk level ordering for colour coding ────────────────────────────────────
const RISK_ORDER = { critical: 4, high: 3, medium: 2, low: 1 };
const riskClass = (level) => `risk-${(level || 'low').toLowerCase()}`;

// ── Derive summary stats from existing cluster / alert data ──────────────────
function buildSummary(clusterSummaries, alerts) {
  const totalClusters = clusterSummaries.length;
  const totalEntities = clusterSummaries.reduce((s, c) => s + c.entity_count, 0);
  const totalAlerts   = alerts.length;

  let highestRiskCluster = null;
  let highestScore = -1;
  clusterSummaries.forEach((c) => {
    if (c.highest_risk > highestScore) {
      highestScore = c.highest_risk;
      highestRiskCluster = c;
    }
  });

  const criticalCount = alerts.filter((a) => a.risk_level?.toLowerCase() === 'critical').length;
  const highCount     = alerts.filter((a) => a.risk_level?.toLowerCase() === 'high').length;

  return { totalClusters, totalEntities, totalAlerts, highestRiskCluster, criticalCount, highCount };
}

// ─────────────────────────────────────────────────────────────────────────────

export default function Clusters() {
  const { clusterId: routeClusterId } = useParams();
  const { selectedDatasetId, loadingDatasets, datasetsError, refreshDatasets } = useDataset();

  const [clusters,           setClusters]           = useState([]);
  const [alerts,             setAlerts]             = useState([]);
  const [loading,            setLoading]            = useState(true);
  const [error,              setError]              = useState(null);
  const [retryToken,         setRetryToken]         = useState(0);

  const [expandedClusterId,  setExpandedClusterId]  = useState(
    () => (routeClusterId ? Number(routeClusterId) : null),
  );

  const [graphData,          setGraphData]          = useState(null);
  const [graphLoading,       setGraphLoading]       = useState(false);
  const [graphError,         setGraphError]         = useState(null);
  const [graphRetryToken,    setGraphRetryToken]    = useState(0);

  const [selectedNode,       setSelectedNode]       = useState(null);

  // ── Fetch clusters + alerts ─────────────────────────────────────────────
  useEffect(() => {
    const controller = new AbortController();
    const fetchData = async () => {
      setLoading(true);
      setError(null);
      try {
        const [clustersRes, alertsRes] = await Promise.all([
          api.clusters(selectedDatasetId, { signal: controller.signal }),
          api.alerts(selectedDatasetId,   { signal: controller.signal }),
        ]);
        if (controller.signal.aborted) return;
        setClusters(clustersRes.data);
        setAlerts(alertsRes.data);
      } catch (err) {
        if (controller.signal.aborted || err.code === 'ERR_CANCELED') return;
        console.error('Failed to fetch clusters:', err);
        setError('Backend unavailable. Unable to fetch cluster data.');
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };

    if (selectedDatasetId) fetchData();
    else if (!loadingDatasets) setLoading(false);

    return () => controller.abort();
  }, [selectedDatasetId, loadingDatasets, retryToken]);

  // ── Route param → expanded cluster ─────────────────────────────────────
  useEffect(() => {
    setExpandedClusterId(routeClusterId ? Number(routeClusterId) : null);
  }, [routeClusterId]);

  // ── Reset graph when dataset changes ────────────────────────────────────
  useEffect(() => {
    setGraphData(null);
    setGraphError(null);
    setSelectedNode(null);
  }, [selectedDatasetId]);

  // ── Fetch graph for expanded cluster ────────────────────────────────────
  useEffect(() => {
    if (!selectedDatasetId || expandedClusterId === null) return undefined;
    const controller = new AbortController();

    const fetchGraph = async () => {
      setGraphLoading(true);
      setGraphError(null);
      try {
        const { data } = await api.clusterGraph(
          selectedDatasetId, expandedClusterId, { signal: controller.signal },
        );
        if (!controller.signal.aborted) setGraphData(data);
      } catch (err) {
        if (!controller.signal.aborted && err.code !== 'ERR_CANCELED') {
          setGraphError('Unable to load graph. Neo4j may be unavailable.');
        }
      } finally {
        if (!controller.signal.aborted) setGraphLoading(false);
      }
    };

    fetchGraph();
    return () => controller.abort();
  }, [selectedDatasetId, expandedClusterId, graphRetryToken]);

  // ── Loading / error / empty guards ──────────────────────────────────────
  if (loading) {
    return (
      <div className="clusters-page">
        <div className="clusters-loading">
          <div className="loading-spinner" />
          Loading clusters…
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="clusters-page">
        <div className="error-state">
          <h2>⚠️ Error</h2>
          <p>{error}</p>
          <button type="button" onClick={() => setRetryToken((v) => v + 1)}>Retry</button>
        </div>
      </div>
    );
  }

  if (clusters.length === 0) {
    return (
      <div className="clusters-page">
        <h1>Behavioral Clusters</h1>
        <div className="empty-state">
          <div className="empty-icon">🔬</div>
          <p>
            {datasetsError
              ? 'Backend unavailable.'
              : selectedDatasetId
              ? 'No clustering data available for this dataset.'
              : 'No analysis available. Upload a dataset to begin.'}
          </p>
          {datasetsError && (
            <button type="button" onClick={refreshDatasets}>Retry</button>
          )}
        </div>
      </div>
    );
  }

  // ── Build cluster summaries ──────────────────────────────────────────────
  const clusterGroups = {};
  clusters.forEach((item) => {
    const cid = item.cluster_id;
    if (!clusterGroups[cid]) clusterGroups[cid] = [];
    clusterGroups[cid].push(item.wallet_id);
  });

  const clusterSummaries = Object.entries(clusterGroups).map(([cid, walletIds]) => {
    const clusterAlerts = alerts.filter((a) => walletIds.includes(a.wallet_id));
    const avgRisk =
      clusterAlerts.length > 0
        ? clusterAlerts.reduce((sum, a) => sum + a.risk_score, 0) / clusterAlerts.length
        : 0;
    const highestRisk   = clusterAlerts.length > 0 ? Math.max(...clusterAlerts.map((a) => a.risk_score)) : 0;
    const highestAlert  = clusterAlerts.length > 0 ? clusterAlerts.find((a) => a.risk_score === highestRisk) : null;

    // Dominant risk level: pick the worst level present
    let domLevel = 'low';
    clusterAlerts.forEach((a) => {
      const lvl = (a.risk_level || '').toLowerCase();
      if ((RISK_ORDER[lvl] || 0) > (RISK_ORDER[domLevel] || 0)) domLevel = lvl;
    });

    return {
      cluster_id:           parseInt(cid),
      entity_count:         walletIds.length,
      average_risk:         avgRisk,
      highest_risk:         highestRisk,
      highest_risk_wallet:  highestAlert,
      dominant_risk_level:  domLevel,
      wallets:              walletIds,
    };
  });

  clusterSummaries.sort((a, b) => b.average_risk - a.average_risk);

  // ── Summary stats ────────────────────────────────────────────────────────
  const summary = buildSummary(clusterSummaries, alerts);

  // ── Toggle cluster expand ────────────────────────────────────────────────
  const toggleExpand = (cid) => {
    setGraphData(null);
    setSelectedNode(null);
    setExpandedClusterId((current) => (current === cid ? null : cid));
  };

  // ── Render ───────────────────────────────────────────────────────────────
  return (
    <div className="clusters-page">
      <h1>Behavioral Clusters</h1>

      <div className="clusters-intro">
        <p>
          Clusters represent groups of wallets with similar behavioral patterns detected by
          DBSCAN clustering. These groupings indicate potential relationships but do{' '}
          <strong>NOT</strong> confirm actual criminal organizations.
        </p>
      </div>

      {/* ── Summary stats bar ── */}
      <div className="clusters-summary">
        <div className="summary-stat">
          <span className="summary-stat-value">{summary.totalClusters}</span>
          <span className="summary-stat-label">Clusters</span>
        </div>
        <div className="summary-stat">
          <span className="summary-stat-value">{summary.totalEntities}</span>
          <span className="summary-stat-label">Entities</span>
        </div>
        <div className="summary-stat">
          <span className="summary-stat-value">{summary.totalAlerts}</span>
          <span className="summary-stat-label">Alerts</span>
        </div>
        {summary.criticalCount > 0 && (
          <div className="summary-stat summary-critical">
            <span className="summary-stat-value">{summary.criticalCount}</span>
            <span className="summary-stat-label">Critical</span>
          </div>
        )}
        {summary.highCount > 0 && (
          <div className="summary-stat summary-high">
            <span className="summary-stat-value">{summary.highCount}</span>
            <span className="summary-stat-label">High Risk</span>
          </div>
        )}
        {summary.highestRiskCluster && (
          <div className="summary-stat summary-top-cluster">
            <span className="summary-stat-value">Cluster {summary.highestRiskCluster.cluster_id}</span>
            <span className="summary-stat-label">
              Highest Risk ({summary.highestRiskCluster.highest_risk.toFixed(2)})
            </span>
          </div>
        )}
      </div>

      {/* ── Cluster list ── */}
      <div className="clusters-list">
        {clusterSummaries.map((cluster) => {
          const isExpanded = expandedClusterId === cluster.cluster_id;
          return (
            <div
              key={cluster.cluster_id}
              className={`cluster-card ${isExpanded ? 'cluster-card--expanded' : ''}`}
            >
              {/* Risk indicator bar */}
              <div className={`cluster-risk-bar ${riskClass(cluster.dominant_risk_level)}`} />

              <div
                className="cluster-header"
                onClick={() => toggleExpand(cluster.cluster_id)}
                role="button"
                tabIndex={0}
                aria-expanded={isExpanded}
                onKeyDown={(e) => e.key === 'Enter' && toggleExpand(cluster.cluster_id)}
              >
                <div className="cluster-title">
                  <h3>Cluster {cluster.cluster_id}</h3>
                  <span className="cluster-badge">{cluster.entity_count} entities</span>
                  {cluster.dominant_risk_level !== 'low' && (
                    <span className={`risk-badge ${riskClass(cluster.dominant_risk_level)}`}>
                      {cluster.dominant_risk_level}
                    </span>
                  )}
                </div>
                <div className="cluster-metrics">
                  <div className="metric">
                    <span className="metric-label">Avg Risk</span>
                    <span className="metric-value">{cluster.average_risk.toFixed(2)}</span>
                  </div>
                  <div className="metric">
                    <span className="metric-label">Max Risk</span>
                    <span className={`metric-value ${riskClass(cluster.highest_risk_wallet?.risk_level)}`}>
                      {cluster.highest_risk.toFixed(2)}
                    </span>
                  </div>
                </div>
                <button className="expand-btn" aria-hidden="true" tabIndex={-1}>
                  {isExpanded ? '▼' : '▶'}
                </button>
              </div>

              {/* ── Expanded detail ── */}
              {isExpanded && (
                <div className="cluster-details">
                  {/* Highest-risk entity */}
                  {cluster.highest_risk_wallet && (
                    <div className="highest-risk">
                      <h4>🔴 Highest-Risk Entity</h4>
                      <div className="entity-card">
                        <div className="entity-row">
                          <span>Wallet</span>
                          <code>{cluster.highest_risk_wallet.wallet_id}</code>
                        </div>
                        <div className="entity-row">
                          <span>Risk Score</span>
                          <strong>{cluster.highest_risk_wallet.risk_score.toFixed(2)}</strong>
                        </div>
                        <div className="entity-row">
                          <span>Risk Level</span>
                          <span className={`risk-badge ${riskClass(cluster.highest_risk_wallet.risk_level)}`}>
                            {cluster.highest_risk_wallet.risk_level}
                          </span>
                        </div>
                        <Link
                          to={`/entities/${cluster.highest_risk_wallet.wallet_id}`}
                          className="investigate-link"
                        >
                          Investigate →
                        </Link>
                      </div>
                    </div>
                  )}

                  {/* Entity grid */}
                  <div className="entities-in-cluster">
                    <h4>📋 All Entities ({cluster.entity_count})</h4>
                    <div className="entities-grid">
                      {cluster.wallets.map((wallet) => {
                        const alert = alerts.find((a) => a.wallet_id === wallet);
                        return (
                          <div key={wallet} className="entity-badge">
                            <Link to={`/entities/${wallet}`}>{wallet}</Link>
                            {alert && (
                              <span className={`risk-tag ${riskClass(alert.risk_level)}`}>
                                {alert.risk_level}
                              </span>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* ── Graph section ── */}
                  <div className="cluster-graph">
                    <div className="cluster-graph-header">
                      <h4>Graph Visualization</h4>
                      {graphData?.limited && (
                        <span className="graph-limit">
                          Limited to {graphData.max_nodes} nodes / {graphData.max_edges} relationships
                        </span>
                      )}
                    </div>

                    {graphLoading ? (
                      <div className="graph-loading-state">
                        <div className="loading-spinner" />
                        Loading graph…
                      </div>
                    ) : graphError ? (
                      <div className="graph-error-state">
                        <span>⚠️ {graphError}</span>
                        <button
                          type="button"
                          onClick={() => setGraphRetryToken((v) => v + 1)}
                        >
                          Retry
                        </button>
                      </div>
                    ) : graphData ? (
                      <InvestigationGraph
                        data={graphData}
                        walletId={cluster.highest_risk_wallet?.wallet_id}
                        onNodeSelect={setSelectedNode}
                      />
                    ) : (
                      <div className="graph-empty-state">No graph data for this cluster.</div>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
