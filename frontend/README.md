# Dashboard

The investigator's workspace for the Bitcoin Investigation Platform: ranked leads on the left, one lead in
depth on the right, and the dataset's overview around them. It is a single-page React app that talks only to
the platform's own API, so it works on a machine with no internet access.

## Run it

**With the platform (no Node.js needed).** The backend serves the built dashboard from `/` when
`frontend/dist/index.html` exists (set `FRONTEND_DIST` to serve a build from somewhere else):

```bash
python -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000
```

**Build it** (Node.js 20.19+ or 22.12+):

```bash
cd frontend
npm ci
npm run build        # writes frontend/dist
npm run lint         # oxlint, must stay clean
```

**Develop it.** Start the backend on port 8000, then:

```bash
cd frontend
npm run dev          # http://localhost:5173, /api is proxied to the backend
```

## Configuration

Both settings are optional (see `.env.example`).

| Variable | Default | Meaning |
|---|---|---|
| `VITE_API_URL` | `/api` | Where the dashboard sends API requests. Set it only when the backend is on another origin. Read at build time. |
| `VITE_BACKEND_URL` | `http://127.0.0.1:8000` | Where `npm run dev` forwards `/api`. |

## What is on the page

| Area | What it shows |
|---|---|
| Top bar | Search over every wallet, transaction, IP, actor and pattern (press `/`), dataset picker, upload (`.csv .tsv .json .jsonl .ndjson .xml`, each optionally `.gz`), seeds, analyse, CSV export per lead type, delete with confirmation. An analysis runs in the background and reports its stage and percentage. |
| Overview strip | Dataset figures and a table of wallets, transactions, actors and IPs by risk level; each figure opens that list filtered to the level. |
| Activity & origin | Transactions per period next to the HIGH or CRITICAL ones (two small charts on one time axis, never two scales on one plot), and where transactions were first seen from, by country and by network. |
| Leads | Six ranked lists: wallets, transactions, actors, IPs, patterns, communities. Risk level and row limit are applied by the server; the text box filters what is loaded. |
| Investigation | One lead: why it was flagged (score split by signal, reasons, detectors, feature attributions), a link-analysis graph, and evidence tabs. Every id is a link to that lead; **Back** retraces the path. Wallets, transactions, actors and IPs have a downloadable Markdown evidence sheet. |
| Data quality | Rows read, kept, repaired and rejected, how the file's columns were understood, example problem rows. |
| Models & evaluation | Every model by family, what each found, how entities and actors were linked, and AUC, precision and recall when the dataset carries labels. |

### Link analysis

- **Wallet:** neighbourhood, *Follow the money* (downstream) or *Where it came from* (upstream), with a depth.
- **Transaction:** inputs, outputs and every IP that announced it; optionally the previous and next spends.
- **Actor, IP, pattern, community:** the transactions that tie the group together.

Colour and size carry the risk level. A thick border marks a seed, a ring a service-like wallet, a dashed ring a
wallet where a trace was stopped (a service pools everybody's funds, so following it further says nothing). An IP
that behaves as a relay node is grey with a dashed outline. Dashed edges are a transaction's first sighting,
dotted edges its later relays. Traces, transactions and patterns use a flow layout: the largest flow runs along
the top and a long chain continues on the next row. Any node opens that lead.

### Links

The address bar always holds a link to what is on screen. Nothing in it is sent to the server.

```
#dataset=<id>&tab=ips&type=wallet&id=<address>&graph=downstream&depth=2&evidence=network&open=activity,quality
```

`tab` is the lead list, `type` + `id` the lead, `graph` and `depth` its trace, `evidence` its evidence tab,
`open` the unfolded sections (`none` for all folded).

## Code map

```
src/
  api/client.js              one function per endpoint, plus download URLs
  lib/                       useApi (fetch + cancel), hash (deep links), format, hooks
  App.jsx                    state: dataset, selection + history, background analysis, sections
  components/
    TopBar.jsx               search, export menu, dataset actions, progress
    KpiStrip.jsx             overview figures, leads by risk level
    ActivitySection.jsx      timeline and origin charts (charts/Timeline, charts/RankedBars)
    LeadsPanel.jsx           the six ranked lists
    DataQuality.jsx, ModelsSection.jsx
    investigation/           WalletView, TransactionView, ActorView, IpView, GroupViews, shared parts
    graph/InvestigationGraph.jsx   Cytoscape graph and its two layouts
  index.css                  design tokens (light, and dark from the OS setting)
  App.css                    layout and components
```

## Conventions

- Plain CSS with tokens in `src/index.css`; every colour is a token, so light and dark stay in step.
  Risk colours always come with the level in words. The four signal colours and the two chart colours were
  checked for colour-vision deficiencies and contrast in both themes.
- Every chart has its figures next to it or a table view; a tooltip adds detail and never holds the only copy.
- Tables are sortable, rows and ids are reachable by keyboard, dialogs and menus close on Escape, and the
  timeline can be read with the arrow keys.
- Cytoscape is loaded on demand as its own chunk, so the first paint does not wait for it.
- No external requests: fonts are the system's, and there is no CDN, analytics or map tile server.
