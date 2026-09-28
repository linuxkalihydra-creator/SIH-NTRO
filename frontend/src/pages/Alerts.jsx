/**
 * Alerts page component with table, filtering, and sorting.
 */

import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client.js';
import { useDataset } from '../context/DatasetContext.jsx';
import './Alerts.css';

export default function Alerts() {
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sortBy, setSortBy] = useState('risk_score');
  const [sortDesc, setSortDesc] = useState(true);
  const [filterLevel, setFilterLevel] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const navigate = useNavigate();
  const { selectedDatasetId, loadingDatasets, datasetsError, refreshDatasets } = useDataset();
  const [retryToken, setRetryToken] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const fetchAlerts = async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await api.alerts(selectedDatasetId, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setAlerts(res.data);
      } catch (err) {
        if (controller.signal.aborted || err.code === 'ERR_CANCELED') return;
        console.error('Failed to fetch alerts:', err);
        setError('Backend unavailable. Unable to fetch alerts.');
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };

    if (selectedDatasetId) fetchAlerts();
    else if (!loadingDatasets) setLoading(false);
    return () => controller.abort();
  }, [selectedDatasetId, loadingDatasets, retryToken]);

  const filtered = alerts
    .filter((alert) => !filterLevel || alert.risk_level === filterLevel)
    .filter((alert) => !searchQuery || alert.wallet_id.toLowerCase().includes(searchQuery.toLowerCase()))
    .sort((a, b) => {
      let aVal = a[sortBy];
      let bVal = b[sortBy];
      if (typeof aVal === 'string') {
        aVal = aVal.toLowerCase();
        bVal = bVal.toLowerCase();
      }
      if (aVal < bVal) return sortDesc ? 1 : -1;
      if (aVal > bVal) return sortDesc ? -1 : 1;
      return 0;
    });

  const handleSort = (column) => {
    if (sortBy === column) {
      setSortDesc(!sortDesc);
    } else {
      setSortBy(column);
      setSortDesc(true);
    }
  };

  const handleRowClick = (walletId) => {
    navigate(`/entities/${walletId}`);
  };

  if (loading) {
    return (
      <div className="alerts-page">
        <div className="loading">Loading alerts...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="alerts-page">
        <div className="error-state">
          <h2>⚠️ Error</h2>
          <p>{error}</p>
          <button type="button" onClick={() => setRetryToken((value) => value + 1)}>Retry</button>
        </div>
      </div>
    );
  }

  if (alerts.length === 0) {
    return (
      <div className="alerts-page">
        <h1>Alerts</h1>
        <div className="empty-state">
          <p>{datasetsError ? 'Backend unavailable.' : selectedDatasetId ? 'No alerts found in the current dataset.' : 'No alerts available. Upload a dataset to begin.'}</p>
          {datasetsError && <button type="button" onClick={refreshDatasets}>Retry</button>}
        </div>
      </div>
    );
  }

  const riskLevels = [...new Set(alerts.map((a) => a.risk_level))];

  return (
    <div className="alerts-page">
      <h1>Alerts</h1>

      <div className="controls">
        <div className="search-box">
          <input
            type="text"
            placeholder="Search by wallet ID..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        <div className="filter-controls">
          <label>Filter by Risk Level:</label>
          <select value={filterLevel} onChange={(e) => setFilterLevel(e.target.value)}>
            <option value="">All Levels</option>
            {riskLevels.map((level) => (
              <option key={level} value={level}>
                {level}
              </option>
            ))}
          </select>
        </div>

        <div className="alert-count">
          {filtered.length} of {alerts.length} alerts
        </div>
      </div>

      <div className="table-container">
        <table className="alerts-table">
          <thead>
            <tr>
              <th onClick={() => handleSort('wallet_id')} className="sortable">
                Wallet ID {sortBy === 'wallet_id' && (sortDesc ? '▼' : '▲')}
              </th>
              <th onClick={() => handleSort('risk_score')} className="sortable">
                Risk Score {sortBy === 'risk_score' && (sortDesc ? '▼' : '▲')}
              </th>
              <th onClick={() => handleSort('risk_level')} className="sortable">
                Risk Level {sortBy === 'risk_level' && (sortDesc ? '▼' : '▲')}
              </th>
              <th onClick={() => handleSort('confidence')} className="sortable">
                Confidence {sortBy === 'confidence' && (sortDesc ? '▼' : '▲')}
              </th>
              <th>Cluster</th>
              <th>Top Reason</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((alert) => (
              <tr key={alert.wallet_id} className="alert-row">
                <td className="wallet-id">{alert.wallet_id}</td>
                <td className="risk-score">{alert.risk_score.toFixed(2)}</td>
                <td>
                  <span className={`risk-badge risk-${alert.risk_level.toLowerCase()}`}>
                    {alert.risk_level}
                  </span>
                </td>
                <td className="confidence">{(alert.confidence * 100).toFixed(0)}%</td>
                <td className="cluster">{alert.cluster_id ?? 'N/A'}</td>
                <td className="reason">
                  {alert.top_reasons?.[0]
                    ? `${alert.top_reasons[0].label} (${(alert.top_reasons[0].confidence * 100).toFixed(0)}%)`
                    : 'N/A'}
                </td>
                <td>
                  <button
                    className="investigate-btn"
                    onClick={() => handleRowClick(alert.wallet_id)}
                  >
                    Investigate
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
