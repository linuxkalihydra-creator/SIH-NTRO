import ErrorBoundary from '../ErrorBoundary.jsx';
import { StatusBox } from '../common.jsx';
import { truncateId } from '../../lib/format.js';
import WalletView from './WalletView.jsx';
import TransactionView from './TransactionView.jsx';
import ActorView from './ActorView.jsx';
import IpView from './IpView.jsx';
import { CommunityView, PatternView } from './GroupViews.jsx';

const TYPE_LABEL = { wallet: 'Wallet', transaction: 'Tx', actor: 'Actor', ip: 'IP', pattern: 'Pattern', community: 'Community' };

function describe(sel) {
  if (sel.type === 'community') return Number(sel.id) === -1 ? 'unassigned' : sel.id;
  if (sel.type === 'ip' || sel.type === 'actor') return truncateId(sel.id, 16, 6);
  return truncateId(sel.id, 6, 4);
}

/** The right-hand panel: one lead in depth, with a trail of the leads visited before it. */
export default function InvestigationPanel(props) {
  const { selection, history, onBack, leadsLoading } = props;
  const recent = history.slice(0, 5);
  const view = selection?.view || {};

  let body;
  if (!selection) {
    body = leadsLoading
      ? <StatusBox kind="loading" title="Loading the strongest lead…" />
      : <StatusBox kind="empty" title="Nothing selected">Pick a lead on the left, or search for a wallet, transaction, IP or actor in the top bar.</StatusBox>;
  } else if (selection.type === 'wallet') {
    body = <WalletView {...props} view={view} id={selection.id} />;
  } else if (selection.type === 'transaction') {
    body = <TransactionView {...props} view={view} id={selection.id} />;
  } else if (selection.type === 'actor') {
    body = <ActorView {...props} view={view} id={selection.id} />;
  } else if (selection.type === 'ip') {
    body = <IpView {...props} view={view} id={selection.id} />;
  } else if (selection.type === 'pattern') {
    body = <PatternView {...props} view={view} />;
  } else {
    body = <CommunityView {...props} view={view} />;
  }

  return (
    <section className="panel inv-panel" aria-labelledby="inv-title">
      <div className="panel-head">
        <h2 id="inv-title">Investigation</h2>
        {history.length > 0 && (
          <nav className="history" aria-label="Previous selections">
            <button type="button" className="btn btn-small" onClick={() => onBack(0)} title="Back to the previous lead">← Back</button>
            <ol className="history-list">
              {recent.map((sel, i) => (
                <li key={`${sel.type}-${sel.id}-${i}`}>
                  <button type="button" className="history-item" onClick={() => onBack(i)} title={`${TYPE_LABEL[sel.type] || sel.type} ${sel.id}`}>
                    <span className="muted">{TYPE_LABEL[sel.type] || sel.type}</span> {describe(sel)}
                  </button>
                </li>
              ))}
            </ol>
          </nav>
        )}
      </div>
      <ErrorBoundary key={selection ? `${selection.type}:${selection.id}` : 'none'} compact>
        <div className="inv-body">{body}</div>
      </ErrorBoundary>
    </section>
  );
}
