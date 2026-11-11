/** Every model behind the scores, grouped by what it is for, and how well detection did when labels exist. */

import { Collapsible, Stat } from './common.jsx';
import { asArray, fmtInt, fmtNum, fmtPct, fmtSeconds, isNum, patternLabel } from '../lib/format.js';

const FAMILIES = [
  ['anomaly detection', 'Anomaly detection', 'Unsupervised detectors that learn what normal behaviour looks like; each runs on actors and on transactions.'],
  ['entity clustering', 'Entity resolution', 'From addresses to owners (entities), to operators (actors), to trading communities.'],
  ['pattern detection', 'Pattern detection', 'Searches of the transaction flow for the shapes that laundering leaves behind.'],
  ['network correlation', 'Network correlation', 'Ties each transaction to the IP that most likely created it.'],
  ['risk propagation', 'Risk propagation', 'Carries evidence along the flow of funds and across shared infrastructure.'],
  ['risk scoring', 'Risk scoring', 'Fuses the anomaly, pattern, taint and network signals into one explainable score.'],
];

function KeyFigures({ items }) {
  const rows = items.filter((item) => item && item[1] !== null && item[1] !== undefined);
  if (!rows.length) return null;
  return (
    <dl className="facts">
      {rows.map(([label, value, title]) => (
        <div key={label} title={title}><dt>{label}</dt><dd>{value}</dd></div>
      ))}
    </dl>
  );
}

/** A family's models; detectors that run at several levels get one row with a column per level. */
function ModelTable({ models, caption }) {
  const names = [...new Set(models.map((model) => model.name))];
  const levels = [...new Set(models.map((model) => model.level))];
  const pivot = names.length < models.length && levels.length > 1;
  return (
    <div className="table-wrap">
      <table className="data-table model-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Model</th>
            {!pivot && <th scope="col">Works on</th>}
            <th scope="col">What it does</th>
            {pivot
              ? levels.map((level) => <th key={level} scope="col" className="num" title="Rows scored in the top 5% by this detector">Flagged: {level}</th>)
              : <th scope="col" className="num" title="What the model found: groups, structures, attributed or affected items">Found</th>}
          </tr>
        </thead>
        <tbody>
          {pivot ? names.map((name) => {
            const rows = models.filter((model) => model.name === name);
            return (
              <tr key={name}>
                <th scope="row">{name}</th>
                <td>{rows[0].description}</td>
                {levels.map((level) => {
                  const match = rows.find((model) => model.level === level);
                  return <td key={level} className="num">{match ? fmtInt(match.flagged) : '—'}</td>;
                })}
              </tr>
            );
          }) : models.map((model, index) => (
            <tr key={`${model.name}-${index}`}>
              <th scope="row">{model.name || 'Model'}</th>
              <td className="nowrap">{model.level || '—'}</td>
              <td>{model.description || '—'}</td>
              <td className="num">{fmtInt(model.flagged)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Meter({ value }) {
  if (!isNum(value)) return '—';
  return (
    <span className="attr-bar">
      <span className="attr-track" aria-hidden="true"><span className="attr-fill" style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} /></span>
      <span className="attr-value wide">{fmtPct(value, 1)}</span>
    </span>
  );
}

function Evaluation({ evaluation }) {
  const detectors = asArray(evaluation.detectors);
  if (!evaluation.labels_available) {
    return (
      <p className="muted small">
        {evaluation.notes || 'The dataset carries no behaviour labels, so detection quality cannot be measured.'}
        {' '}Upload a dataset with a <code>behavior_type</code> column to see AUC, precision and recall here.
      </p>
    );
  }
  const lift = isNum(evaluation.transaction_auc) && isNum(evaluation.transaction_anomaly_only_auc)
    ? evaluation.transaction_auc - evaluation.transaction_anomaly_only_auc
    : null;
  return (
    <>
      <div className="stat-row">
        <Stat
          label="Fused score AUC"
          value={isNum(evaluation.transaction_auc) ? fmtNum(evaluation.transaction_auc, 3) : '—'}
          sub="ranking illicit above licit transactions"
          title="Area under the ROC curve of the transaction risk score against the labels (1.0 is perfect, 0.5 is chance)"
        />
        <Stat
          label="Anomaly-only AUC"
          value={isNum(evaluation.transaction_anomaly_only_auc) ? fmtNum(evaluation.transaction_anomaly_only_auc, 3) : '—'}
          sub={lift !== null ? `fusion adds ${lift >= 0 ? '+' : ''}${fmtNum(lift, 3)}` : 'the anomaly ensemble alone'}
          title="The same measure using only the anomaly ensemble: what pattern, taint and network evidence add on top"
        />
        <Stat
          label="Precision at HIGH"
          value={isNum(evaluation.high_risk_precision) ? fmtPct(evaluation.high_risk_precision, 1) : '—'}
          sub={`of ${fmtInt(evaluation.high_risk_transactions)} HIGH or CRITICAL transactions`}
          title="Share of HIGH or CRITICAL transactions that are labelled illicit"
        />
        <Stat
          label="Illicit recall at HIGH"
          value={isNum(evaluation.illicit_recall_at_high) ? fmtPct(evaluation.illicit_recall_at_high, 1) : '—'}
          sub="of labelled illicit transactions"
          title="Share of labelled illicit transactions that are scored HIGH or CRITICAL"
        />
      </div>
      {detectors.length > 0 && (
        <div className="table-wrap eval-table">
          <table className="data-table">
            <caption className="sr-only">Precision and recall of each pattern detector against the labels</caption>
            <thead>
              <tr>
                <th scope="col">Pattern detector</th>
                <th scope="col">Label in the data</th>
                <th scope="col" title="Share of the detector’s transactions that carry the label">Precision</th>
                <th scope="col" title="Share of labelled transactions the detector found">Recall</th>
                <th scope="col" className="num" title="Labelled transactions">Labelled</th>
              </tr>
            </thead>
            <tbody>
              {detectors.map((d, i) => (
                <tr key={`${d.label}-${d.detector}-${i}`}>
                  <th scope="row">{patternLabel(d.detector)}</th>
                  <td><code>{d.label}</code></td>
                  <td><Meter value={d.precision} /></td>
                  <td><Meter value={d.recall} /></td>
                  <td className="num">{fmtInt(d.support)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {evaluation.notes && <p className="muted small">{evaluation.notes}</p>}
    </>
  );
}

export default function ModelsSection({ overview, open, onToggle }) {
  const models = asArray(overview.models);
  const evaluation = overview.evaluation || {};
  const network = overview.network || {};
  const stats = overview.stats || {};
  const links = Object.entries(overview.entity_links || {}).sort((a, b) => b[1] - a[1]);
  const actorLinks = Object.entries(overview.actor_links || {}).sort((a, b) => b[1] - a[1]);
  const known = FAMILIES.map(([key]) => key);
  const families = [
    ...FAMILIES,
    ...[...new Set(models.map((model) => model.family).filter((family) => family && !known.includes(family)))].map((family) => [family, family, '']),
  ].map(([key, title, description]) => ({ key, title, description, models: models.filter((model) => model.family === key) }))
    .filter((family) => family.models.length > 0);
  const orphans = models.filter((model) => !model.family);
  const stages = overview.stage_seconds && typeof overview.stage_seconds === 'object'
    ? Object.entries(overview.stage_seconds).filter(([, seconds]) => isNum(seconds))
    : [];

  const hint = [
    `${models.length} model${models.length === 1 ? '' : 's'} in ${families.length} famil${families.length === 1 ? 'y' : 'ies'}`,
    evaluation.labels_available && isNum(evaluation.transaction_auc) ? `AUC ${fmtNum(evaluation.transaction_auc, 3)} against labels` : null,
    !evaluation.labels_available ? 'no labels to evaluate against' : null,
  ].filter(Boolean).join(' · ');

  const extras = {
    'entity clustering': (
      <>
        {links.length > 0 && (
          <>
            <h4 className="family-subhead">Address links made, by ownership heuristic</h4>
            <KeyFigures items={links.map(([name, count]) => [name, fmtInt(count)])} />
          </>
        )}
        {actorLinks.length > 0 && (
          <>
            <h4 className="family-subhead">Entities joined into actors, by the two clues that agreed</h4>
            <KeyFigures items={actorLinks.map(([name, count]) => [name, fmtInt(count)])} />
          </>
        )}
        <KeyFigures
          items={[
            ['Multi-address entities', isNum(stats.multi_address_entities) ? fmtInt(stats.multi_address_entities) : null],
            ['CoinJoins kept out of co-spend clustering', isNum(stats.coinjoins_excluded_from_cio) ? fmtInt(stats.coinjoins_excluded_from_cio) : null,
              'Inputs of a CoinJoin belong to different people, so they are never merged into one owner'],
            ['Actors spanning several entities', isNum(stats.multi_entity_actors) ? fmtInt(stats.multi_entity_actors) : null],
            ['Widely shared IPs ignored when linking actors', isNum(network.skipped_shared_ips) ? fmtInt(network.skipped_shared_ips) : null,
              'An IP used by many unrelated entities (a relay, VPN or public node) is not evidence of common operation'],
          ]}
        />
      </>
    ),
    'network correlation': (
      <KeyFigures
        items={[
          ['Network sightings', isNum(network.observations) ? fmtInt(network.observations) : null],
          ['Transactions seen more than once', isNum(network.multi_observed_transactions) ? fmtInt(network.multi_observed_transactions) : null],
          ['Attributed to their first-seen IP', isNum(network.attributed_transactions)
            ? `${fmtInt(network.attributed_transactions)}${isNum(network.attributed_share) ? ` (${fmtPct(network.attributed_share, 1)})` : ''}` : null],
          ['Mean origin confidence', isNum(network.mean_origin_confidence) ? fmtPct(network.mean_origin_confidence) : null],
          ['Relay nodes identified', isNum(network.relay_nodes) ? fmtInt(network.relay_nodes) : null,
            'IPs that mostly pass on transactions first seen elsewhere; what they announce first is not attributed'],
        ]}
      />
    ),
    'risk scoring': (
      <KeyFigures
        items={[
          ['CRITICAL', 'score 80 or more'],
          ['HIGH', '60 to 79'],
          ['MEDIUM', '35 to 59'],
          ['LOW', 'below 35'],
        ]}
      />
    ),
  };

  return (
    <Collapsible id="models" title="Models & evaluation" hint={hint} open={open} onToggle={onToggle}>
      {models.length === 0 ? (
        <p className="muted small">No model information was stored with this analysis.</p>
      ) : (
        <div className="model-families">
          {families.map((family) => (
            <section key={family.key} className="model-family">
              <h3>{family.title} <span className="count-pill">{family.models.length}</span></h3>
              {family.description && <p className="muted small">{family.description}</p>}
              <ModelTable models={family.models} caption={`${family.title} models`} />
              {extras[family.key]}
            </section>
          ))}
          {orphans.length > 0 && (
            <section className="model-family">
              <h3>Other models</h3>
              <ModelTable models={orphans} caption="Other models" />
            </section>
          )}
        </div>
      )}

      <section className="model-family">
        <h3>Evaluation against labels</h3>
        <Evaluation evaluation={evaluation} />
      </section>

      {stages.length > 0 && (
        <p className="muted small">
          Pipeline run: {stages.map(([stage, seconds]) => `${stage} ${fmtSeconds(seconds)}`).join(' · ')}
          {isNum(overview.processing_seconds) && <> · total {fmtSeconds(overview.processing_seconds)}</>}
        </p>
      )}
    </Collapsible>
  );
}
