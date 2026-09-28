/**
 * Dashboard / Overview page component.
 */

import React, { useEffect, useRef, useState } from 'react';
import api, { apiErrorMessage } from '../api/client.js';
import { useDataset } from '../context/DatasetContext.jsx';
import './Dashboard.css';

function normalizeStats(stats) {
  const rawStats = stats && typeof stats === 'object' ? stats : {};
  return {
    totalTransactions: Number(rawStats.total_transactions ?? rawStats.total_records ?? 0),
    uniqueWallets: Number(rawStats.unique_wallets ?? 0),
    uniqueIps: Number(rawStats.unique_ips ?? 0),
    behaviorTypes: Array.isArray(rawStats.behavior_types)
      ? rawStats.behavior_types
      : Object.keys(rawStats.behavior_distribution || {}),
  };
}

export default function Dashboard() {
  const { datasets, selectedDatasetId, selectDataset, loadingDatasets, datasetsError, refreshDatasets } = useDataset();
  const [stats, setStats] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [clusters, setClusters] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [uploadState, setUploadState] = useState('idle');
  const [uploadError, setUploadError] = useState(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);
  const [pendingDatasetId, setPendingDatasetId] = useState(null);
  const [analysisFailed, setAnalysisFailed] = useState(false);
  const [retryToken, setRetryToken] = useState(0);
  const fileInputRef = useRef(null);

  useEffect(() => {
    const controller = new AbortController();

    if (uploadState === 'analyzing') {
      // A new upload is now active. Never show the previous investigation's results.
      setStats(null);
      setAlerts([]);
      setClusters([]);
      setError(null);
      setLoading(false);
      return () => controller.abort();
    }

    const fetchData = async () => {
      setLoading(true);
      setError(null);
      try {
        const [statsRes, alertsRes, clustersRes] = await Promise.all([
          api.stats(selectedDatasetId, { signal: controller.signal }),
          api.alerts(selectedDatasetId, { signal: controller.signal }),
          api.clusters(selectedDatasetId, { signal: controller.signal }),
        ]);

        if (controller.signal.aborted) return;
        setStats(normalizeStats(statsRes.data));
        setAlerts(Array.isArray(alertsRes.data) ? alertsRes.data : []);
        setClusters(Array.isArray(clustersRes.data) ? clustersRes.data : []);
      } catch (err) {
        if (controller.signal.aborted || err.code === 'ERR_CANCELED') return;
        console.error('Failed to fetch dashboard data:', err);
      setError('Backend unavailable. Make sure the backend is running, then retry.');
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };

    if (selectedDatasetId) fetchData(); else if (!loadingDatasets) setLoading(false);
    return () => controller.abort();
  }, [selectedDatasetId, loadingDatasets, retryToken, uploadState]);

  const selectFile = (file) => {
    if (!file) return;
    if (!['csv', 'json', 'xml'].includes(file.name.split('.').pop()?.toLowerCase())) {
      setUploadError('Unsupported file type. Please upload CSV, JSON, or XML.');
      return;
    }
    setSelectedFile(file);
    setUploadError(null);
    setUploadState('selected');
  };

  const uploadFile = async () => {
    if (!selectedFile) {
      setUploadError('Please select a dataset before uploading.');
      return;
    }
    setUploadState('uploading');
    setUploadError(null);
    setAnalysisFailed(false);
    try {
      const { data } = await api.uploadDataset(selectedFile);
      
      // Validate response contains dataset_id
      if (!data || !data.dataset_id) {
        setUploadState('failed');
        setUploadError('Dataset upload completed but no dataset ID was returned by the backend.');
        console.error('Upload response missing dataset_id:', data);
        return;
      }
      
      // Results are unavailable until the synchronous analysis request returns.
      // Do not activate this ID yet or dependent pages will correctly receive 409.
      selectDataset(null);
      setPendingDatasetId(data.dataset_id);
      console.debug('Dataset uploaded with ID:', data.dataset_id);
      setUploadOpen(false);
      setSelectedFile(null);
      await analyzeDataset(data.dataset_id);
    } catch (requestError) {
      setUploadState('failed');
      setUploadError(`Dataset upload failed. ${apiErrorMessage(requestError, 'Upload')}`);
      console.error('Upload error:', requestError);
    }
  };

  const analyzeDataset = async (datasetId = selectedDatasetId) => {
    if (!datasetId) {
      console.warn('analyzeDataset called with null/undefined dataset_id');
      return;
    }
    setUploadState('analyzing');
    setUploadError(null);
    setAnalysisFailed(false);
    try {
      console.debug('Starting analysis for dataset:', datasetId);
      await api.analyze(null, null, 0.05, 42, datasetId);
      selectDataset(datasetId);
      await refreshDatasets();
      setPendingDatasetId(null);
      setUploadState('completed');
    } catch (requestError) {
      setUploadState('failed');
      setAnalysisFailed(true);
      setUploadError(`Dataset analysis failed. ${apiErrorMessage(requestError, 'Analysis')}`);
      console.error('Analysis error for dataset', datasetId, ':', requestError);
    }
  };

  const openUpload = () => { setUploadOpen(true); setUploadError(null); };
  
  const formatFileSize = (bytes) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
  };
  
  const uploadDialog = uploadOpen && (
    <div className="upload-modal-backdrop" role="presentation">
      <section className="upload-modal" role="dialog" aria-modal="true" aria-labelledby="upload-title">
        <h2 id="upload-title">Upload Dataset</h2>
        <p>Supported formats: CSV, JSON, XML</p>
        <input ref={fileInputRef} type="file" accept=".csv,.json,.xml" onChange={(event) => selectFile(event.target.files?.[0])} />
        {selectedFile ? (
          <div className="selected-file">
            <p><strong>Selected file:</strong> {selectedFile.name}</p>
            <p><strong>File size:</strong> {formatFileSize(selectedFile.size)}</p>
            <p><strong>Format:</strong> {selectedFile.name.split('.').pop()?.toUpperCase()}</p>
          </div>
        ) : (
          <p className="selected-file">No file selected</p>
        )}
        {uploadError && <p className="upload-error">{uploadError}</p>}
        <div className="upload-actions">
          <button type="button" onClick={uploadFile} disabled={!selectedFile || uploadState === 'uploading'}>{uploadState === 'uploading' ? 'Uploading…' : 'Upload'}</button>
          <button type="button" onClick={() => { setUploadOpen(false); setSelectedFile(null); }} disabled={uploadState === 'uploading'}>Cancel</button>
        </div>
      </section>
    </div>
  );

  if (loading || loadingDatasets) {
    return (
      <div className="dashboard">
        <h1>Investigation Dashboard</h1>
        <button type="button" className="upload-dataset-button" onClick={openUpload}>Upload Dataset</button>
        <div className="loading">Loading dashboard...</div>
        {uploadDialog}
      </div>
    );
  }

  if (error) {
    return (
      <div className="dashboard">
        <h1>Investigation Dashboard</h1>
        <button type="button" className="upload-dataset-button" onClick={openUpload}>Upload Dataset</button>
        <div className="error-state">
          <h2>⚠️ Connection Error</h2>
          <p>{error}</p>
          <button type="button" onClick={() => setRetryToken((value) => value + 1)}>Retry</button>
        </div>
        {uploadDialog}
      </div>
    );
  }

  if (!stats) {
    const analysisInProgress = uploadState === 'analyzing';
    return (
      <div className="dashboard">
        <h1>Investigation Dashboard</h1>
        <button type="button" className="upload-dataset-button" onClick={openUpload}>Upload Dataset</button>
        <div className="empty-state">
          <h2>{datasetsError ? 'Backend unavailable' : analysisInProgress ? 'Analysis in progress...' : 'No dataset uploaded.'}</h2>
          <p>{datasetsError || (analysisInProgress ? 'Dataset uploaded ✓ The existing investigation pipeline is running.' : 'Upload a CSV, JSON, or XML dataset to begin the investigation.')}</p>
          {!analysisInProgress && <button type="button" onClick={datasetsError ? refreshDatasets : openUpload}>{datasetsError ? 'Retry' : 'Upload Dataset'}</button>}
          {uploadError && <p className="upload-error">{uploadError}</p>}
          {analysisFailed && <button type="button" onClick={() => analyzeDataset(pendingDatasetId)}>Retry Analysis</button>}
        </div>
        {uploadDialog}
      </div>
    );
  }

  const topAlerts = alerts.slice(0, 5);
  const criticalCount = alerts.filter((a) => a.risk_level === 'CRITICAL').length;
  const highCount = alerts.filter((a) => a.risk_level === 'HIGH').length;

  return (
    <div className="dashboard">
      <h1>Investigation Dashboard</h1>

      <section className="upload-panel" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); selectFile(event.dataTransfer.files?.[0]); setUploadOpen(true); }}>
        <div><h2>Upload Investigation Dataset</h2><p>Supported formats: CSV, JSON, XML</p></div>
        <div className="upload-actions">
          <button type="button" onClick={openUpload}>Upload Dataset</button>
        </div>
        <small>{datasets.find((dataset) => dataset.dataset_id === selectedDatasetId)?.filename ? `Dataset: ${datasets.find((dataset) => dataset.dataset_id === selectedDatasetId).filename}` : 'No dataset uploaded.'}</small>
        {uploadState === 'analyzing' && <p>Dataset uploaded ✓ Analysis running with the existing investigation pipeline…</p>}
        {uploadState === 'completed' && <p>Dataset uploaded successfully. Analysis complete ✓</p>}
        {uploadError && <p className="upload-error">{uploadError}</p>}
        {analysisFailed && <button type="button" onClick={() => analyzeDataset(pendingDatasetId)}>Retry Analysis</button>}
      </section>
      {uploadDialog}

      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-icon">📊</div>
          <div className="stat-content">
            <div className="stat-label">Total Transactions</div>
            <div className="stat-value">{stats.totalTransactions.toLocaleString()}</div>
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-icon">👛</div>
          <div className="stat-content">
            <div className="stat-label">Unique Wallets</div>
            <div className="stat-value">{stats.uniqueWallets.toLocaleString()}</div>
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-icon">🌐</div>
          <div className="stat-content">
            <div className="stat-label">Unique IPs</div>
            <div className="stat-value">{stats.uniqueIps.toLocaleString()}</div>
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-icon">⚠️</div>
          <div className="stat-content">
            <div className="stat-label">Alerts</div>
            <div className="stat-value">{alerts.length}</div>
          </div>
        </div>

        <div className="stat-card critical">
          <div className="stat-icon">🔴</div>
          <div className="stat-content">
            <div className="stat-label">Critical Entities</div>
            <div className="stat-value">{criticalCount}</div>
          </div>
        </div>

        <div className="stat-card high">
          <div className="stat-icon">🟠</div>
          <div className="stat-content">
            <div className="stat-label">High-Risk Entities</div>
            <div className="stat-value">{highCount}</div>
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-icon">🔗</div>
          <div className="stat-content">
            <div className="stat-label">Clusters</div>
            <div className="stat-value">{new Set(clusters.map((c) => c.cluster_id)).size}</div>
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-icon">🏷️</div>
          <div className="stat-content">
            <div className="stat-label">Behavior Types</div>
            <div className="stat-value">{stats.behaviorTypes.length}</div>
          </div>
        </div>
      </div>

      <div className="dashboard-section">
        <h2>🔴 Top Risk Entities</h2>
        {topAlerts.length === 0 ? (
          <div className="empty-message">No alerts found.</div>
        ) : (
          <div className="alerts-preview">
            {topAlerts.map((alert) => (
              <div key={alert.wallet_id} className="alert-preview-card">
                <div className="alert-header">
                  <div className="wallet-id-truncated">{alert.wallet_id}</div>
                  <div className={`risk-badge risk-${alert.risk_level.toLowerCase()}`}>
                    {alert.risk_level}
                  </div>
                </div>
                <div className="alert-details">
                  <div className="detail-row">
                    <span>Risk Score:</span>
                    <strong>{alert.risk_score.toFixed(2)}</strong>
                  </div>
                  <div className="detail-row">
                    <span>Confidence:</span>
                    <strong>{(alert.confidence * 100).toFixed(0)}%</strong>
                  </div>
                  <div className="detail-row">
                    <span>Cluster:</span>
                    <strong>{alert.cluster_id ?? 'N/A'}</strong>
                  </div>
                  <div className="reason">
                    {alert.top_reasons.length > 0 && (
                      <>
                        <strong>Top Reason:</strong>
                        <p>{alert.top_reasons[0].label} ({(alert.top_reasons[0].confidence * 100).toFixed(0)}% confidence)</p>
                      </>
                    )}
                  </div>
                  <a href={`/entities/${alert.wallet_id}`} className="investigate-link">
                    Investigate →
                  </a>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="dashboard-section">
        <h2>📋 Behavioral Profiles in Dataset</h2>
        <div className="profiles-list">
          {stats.behaviorTypes.map((profile) => (
            <div key={profile} className="profile-badge">
              {profile}
            </div>
          ))}
        </div>
        <p className="note">These profiles are investigative signals, not confirmed activities.</p>
      </div>
    </div>
  );
}
