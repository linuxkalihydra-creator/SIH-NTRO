import { useEffect, useMemo, useState } from 'react';
import {
  analyzeDataset,
  apiErrorMessage,
  getClusters,
  getHealth,
  getOverview,
  getPatterns,
  getTransactionAlerts,
  getWalletAlerts,
  isNotAnalysed,
  listDatasets,
  updateSeeds,
  uploadDataset,
} from './api/client.js';
import useApi from './lib/useApi.js';
import { asArray } from './lib/format.js';
import TopBar from './components/TopBar.jsx';
import SeedsDrawer from './components/SeedsDrawer.jsx';
import KpiStrip from './components/KpiStrip.jsx';
import LeadsPanel from './components/LeadsPanel.jsx';
import InvestigationPanel from './components/investigation/InvestigationPanel.jsx';
import ModelsSection from './components/ModelsSection.jsx';
import ErrorBoundary from './components/ErrorBoundary.jsx';
import { StatusBox } from './components/common.jsx';
import './App.css';

const DATASET_KEY = 'bip.datasetId';
const LEADS_LIMIT = 500;
const HISTORY_LIMIT = 20;

function readSavedDataset() {
  try {
    return localStorage.getItem(DATASET_KEY) || null;
  } catch {
    return null;
  }
}

function saveDataset(id) {
  try {
    if (id) localStorage.setItem(DATASET_KEY, id);
    else localStorage.removeItem(DATASET_KEY);
  } catch {
    // Storage can be unavailable (private mode); the choice then lives in memory only.
  }
}

/** Saved choice if still present, else the latest completed analysis, else the newest upload. */
function pickDataset(datasets, chosenId) {
  if (!datasets?.length) return null;
  if (chosenId && datasets.some((d) => d.dataset_id === chosenId)) return chosenId;
  const ts = (d) => String(d.updated_at || d.created_at || '');
  const completed = datasets
    .filter((d) => d.analysis_status === 'completed')
    .sort((a, b) => ts(b).localeCompare(ts(a)));
  return (completed[0] || datasets[0]).dataset_id;
}

const sameSelection = (a, b) => a && b && a.type === b.type && String(a.id) === String(b.id);

export default function App() {
  // ── Datasets ──────────────────────────────────────────────────────────
  const [datasets, setDatasets] = useState(null);
  const [datasetsError, setDatasetsError] = useState(null);
  const [chosenId, setChosenId] = useState(readSavedDataset);
  const datasetId = pickDataset(datasets, chosenId);
  const dataset = datasets?.find((d) => d.dataset_id === datasetId) || null;

  const refreshDatasets = async () => {
    try {
      const list = await listDatasets();
      setDatasets(asArray(list));
      setDatasetsError(null);
      return asArray(list);
    } catch (error) {
      setDatasetsError(apiErrorMessage(error, 'Loading datasets'));
      setDatasets((current) => current || []);
      return null;
    }
  };

  useEffect(() => {
    let alive = true;
    listDatasets()
      .then((list) => {
        if (!alive) return;
        setDatasets(asArray(list));
        setDatasetsError(null);
      })
      .catch((error) => {
        if (!alive) return;
        setDatasetsError(apiErrorMessage(error, 'Loading datasets'));
        setDatasets((current) => current || []);
      });
    return () => {
      alive = false;
    };
  }, []);

  const chooseDataset = (id) => {
    setChosenId(id);
    setBanner(null);
    saveDataset(id);
  };

  // ── Health (polled) ───────────────────────────────────────────────────
  const [health, setHealth] = useState({ status: 'loading' });
  useEffect(() => {
    let alive = true;
    const check = () => getHealth()
      .then((data) => alive && setHealth({ status: 'success', data }))
      .catch((error) => alive && setHealth({ status: 'error', error }));
    check();
    const timer = setInterval(check, 30000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // ── Overview + leads (refetched whenever `refresh` changes) ─────────
  const [refresh, setRefresh] = useState(0);
  const scope = datasetId ? `${datasetId}|${refresh}` : null;
  const overview = useApi(scope && `overview|${scope}`, (signal) => getOverview(datasetId, signal));
  const analysed = overview.status === 'success';
  const notAnalysed = overview.status === 'error' && isNotAnalysed(overview.error);

  const [leadsTab, setLeadsTab] = useState('wallets');
  const [openedTabs, setOpenedTabs] = useState(() => new Set(['wallets']));
  const openTab = (tab) => {
    setLeadsTab(tab);
    setOpenedTabs((current) => (current.has(tab) ? current : new Set([...current, tab])));
  };
  const leadKey = (tab) => (analysed && openedTabs.has(tab) ? `${tab}|${scope}` : null);
  const leads = {
    wallets: useApi(leadKey('wallets'), (signal) => getWalletAlerts(datasetId, { limit: LEADS_LIMIT }, signal)),
    transactions: useApi(leadKey('transactions'), (signal) => getTransactionAlerts(datasetId, { limit: LEADS_LIMIT }, signal)),
    patterns: useApi(leadKey('patterns'), (signal) => getPatterns(datasetId, {}, signal)),
    communities: useApi(leadKey('communities'), (signal) => getClusters(datasetId, signal)),
  };

  // ── Selection + history (scoped to the dataset) ─────────────────────
  const [nav, setNav] = useState({ datasetId: null, current: null, history: [] });
  const navForDataset = nav.datasetId === datasetId ? nav : { datasetId, current: null, history: [] };
  // Seeds are already known to the investigator, so open on the strongest new lead.
  const walletLeads = asArray(leads.wallets.data);
  const topWallet = walletLeads.find((alert) => !alert.is_seed) || walletLeads[0];
  const selection = navForDataset.current
    || (topWallet ? { type: 'wallet', id: String(topWallet.wallet_id) } : null);

  const select = (next) => {
    if (!next?.id && next?.id !== 0) return;
    const normalized = { ...next, id: String(next.id) };
    setNav((state) => {
      const base = state.datasetId === datasetId ? state : { datasetId, current: null, history: [] };
      const current = base.current || selection;
      if (sameSelection(current, normalized)) {
        return { ...base, current: normalized.data ? normalized : current };
      }
      const history = current ? [current, ...base.history].slice(0, HISTORY_LIMIT) : base.history;
      return { datasetId, current: normalized, history };
    });
    // Keep the investigation panel in view (it sits below the leads on narrow screens).
    requestAnimationFrame(() => {
      const head = document.getElementById('inv-title');
      if (!head) return;
      const { top } = head.getBoundingClientRect();
      if (top < 0 || top > window.innerHeight - 80) head.scrollIntoView({ block: 'start', behavior: 'smooth' });
    });
  };

  const back = (index = 0) => {
    setNav((state) => {
      if (state.datasetId !== datasetId || !state.history[index]) return state;
      return { datasetId, current: state.history[index], history: state.history.slice(index + 1) };
    });
  };

  // ── Seeds ─────────────────────────────────────────────────────────────
  const [pendingSeeds, setPendingSeeds] = useState({});
  const seeds = analysed ? asArray(overview.data?.seeds) : asArray(pendingSeeds[datasetId]);
  const [seedsOpen, setSeedsOpen] = useState(false);
  const [unknownSeeds, setUnknownSeeds] = useState([]);

  // ── Long-running actions ──────────────────────────────────────────────
  const [busy, setBusy] = useState(null); // {kind, startedAt, label}
  const [banner, setBanner] = useState(null); // {kind: 'error'|'info'|'success', text, action?}

  const runAnalyze = async (targetId = datasetId) => {
    if (!targetId || busy) return;
    const started = Date.now();
    setBusy({ kind: 'analyze', startedAt: started, label: 'Analysing dataset' });
    setBanner(null);
    try {
      const seedList = analysed && targetId === datasetId ? asArray(overview.data?.seeds) : asArray(pendingSeeds[targetId]);
      await analyzeDataset(targetId, seedList);
      const seconds = ((Date.now() - started) / 1000).toFixed(0);
      setBanner({ kind: 'success', text: `Analysis completed in ${seconds}s.` });
      await refreshDatasets();
      setRefresh((r) => r + 1);
    } catch (error) {
      setBanner({ kind: 'error', text: `Analysis failed: ${apiErrorMessage(error, 'Analysis')}` });
      refreshDatasets();
    } finally {
      setBusy(null);
    }
  };

  const applySeeds = async (nextSeeds) => {
    if (!datasetId || busy) return false;
    if (!analysed) {
      setPendingSeeds((current) => ({ ...current, [datasetId]: nextSeeds }));
      setUnknownSeeds([]);
      setBanner({ kind: 'info', text: `${nextSeeds.length} seed wallet(s) will be used when you run Analyze.` });
      return true;
    }
    setBusy({ kind: 'seeds', startedAt: Date.now(), label: 'Re-scoring with new seeds' });
    try {
      const result = await updateSeeds(datasetId, nextSeeds);
      const unknown = asArray(result?.unknown_seeds);
      setUnknownSeeds(unknown);
      setBanner({
        kind: unknown.length ? 'info' : 'success',
        text: unknown.length
          ? `Seeds updated. ${unknown.length} id(s) were not found in this dataset.`
          : `Seeds updated (${asArray(result?.overview?.seeds).length || nextSeeds.length} seeds). Scores recomputed.`,
      });
      setRefresh((r) => r + 1);
      return true;
    } catch (error) {
      setBanner({ kind: 'error', text: `Updating seeds failed: ${apiErrorMessage(error, 'Updating seeds')}` });
      return false;
    } finally {
      setBusy(null);
    }
  };

  const toggleSeed = (walletId, makeSeed) => {
    const next = makeSeed ? [...new Set([...seeds, walletId])] : seeds.filter((s) => s !== walletId);
    return applySeeds(next);
  };

  const upload = async (file) => {
    if (!file || busy) return;
    setBusy({ kind: 'upload', startedAt: Date.now(), label: `Uploading ${file.name}` });
    setBanner(null);
    try {
      const meta = await uploadDataset(file);
      await refreshDatasets();
      if (meta?.dataset_id) chooseDataset(meta.dataset_id);
      setBanner({
        kind: 'success',
        text: `Uploaded ${meta?.filename || file.name}${meta?.record_count ? ` (${meta.record_count.toLocaleString()} records)` : ''}. Run the analysis to generate leads.`,
        action: meta?.dataset_id ? { label: 'Analyze now', datasetId: meta.dataset_id } : null,
      });
    } catch (error) {
      setBanner({ kind: 'error', text: `Upload failed: ${apiErrorMessage(error, 'Upload')}` });
    } finally {
      setBusy(null);
    }
  };

  const seedSet = useMemo(() => new Set(seeds), [seeds]);

  // ── Render ────────────────────────────────────────────────────────────
  let content;
  if (datasets === null) {
    content = <StatusBox kind="loading" title="Loading datasets…" />;
  } else if (datasetsError && !datasets.length) {
    content = (
      <StatusBox kind="error" title="Cannot reach the backend" action={<button type="button" className="btn" onClick={refreshDatasets}>Retry</button>}>
        {datasetsError}
      </StatusBox>
    );
  } else if (!datasets.length) {
    content = (
      <StatusBox kind="empty" title="No datasets yet">
        Upload a transaction dataset (.csv, .json or .xml) with <strong>Upload dataset</strong> to start an investigation.
      </StatusBox>
    );
  } else if (overview.status === 'loading') {
    content = <StatusBox kind="loading" title="Loading analysis…" />;
  } else if (notAnalysed) {
    content = (
      <StatusBox
        kind="empty"
        title={`${dataset?.filename || 'This dataset'} has not been analysed yet`}
        action={(
          <div className="status-actions">
            <button type="button" className="btn" onClick={() => setSeedsOpen(true)} disabled={Boolean(busy)}>Seeds ({seeds.length})</button>
            <button type="button" className="btn btn-primary" onClick={() => runAnalyze()} disabled={Boolean(busy)}>Analyze now</button>
          </div>
        )}
      >
        Optionally add known-illicit seed wallets first, then run the analysis (typically 30–60 s).
        {dataset?.status === 'failed' && dataset?.error_message && <p className="error-text">Last attempt failed: {dataset.error_message}</p>}
      </StatusBox>
    );
  } else if (overview.status === 'error') {
    content = (
      <StatusBox kind="error" title="Could not load the overview" action={<button type="button" className="btn" onClick={overview.reload}>Retry</button>}>
        {apiErrorMessage(overview.error, 'Loading overview')}
      </StatusBox>
    );
  } else if (analysed) {
    const ov = overview.data || {};
    content = (
      <>
        <KpiStrip overview={ov} />
        <ErrorBoundary>
          <div className="workspace">
            <LeadsPanel
              tab={leadsTab}
              onTabChange={openTab}
              leads={leads}
              overview={ov}
              selection={selection}
              seedSet={seedSet}
              onSelect={select}
            />
            <InvestigationPanel
              key={datasetId}
              datasetId={datasetId}
              refresh={refresh}
              selection={selection}
              history={navForDataset.history}
              onBack={back}
              onSelect={select}
              seedSet={seedSet}
              onToggleSeed={toggleSeed}
              busy={Boolean(busy)}
              patternsLead={leads.patterns}
              communitiesLead={leads.communities}
            />
          </div>
        </ErrorBoundary>
        <ModelsSection overview={ov} />
      </>
    );
  }

  return (
    <div className="app">
      <TopBar
        datasets={datasets || []}
        datasetId={datasetId}
        onDatasetChange={chooseDataset}
        onUpload={upload}
        onAnalyze={() => runAnalyze()}
        onOpenSeeds={() => setSeedsOpen(true)}
        seedCount={seeds.length}
        busy={busy}
        health={health}
        canAnalyze={Boolean(datasetId)}
      />
      <p className="disclaimer" role="note">
        Results are investigative leads, not proof of identity, ownership or criminal activity.
      </p>
      {banner && (
        <div className={`banner banner-${banner.kind}`} role={banner.kind === 'error' ? 'alert' : 'status'}>
          <span>{banner.text}</span>
          <span className="banner-actions">
            {banner.action && (
              <button
                type="button"
                className="btn btn-small btn-primary"
                disabled={Boolean(busy)}
                onClick={() => { const target = banner.action.datasetId; setBanner(null); runAnalyze(target); }}
              >
                {banner.action.label}
              </button>
            )}
            <button type="button" className="icon-button" onClick={() => setBanner(null)} aria-label="Dismiss message">✕</button>
          </span>
        </div>
      )}
      <main className="main">{content}</main>
      {seedsOpen && (
        <SeedsDrawer
          seeds={seeds}
          analysed={analysed}
          busy={Boolean(busy)}
          unknownSeeds={unknownSeeds}
          onApply={applySeeds}
          onClose={() => setSeedsOpen(false)}
          onSelectWallet={(id) => { setSeedsOpen(false); select({ type: 'wallet', id }); }}
        />
      )}
    </div>
  );
}
