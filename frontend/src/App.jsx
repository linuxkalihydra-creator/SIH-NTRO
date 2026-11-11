import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  DATASET_ACCEPT,
  apiErrorMessage,
  deleteDataset,
  getActorAlerts,
  getClusters,
  getHealth,
  getIpAlerts,
  getOverview,
  getPatterns,
  getProgress,
  getTransactionAlerts,
  getWalletAlerts,
  isNotAnalysed,
  listDatasets,
  startAnalysis,
  updateSeeds,
  uploadDataset,
} from './api/client.js';
import useApi from './lib/useApi.js';
import { asArray, fmtBytes, fmtSeconds, plural, setStandardPorts } from './lib/format.js';
import { DEFAULT_SECTIONS, SECTIONS, readHash, writeHash } from './lib/hash.js';
import TopBar from './components/TopBar.jsx';
import SeedsDrawer from './components/SeedsDrawer.jsx';
import KpiStrip from './components/KpiStrip.jsx';
import ActivitySection from './components/ActivitySection.jsx';
import DataQuality from './components/DataQuality.jsx';
import LeadsPanel from './components/LeadsPanel.jsx';
import InvestigationPanel from './components/investigation/InvestigationPanel.jsx';
import ModelsSection from './components/ModelsSection.jsx';
import AnalysisProgress from './components/AnalysisProgress.jsx';
import ErrorBoundary from './components/ErrorBoundary.jsx';
import { ConfirmDialog, StatusBox } from './components/common.jsx';
import './App.css';

const DATASET_KEY = 'bip.datasetId';
const SECTIONS_KEY = 'bip.sections';
const HISTORY_LIMIT = 20;
const POLL_MS = 700;
const DEFAULT_QUERY = {
  wallets: { level: '', limit: 500 },
  transactions: { level: '', limit: 500 },
  actors: { level: '', limit: 300 },
  ips: { level: '', limit: 300, relays: false },
};
// Ids of these lead types survive a new analysis; actor, pattern and community numbers do not.
const STABLE_TYPES = ['wallet', 'transaction', 'ip'];

function readStored(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null; // storage can be unavailable (private mode); the choice then lives in memory only
  }
}

function store(key, value) {
  try {
    if (value === null || value === undefined) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // see readStored
  }
}

function readStoredSections() {
  const text = readStored(SECTIONS_KEY);
  if (text === null) return null;
  return text.split(',').filter((name) => SECTIONS.includes(name));
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

const sameSelection = (a, b) => Boolean(a && b && a.type === b.type && String(a.id) === String(b.id));

/** The navigation state as it applies to `datasetId` (a link without a dataset applies to whichever is shown). */
function scopeNav(state, datasetId) {
  if (state.datasetId === datasetId) return state;
  if (state.datasetId === null && state.current) return { ...state, datasetId };
  return { datasetId, current: null, history: [] };
}

function selectionFromHash(parsed) {
  if (!parsed.selection) return null;
  const view = {};
  if (parsed.graph) view.graph = parsed.graph;
  if (parsed.depth) view.depth = parsed.depth;
  if (parsed.evidence) view.evidence = parsed.evidence;
  return { ...parsed.selection, view };
}

export default function App() {
  const [initial] = useState(readHash);

  // ── Datasets ──────────────────────────────────────────────────────────
  const [datasets, setDatasets] = useState(null);
  const [datasetsError, setDatasetsError] = useState(null);
  const [chosenId, setChosenId] = useState(() => initial.dataset || readStored(DATASET_KEY));
  const datasetId = pickDataset(datasets, chosenId);
  const dataset = datasets?.find((d) => d.dataset_id === datasetId) || null;

  const refreshDatasets = useCallback(async () => {
    try {
      const list = asArray(await listDatasets());
      setDatasets(list);
      setDatasetsError(null);
      return list;
    } catch (error) {
      setDatasetsError(apiErrorMessage(error, 'Loading datasets'));
      setDatasets((current) => current || []);
      return null;
    }
  }, []);

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
  // While a re-score or a new analysis reloads the page, the previous figures stay on screen.
  const overview = useApi(scope && `overview|${scope}`, (signal) => getOverview(datasetId, signal), { keepWithin: `overview|${datasetId}` });
  const analysed = overview.status === 'success';
  // Port highlighting follows the backend's configured set of standard ports.
  useEffect(() => setStandardPorts(overview.data?.network?.standard_ports), [overview.data]);
  const notAnalysed = overview.status === 'error' && isNotAnalysed(overview.error);

  const [leadsTab, setLeadsTab] = useState(initial.tab || 'wallets');
  const [openedTabs, setOpenedTabs] = useState(() => new Set(['wallets', initial.tab || 'wallets']));
  const openTab = useCallback((tab) => {
    setLeadsTab(tab);
    setOpenedTabs((current) => (current.has(tab) ? current : new Set([...current, tab])));
  }, []);
  // Risk level and row limit of the ranked lists are applied by the server, so they are part of the request.
  const [leadQuery, setLeadQuery] = useState(DEFAULT_QUERY);
  const changeQuery = useCallback((tab, patch) => {
    setLeadQuery((current) => ({ ...current, [tab]: { ...current[tab], ...patch } }));
  }, []);
  const leadKey = (tab, variant = '') => (analysed && openedTabs.has(tab) ? `${tab}|${scope}|${variant}` : null);
  const variant = (tab) => Object.values(leadQuery[tab]).join('|');
  const leadArgs = (tab) => ({ limit: leadQuery[tab].limit, level: leadQuery[tab].level || undefined });
  const leads = {
    wallets: useApi(leadKey('wallets', variant('wallets')), (signal) => getWalletAlerts(datasetId, leadArgs('wallets'), signal), { keepWithin: `wallets|${datasetId}` }),
    transactions: useApi(leadKey('transactions', variant('transactions')), (signal) => getTransactionAlerts(datasetId, leadArgs('transactions'), signal), { keepWithin: `transactions|${datasetId}` }),
    actors: useApi(leadKey('actors', variant('actors')), (signal) => getActorAlerts(datasetId, leadArgs('actors'), signal), { keepWithin: `actors|${datasetId}` }),
    ips: useApi(leadKey('ips', variant('ips')), (signal) => getIpAlerts(datasetId, { ...leadArgs('ips'), includeRelays: leadQuery.ips.relays }, signal), { keepWithin: `ips|${datasetId}` }),
    patterns: useApi(leadKey('patterns'), (signal) => getPatterns(datasetId, {}, signal)),
    communities: useApi(leadKey('communities'), (signal) => getClusters(datasetId, signal)),
  };

  // ── Selection + history (scoped to the dataset) ─────────────────────
  const [nav, setNav] = useState(() => ({ datasetId: initial.dataset, current: selectionFromHash(initial), history: [] }));
  const navForDataset = scopeNav(nav, datasetId);
  const selection = navForDataset.current;

  // Nothing selected yet: open the strongest lead that is not already known to the investigator (a seed).
  const walletLeads = leadQuery.wallets.level ? null : leads.wallets.data;
  const topWalletId = useMemo(() => {
    const list = asArray(walletLeads);
    const top = list.find((alert) => !alert.is_seed) || list[0];
    return top ? String(top.wallet_id) : null;
  }, [walletLeads]);
  if (datasetId && topWalletId && !selection) {
    // Derived state, adjusted while rendering: React re-renders at once with the lead selected.
    setNav({ datasetId, current: { type: 'wallet', id: topWalletId }, history: navForDataset.history });
  }

  const select = useCallback((next) => {
    if (!next || next.id === undefined || next.id === null || next.id === '') return;
    const normalized = { type: next.type, id: String(next.id), ...(next.data ? { data: next.data } : {}) };
    setNav((state) => {
      const base = scopeNav(state, datasetId);
      const current = base.current;
      if (sameSelection(current, normalized)) {
        return normalized.data ? { ...base, current: { ...current, data: normalized.data } } : base;
      }
      const history = current ? [current, ...base.history].slice(0, HISTORY_LIMIT) : base.history;
      return { datasetId, current: normalized, history };
    });
    // Keep the investigation panel in view (it sits below the leads on narrow screens).
    requestAnimationFrame(() => {
      const head = document.getElementById('inv-title');
      if (!head) return;
      const bar = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--topbar-h')) || 48;
      const { top } = head.getBoundingClientRect();
      if (top < bar || top > window.innerHeight - 120) {
        window.scrollTo({ top: window.scrollY + top - bar - 12, behavior: 'smooth' });
      }
    });
  }, [datasetId]);

  const back = useCallback((index = 0) => {
    setNav((state) => {
      if (state.datasetId !== datasetId || !state.history[index]) return state;
      return { datasetId, current: state.history[index], history: state.history.slice(index + 1) };
    });
  }, [datasetId]);

  /** Graph mode, depth and evidence tab of the current lead (kept with it, so Back restores them). */
  const changeView = useCallback((patch) => {
    setNav((state) => {
      const base = scopeNav(state, datasetId);
      if (!base.current) return state;
      return { ...base, current: { ...base.current, view: { ...base.current.view, ...patch } } };
    });
  }, [datasetId]);

  // ── Foldable sections ─────────────────────────────────────────────────
  const [openSections, setOpenSections] = useState(() => initial.open ?? readStoredSections() ?? DEFAULT_SECTIONS);
  const toggleSection = useCallback((name, isOpen) => {
    setOpenSections((current) => {
      const next = SECTIONS.filter((section) => (section === name ? isOpen : current.includes(section)));
      store(SECTIONS_KEY, next.join(','));
      return next;
    });
  }, []);

  // ── The link in the address bar mirrors the workspace ─────────────────
  useEffect(() => {
    if (!datasetId) return;
    writeHash({
      dataset: datasetId,
      tab: leadsTab,
      selection,
      graph: selection?.view?.graph,
      depth: selection?.view?.depth,
      evidence: selection?.view?.evidence,
      open: openSections,
    });
  }, [datasetId, leadsTab, selection, openSections]);

  useEffect(() => {
    const onHashChange = () => {
      const next = readHash();
      if (next.dataset) setChosenId(next.dataset);
      if (next.tab) openTab(next.tab);
      if (next.open) setOpenSections(next.open);
      const target = selectionFromHash(next);
      if (!target) return;
      setNav((state) => {
        const sameDataset = !next.dataset || next.dataset === state.datasetId;
        const history = sameDataset && state.current && !sameSelection(state.current, target)
          ? [state.current, ...state.history].slice(0, HISTORY_LIMIT)
          : sameDataset ? state.history : [];
        return { datasetId: next.dataset || state.datasetId, current: target, history };
      });
    };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, [openTab]);

  useEffect(() => {
    document.title = dataset?.filename ? `${dataset.filename} · Bitcoin Investigation Platform` : 'Bitcoin Investigation Platform';
  }, [dataset?.filename]);

  // ── Seeds ─────────────────────────────────────────────────────────────
  const [pendingSeeds, setPendingSeeds] = useState({});
  const seeds = useMemo(
    () => (analysed ? asArray(overview.data?.seeds) : asArray(pendingSeeds[datasetId])),
    [analysed, overview.data, pendingSeeds, datasetId],
  );
  const seedSet = useMemo(() => new Set(seeds), [seeds]);
  const [seedsOpen, setSeedsOpen] = useState(false);
  const [unknownSeeds, setUnknownSeeds] = useState([]);

  // ── Short blocking actions and the banner ─────────────────────────────
  const [busy, setBusy] = useState(null); // {kind: 'upload'|'seeds'|'delete', label}
  const [banner, setBanner] = useState(null); // {kind: 'error'|'info'|'success', text, action?}

  const chooseDataset = useCallback((id) => {
    setChosenId(id);
    setBanner(null);
    setUnknownSeeds([]);
    setLeadQuery(DEFAULT_QUERY);
    store(DATASET_KEY, id);
  }, []);

  // ── Analysis: started in the background, followed by polling its progress ──
  const [job, setJob] = useState(null); // {datasetId, name, stage, fraction, elapsed}
  const jobId = job?.datasetId || null;
  const jobRef = useRef(null);
  const finishRef = useRef(null);
  useEffect(() => {
    jobRef.current = job;
    finishRef.current = (finishedId, progress) => {
      const name = jobRef.current?.name || finishedId;
      setJob(null);
      refreshDatasets();
      if (progress.status === 'completed') {
        const took = progress.elapsed_seconds > 0 ? ` in ${fmtSeconds(progress.elapsed_seconds)}` : '';
        if (finishedId === datasetId) {
          setRefresh((value) => value + 1);
          // Actor, pattern and community numbers are reassigned by a new analysis.
          setNav((state) => (state.datasetId === finishedId && state.current && !STABLE_TYPES.includes(state.current.type)
            ? { datasetId: finishedId, current: null, history: [] }
            : { ...state, history: state.history.filter((item) => STABLE_TYPES.includes(item.type)) }));
          setBanner({ kind: 'success', text: `Analysis of ${name} completed${took}.` });
        } else {
          setBanner({ kind: 'success', text: `Analysis of ${name} completed${took}.`, action: { label: 'Open it', open: finishedId } });
        }
      } else if (progress.status === 'failed') {
        setBanner({ kind: 'error', text: `Analysis of ${name} failed: ${progress.error || 'no reason was reported'}` });
      } else if (progress.status === 'unreachable') {
        setBanner({ kind: 'error', text: `Lost contact with the backend while ${name} was being analysed. ${progress.error || ''}`.trim() });
      } else {
        setBanner({ kind: 'info', text: `The analysis of ${name} is no longer running (the server was restarted). Run it again.` });
      }
    };
  });

  useEffect(() => {
    if (!jobId) return undefined;
    let alive = true;
    let timer = 0;
    let failures = 0;
    const tick = async () => {
      try {
        const progress = await getProgress(jobId);
        if (!alive) return;
        failures = 0;
        if (progress.status === 'running') {
          setJob((current) => (current && current.datasetId === jobId
            ? { ...current, stage: progress.stage, fraction: progress.fraction, elapsed: progress.elapsed_seconds }
            : current));
          timer = setTimeout(tick, POLL_MS);
        } else {
          finishRef.current(jobId, progress);
        }
      } catch (error) {
        if (!alive) return;
        failures += 1;
        if (failures >= 6) finishRef.current(jobId, { status: 'unreachable', error: apiErrorMessage(error, 'Checking progress') });
        else timer = setTimeout(tick, POLL_MS * 2);
      }
    };
    timer = setTimeout(tick, 200);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [jobId]);

  // A dataset that is already being analysed (started elsewhere, or before a page reload): follow it.
  const followedRef = useRef(new Set());
  useEffect(() => {
    if (job || !dataset || dataset.analysis_status !== 'running' || followedRef.current.has(dataset.dataset_id)) return;
    followedRef.current.add(dataset.dataset_id);
    setJob({ datasetId: dataset.dataset_id, name: dataset.filename || dataset.dataset_id, stage: 'Analysing', fraction: 0, elapsed: 0 });
  }, [dataset, job]);

  const runAnalyze = async (targetId = datasetId) => {
    if (!targetId || busy || job) return;
    const target = datasets?.find((d) => d.dataset_id === targetId);
    const seedList = analysed && targetId === datasetId ? asArray(overview.data?.seeds) : asArray(pendingSeeds[targetId]);
    setBanner(null);
    followedRef.current.add(targetId);
    setJob({ datasetId: targetId, name: target?.filename || targetId, stage: 'Starting', fraction: 0, elapsed: 0 });
    try {
      await startAnalysis(targetId, seedList);
      refreshDatasets();
    } catch (error) {
      if (error?.response?.status === 409) return; // already running: the poll above follows it
      setJob(null);
      setBanner({ kind: 'error', text: `The analysis could not start: ${apiErrorMessage(error, 'Analysis')}` });
    }
  };

  const applySeeds = async (nextSeeds) => {
    if (!datasetId || busy) return false;
    if (!analysed) {
      setPendingSeeds((current) => ({ ...current, [datasetId]: nextSeeds }));
      setUnknownSeeds([]);
      setBanner({ kind: 'info', text: `${plural(nextSeeds.length, 'seed wallet')} will be used when you run Analyze.` });
      return true;
    }
    if (job?.datasetId === datasetId) {
      setBanner({ kind: 'info', text: 'This dataset is being analysed. Change the seeds when the analysis has finished.' });
      return false;
    }
    setBusy({ kind: 'seeds', label: 'Re-scoring with the new seeds…' });
    try {
      const result = await updateSeeds(datasetId, nextSeeds);
      const unknown = asArray(result?.unknown_seeds);
      setUnknownSeeds(unknown);
      const kept = asArray(result?.overview?.seeds).length;
      setBanner({
        kind: unknown.length ? 'info' : 'success',
        text: unknown.length
          ? `Seeds updated: ${plural(kept, 'seed')} in use, ${unknown.length} id(s) are not in this dataset and were ignored. Scores recomputed.`
          : `Seeds updated (${plural(kept || nextSeeds.length, 'seed')}). Taint was traced again and every score recomputed.`,
      });
      setRefresh((value) => value + 1);
      return true;
    } catch (error) {
      setBanner({ kind: 'error', text: `Updating the seeds failed: ${apiErrorMessage(error, 'Updating seeds')}` });
      return false;
    } finally {
      setBusy(null);
    }
  };

  const toggleSeed = (walletId, makeSeed) => {
    const next = makeSeed ? [...new Set([...seeds, walletId])] : seeds.filter((seed) => seed !== walletId);
    return applySeeds(next);
  };

  const fileRef = useRef(null);
  const upload = async (file) => {
    if (!file || busy) return;
    setBusy({ kind: 'upload', label: `Uploading ${file.name}…` });
    setBanner(null);
    try {
      const meta = await uploadDataset(file);
      await refreshDatasets();
      if (meta?.dataset_id) chooseDataset(meta.dataset_id);
      const size = fmtBytes(meta?.size_bytes);
      setBanner({
        kind: 'success',
        text: `Uploaded ${meta?.filename || file.name}${size ? ` (${size})` : ''}. Add seed wallets if you have any, then run the analysis.`,
        action: meta?.dataset_id ? { label: 'Analyze now', analyze: meta.dataset_id } : null,
      });
    } catch (error) {
      setBanner({ kind: 'error', text: `Upload failed: ${apiErrorMessage(error, 'Upload')}` });
    } finally {
      setBusy(null);
    }
  };

  const [confirmDelete, setConfirmDelete] = useState(null);
  const removeDataset = async () => {
    const target = confirmDelete;
    if (!target || busy) return;
    setBusy({ kind: 'delete', label: `Deleting ${target.filename || target.dataset_id}…` });
    try {
      await deleteDataset(target.dataset_id);
      if (chosenId === target.dataset_id) {
        setChosenId(null);
        store(DATASET_KEY, null);
      }
      await refreshDatasets();
      setBanner({ kind: 'success', text: `Deleted ${target.filename || target.dataset_id} with its stored analysis and exports.` });
    } catch (error) {
      setBanner({ kind: 'error', text: `Delete failed: ${apiErrorMessage(error, 'Delete')}` });
    } finally {
      setConfirmDelete(null);
      setBusy(null);
    }
  };

  /** From the KPI strip: open a ranked list, optionally narrowed to one risk level. */
  const pickLeads = useCallback((tab, level) => {
    openTab(tab);
    changeQuery(tab, { level });
    requestAnimationFrame(() => document.getElementById('leads-title')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
  }, [openTab, changeQuery]);

  const runBannerAction = () => {
    const action = banner?.action;
    setBanner(null);
    if (action?.analyze) runAnalyze(action.analyze);
    else if (action?.open) chooseDataset(action.open);
  };

  // ── Render ────────────────────────────────────────────────────────────
  const analysingThis = Boolean(job && job.datasetId === datasetId);
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
      <StatusBox
        kind="empty"
        title="No datasets yet"
        action={<button type="button" className="btn btn-primary" onClick={() => fileRef.current?.click()} disabled={Boolean(busy)}>Upload dataset</button>}
      >
        Upload transaction and network metadata as CSV, TSV, JSON, JSON Lines or XML (each may be gzip-compressed).
        One row per sighting of a transaction; a transaction seen from several IPs may appear on several rows.
      </StatusBox>
    );
  } else if (overview.status === 'loading') {
    content = <StatusBox kind="loading" title="Loading the analysis…" />;
  } else if (analysingThis && !analysed) {
    content = (
      <div className="status-box status-progress">
        <AnalysisProgress job={job} />
        <p className="muted small">You can keep working with other datasets; this page fills in when the analysis is done.</p>
      </div>
    );
  } else if (notAnalysed) {
    const detail = apiErrorMessage(overview.error);
    const outdated = /outdated/i.test(detail);
    const failed = dataset?.analysis_status === 'failed';
    const name = dataset?.filename || 'This dataset';
    content = (
      <StatusBox
        kind="empty"
        title={outdated ? `${name} must be analysed again` : failed ? `The analysis of ${name} failed` : `${name} has not been analysed yet`}
        action={(
          <div className="status-actions">
            <button type="button" className="btn" onClick={() => setSeedsOpen(true)} disabled={Boolean(busy)}>Seeds ({seeds.length})</button>
            <button type="button" className="btn btn-primary" onClick={() => runAnalyze()} disabled={Boolean(busy) || Boolean(job)}>Analyze now</button>
          </div>
        )}
      >
        {failed && !outdated && (
          <p className="error-text">{dataset?.error_message || 'No reason was reported.'}</p>
        )}
        {outdated
          ? detail
          : failed
            ? 'Correct the file and upload it again, or run the analysis once more.'
            : 'Optionally add known illicit seed wallets first, then run the analysis. It runs in the background and reports its progress.'}
      </StatusBox>
    );
  } else if (overview.status === 'error') {
    content = (
      <StatusBox kind="error" title="Could not load the analysis" action={<button type="button" className="btn" onClick={overview.reload}>Retry</button>}>
        {apiErrorMessage(overview.error, 'Loading the overview')}
      </StatusBox>
    );
  } else if (analysed) {
    const ov = overview.data || {};
    content = (
      <>
        <KpiStrip overview={ov} onPick={pickLeads} />
        <ErrorBoundary compact>
          <ActivitySection overview={ov} open={openSections.includes('activity')} onToggle={(value) => toggleSection('activity', value)} />
        </ErrorBoundary>
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
              query={leadQuery}
              onQueryChange={changeQuery}
            />
            <InvestigationPanel
              key={datasetId}
              datasetId={datasetId}
              refresh={refresh}
              selection={selection}
              history={navForDataset.history}
              onBack={back}
              onSelect={select}
              onViewChange={changeView}
              seedSet={seedSet}
              onToggleSeed={toggleSeed}
              busy={Boolean(busy) || analysingThis}
              patternsLead={leads.patterns}
              communitiesLead={leads.communities}
              leadsLoading={leads.wallets.status === 'loading'}
            />
          </div>
        </ErrorBoundary>
        <ErrorBoundary compact>
          <DataQuality overview={ov} open={openSections.includes('quality')} onToggle={(value) => toggleSection('quality', value)} />
        </ErrorBoundary>
        <ErrorBoundary compact>
          <ModelsSection overview={ov} open={openSections.includes('models')} onToggle={(value) => toggleSection('models', value)} />
        </ErrorBoundary>
      </>
    );
  }

  return (
    <div className="app">
      <TopBar
        datasets={datasets || []}
        datasetId={datasetId}
        onDatasetChange={chooseDataset}
        onUploadClick={() => fileRef.current?.click()}
        onAnalyze={() => runAnalyze()}
        onOpenSeeds={() => setSeedsOpen(true)}
        onDelete={() => dataset && setConfirmDelete(dataset)}
        onSelect={select}
        seedCount={seeds.length}
        busy={busy}
        job={analysingThis && !analysed ? null : job}
        analysing={Boolean(job)}
        analysingThis={analysingThis}
        health={health}
        analysed={analysed}
      />
      <input
        ref={fileRef}
        type="file"
        accept={DATASET_ACCEPT}
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) upload(file);
        }}
      />
      <p className="disclaimer" role="note">
        Results are investigative leads, not proof of identity, ownership or criminal activity.
      </p>
      {banner && (
        <div className={`banner banner-${banner.kind}`} role={banner.kind === 'error' ? 'alert' : 'status'}>
          <span>{banner.text}</span>
          <span className="banner-actions">
            {banner.action && (
              <button type="button" className="btn btn-small btn-primary" disabled={Boolean(busy) || (banner.action.analyze && Boolean(job))} onClick={runBannerAction}>
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
          busy={Boolean(busy) || analysingThis}
          unknownSeeds={unknownSeeds}
          onApply={applySeeds}
          onClose={() => setSeedsOpen(false)}
          onSelectWallet={(id) => { setSeedsOpen(false); select({ type: 'wallet', id }); }}
        />
      )}
      {confirmDelete && (
        <ConfirmDialog
          title={`Delete ${confirmDelete.filename || confirmDelete.dataset_id}?`}
          confirmLabel="Delete dataset"
          busy={busy?.kind === 'delete'}
          onConfirm={removeDataset}
          onCancel={() => { if (!busy) setConfirmDelete(null); }}
        >
          <p>
            The uploaded file, its stored analysis, the exported leads and evidence sheets are removed
            {health.data?.graph_available ? ', together with its graph in Neo4j' : ''}. This cannot be undone.
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}
