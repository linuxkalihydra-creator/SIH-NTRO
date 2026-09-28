import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import api from '../api/client.js';

const DatasetContext = createContext(null);
const CURRENT_DATASET_KEY = 'currentDatasetId';

export function DatasetProvider({ children }) {
  const [datasets, setDatasets] = useState([]);
  const [selectedDatasetId, setSelectedDatasetId] = useState(null);
  const [loadingDatasets, setLoadingDatasets] = useState(true);
  const [datasetsError, setDatasetsError] = useState(null);

  const readSavedDatasetId = () => {
    try {
      const value = localStorage.getItem(CURRENT_DATASET_KEY);
      if (value && value.trim()) return value;
      if (value !== null) localStorage.removeItem(CURRENT_DATASET_KEY);
    } catch (error) {
      // Storage may be blocked by browser privacy settings; the app can still work in memory.
      console.warn('Unable to read dataset preference', error);
    }
    return null;
  };

  const writeSavedDatasetId = (datasetId) => {
    try {
      if (datasetId) localStorage.setItem(CURRENT_DATASET_KEY, datasetId);
      else localStorage.removeItem(CURRENT_DATASET_KEY);
    } catch (error) {
      console.warn('Unable to save dataset preference', error);
    }
  };

  const refreshDatasets = useCallback(async (signal) => {
    setLoadingDatasets(true);
    setDatasetsError(null);
    try {
      const { data } = await api.datasets({ signal });
      if (signal?.aborted) return;
      setDatasets(data);
      const saved = readSavedDatasetId();
      const savedDataset = data.find((item) => item.dataset_id === saved) || null;
      const savedCompleted = savedDataset?.analysis_status === 'completed' ? savedDataset : null;
      if (saved && !data.some((item) => item.dataset_id === saved)) writeSavedDatasetId(null);
      if (savedDataset && !savedCompleted) writeSavedDatasetId(null);
      // With no usable saved choice (e.g. a fresh browser), open the most recently completed analysis.
      const latestCompleted = data
        .filter((item) => item.analysis_status === 'completed')
        .sort((a, b) => String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || '')))[0] || null;
      const selected = savedCompleted || latestCompleted;
      if (selected) {
        setSelectedDatasetId(selected.dataset_id);
      } else {
        setSelectedDatasetId(null);
      }
    } catch (error) {
      if (signal?.aborted || error.code === 'ERR_CANCELED') return;
      setDatasets([]);
      setSelectedDatasetId(null);
      setDatasetsError('Failed to load dataset information.');
    } finally {
      if (!signal?.aborted) setLoadingDatasets(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    refreshDatasets(controller.signal);
    return () => controller.abort();
  }, [refreshDatasets]);

  const selectDataset = (datasetId) => {
    setSelectedDatasetId(datasetId || null);
    writeSavedDatasetId(datasetId);
  };
  return <DatasetContext.Provider value={{ datasets, selectedDatasetId, selectDataset, loadingDatasets, datasetsError, refreshDatasets }}>{children}</DatasetContext.Provider>;
}

export const useDataset = () => useContext(DatasetContext);
