import ErrorBoundary from '../ErrorBoundary.jsx';
import { StatusBox } from '../common.jsx';
import { truncateId } from '../../lib/format.js';
import WalletView from './WalletView.jsx';
import TransactionView from './TransactionView.jsx';
import { CommunityView, PatternView } from './GroupViews.jsx';

const TYPE_LABEL = { wallet: 'Wallet', transaction: 'Tx', pattern: 'Pattern', community: 'Community' };

function describe(sel) {
  if (sel.type === 'community') return sel.data?.label || `Cluster ${sel.id}`;
  return truncateId(sel.id, 6, 4);
}

export default function InvestigationPanel(props) {
  const { selection, history, onBack } = props;
  const recent = history.slice(0, 5);

  let body;
  if (!selection) {
    body = <StatusBox kind="empty" title="Nothing selected">Pick a lead on the left, or wait for the ranked wallets to load.</StatusBox>;
  } else if (selection.type === 'wallet') {
    body = <WalletView {...props} id={selection.id} />;
  } else if (selection.type === 'transaction') {
    body = <TransactionView {...props} id={selection.id} />;
  } else if (selection.type === 'pattern') {
    body = <PatternView {...props} />;
  } else {
    body = <CommunityView {...props} />;
  }

  return (
    <section className="panel inv-panel" aria-labelledby="inv-title">
      <div className="panel-head">
        <h2 id="inv-title">Investigation</h2>
        {history.length > 0 && (
          <nav className="history" aria-label="Previous selections">
            <button type="button" className="btn btn-small" onClick={() => onBack(0)} title="Back to previous selection">← Back</button>
            <ol className="history-list">
              {recent.map((sel, i) => (
                <li key={`${sel.type}-${sel.id}-${i}`}>
                  <button type="button" className="history-item" onClick={() => onBack(i)} title={`${sel.type} ${sel.id}`}>
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
