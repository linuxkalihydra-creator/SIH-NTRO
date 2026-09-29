import { asArray, fmtInt, fmtNum, fmtPct, isNum, patternLabel } from '../lib/format.js';

export default function ModelsSection({ overview }) {
  const models = asArray(overview.models);
  const evaluation = overview.evaluation || {};
  const detectors = asArray(evaluation.detectors);

  return (
    <details className="panel models-section">
      <summary>
        <h2>Models &amp; evaluation</h2>
        <span className="muted small">{models.length} model{models.length === 1 ? '' : 's'}</span>
      </summary>
      <div className="models-body">
        {models.length === 0 ? (
          <p className="muted small">No model information reported for this analysis.</p>
        ) : (
          <div className="model-grid">
            {models.map((model, i) => (
              <article key={`${model.name}-${i}`} className="model-card">
                <header>
                  <h3>{model.name || 'Model'}</h3>
                  <span className="model-flagged" title="Items flagged by this model">{fmtInt(model.flagged)} flagged</span>
                </header>
                <div className="chip-row">
                  {model.family && <span className="chip chip-small">{model.family}</span>}
                  {model.level && <span className="chip chip-small chip-muted">{model.level} level</span>}
                </div>
                {model.description && <p className="small">{model.description}</p>}
              </article>
            ))}
          </div>
        )}

        <h3 className="section-subhead">Evaluation against labels</h3>
        {evaluation.labels_available ? (
          <>
            <p className="small">
              Transaction-level AUC: <strong>{isNum(evaluation.transaction_auc) ? fmtNum(evaluation.transaction_auc, 3) : '—'}</strong>
            </p>
            {detectors.length > 0 && (
              <div className="table-wrap">
                <table className="data-table">
                  <caption className="sr-only">Detector precision and recall</caption>
                  <thead>
                    <tr>
                      <th scope="col">Label</th>
                      <th scope="col">Detector</th>
                      <th scope="col" className="num">Precision</th>
                      <th scope="col" className="num">Recall</th>
                      <th scope="col" className="num">Support</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detectors.map((d, i) => (
                      <tr key={`${d.label}-${d.detector}-${i}`}>
                        <td><code>{d.label}</code></td>
                        <td>{patternLabel(d.detector)}</td>
                        <td className="num">{fmtPct(d.precision, 1)}</td>
                        <td className="num">{fmtPct(d.recall, 1)}</td>
                        <td className="num">{fmtInt(d.support)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {evaluation.notes && <p className="muted small">{evaluation.notes}</p>}
          </>
        ) : (
          <p className="muted small">
            Ground-truth labels are not available for this dataset, so precision/recall cannot be computed.
            {evaluation.notes && <> {evaluation.notes}</>}
          </p>
        )}
      </div>
    </details>
  );
}
