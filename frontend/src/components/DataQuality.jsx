/** What ingestion did with the uploaded rows: kept, repaired, rejected, and how columns were understood. */

import { Collapsible, Stat } from './common.jsx';
import { asArray, fmtInt, fmtPct, fmtTime, isNum } from '../lib/format.js';

function CountTable({ caption, entries, nameHeader, countHeader, total }) {
  return (
    <div className="table-wrap">
      <table className="data-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">{nameHeader}</th>
            <th scope="col" className="num">{countHeader}</th>
            {isNum(total) && total > 0 && <th scope="col" className="num">Share</th>}
          </tr>
        </thead>
        <tbody>
          {entries.map(([name, count]) => (
            <tr key={name}>
              <td>{name}</td>
              <td className="num">{fmtInt(count)}</td>
              {isNum(total) && total > 0 && <td className="num">{count / total < 0.001 && count > 0 ? '<0.1%' : fmtPct(count / total, 1)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function DataQuality({ overview, open, onToggle }) {
  const report = overview.ingestion && typeof overview.ingestion === 'object' ? overview.ingestion : {};
  const stored = Object.keys(report).length > 0;
  const rows = report.rows ?? report.total_records;
  const kept = report.transactions ?? report.valid_records;
  const byCount = (a, b) => b[1] - a[1];
  const rejected = Object.entries(report.rejected || {}).sort(byCount);
  const warnings = Object.entries(report.warnings || {}).sort(byCount);
  const missing = Object.entries(report.missing_fields || {}).sort(byCount);
  const mapping = Object.entries(report.column_mapping || {});
  const unmapped = asArray(report.unmapped_columns);
  const examples = asArray(report.examples);
  const geo = Object.entries(overview.geo_sources || {}).sort(byCount);
  const geoTotal = geo.reduce((acc, [, count]) => acc + count, 0);
  const noted = warnings.reduce((acc, [, count]) => acc + count, 0);
  const range = asArray(report.time_range);
  const renamed = mapping.filter(([source, canonical]) => source !== canonical);

  const hint = stored
    ? [
      `${fmtInt(kept)} transactions from ${fmtInt(rows)} rows`,
      `${fmtInt(report.invalid_records || 0)} rejected`,
      noted ? `${fmtInt(noted)} repaired or noted` : 'nothing to repair',
    ].join(' · ')
    : 'no ingestion report stored';

  return (
    <Collapsible id="quality" title="Data quality" hint={hint} open={open} onToggle={onToggle}>
      {!stored ? (
        <p className="muted small">
          This analysis was stored without an ingestion report. Run <strong>Analyze</strong> again to see how the rows were read, repaired and rejected.
        </p>
      ) : (
        <>
          <div className="stat-row">
            <Stat label="Rows read" value={fmtInt(rows)} sub={report.format ? `format: ${report.format}` : null} />
            <Stat label="Transactions kept" value={fmtInt(kept)} sub="one per distinct txid" />
            <Stat
              label="Network sightings"
              value={fmtInt(report.observations)}
              sub={`${fmtInt(report.multi_observation_transactions || 0)} transactions seen more than once`}
              title="Every row is a sighting; rows that repeat a txid from another IP or time are kept as further sightings of the same transaction"
            />
            <Stat
              label="Rejected rows"
              value={fmtInt(report.invalid_records || 0)}
              tone={report.invalid_records > 0 ? 'alert' : undefined}
              sub={isNum(rows) && rows > 0 ? `${fmtPct((report.invalid_records || 0) / rows, 1)} of rows` : null}
            />
            <Stat label="Exact duplicates" value={fmtInt(report.duplicates || 0)} sub="identical rows, counted once" />
            <Stat
              label="Conflicting rows"
              value={fmtInt(report.conflicting_rows || 0)}
              tone={report.conflicting_rows > 0 ? 'warn' : undefined}
              sub="same txid, different content"
            />
          </div>

          <dl className="facts">
            <div><dt>Amounts read as</dt><dd>{report.amount_unit || '—'}</dd></div>
            <div>
              <dt>Period covered (UTC)</dt>
              <dd>{range.length === 2 ? `${fmtTime(range[0])} → ${fmtTime(range[1])}` : '—'}</dd>
            </div>
            <div>
              <dt>Country and network from</dt>
              <dd>
                {geo.length
                  ? geo.map(([source, count]) => `${source} ${geoTotal ? fmtPct(count / geoTotal) : ''}`.trim()).join(' · ')
                  : '—'}
              </dd>
            </div>
          </dl>

          <div className="quality-grid">
            <section>
              <h3 className="section-subhead">Rejected rows</h3>
              {rejected.length ? (
                <CountTable caption="Why rows were rejected" entries={rejected} nameHeader="Reason" countHeader="Rows" total={rows} />
              ) : <p className="muted small">No row was rejected.</p>}
            </section>
            <section>
              <h3 className="section-subhead">Repaired or noted <span className="muted">(rows kept)</span></h3>
              {warnings.length ? (
                <CountTable caption="Repairs made while reading" entries={warnings} nameHeader="What was done" countHeader="Transactions" total={kept} />
              ) : <p className="muted small">No row needed repair.</p>}
            </section>
            {missing.length > 0 && (
              <section>
                <h3 className="section-subhead">Missing fields</h3>
                <CountTable caption="Fields missing from rows" entries={missing} nameHeader="Field" countHeader="Rows without it" total={rows} />
              </section>
            )}
            <section>
              <h3 className="section-subhead">How the columns were understood</h3>
              {mapping.length ? (
                <>
                  <p className="muted small">
                    {mapping.length} column{mapping.length === 1 ? '' : 's'} recognised
                    {renamed.length ? <>, {renamed.length} under another name:</> : ' by their standard names.'}
                  </p>
                  {renamed.length > 0 && (
                    <div className="table-wrap" style={{ maxHeight: 220 }}>
                      <table className="data-table">
                        <caption className="sr-only">Column names mapped to standard fields</caption>
                        <thead>
                          <tr><th scope="col">Column in the file</th><th scope="col">Read as</th></tr>
                        </thead>
                        <tbody>
                          {renamed.map(([source, canonical]) => (
                            <tr key={source}><td><code>{source}</code></td><td><code>{canonical}</code></td></tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              ) : <p className="muted small">The column mapping was not recorded.</p>}
              {unmapped.length > 0 && (
                <>
                  <p className="muted small">Ignored columns (not part of the model):</p>
                  <ul className="chip-row">
                    {unmapped.map((column) => <li key={column} className="chip chip-small chip-muted"><code>{column}</code></li>)}
                  </ul>
                </>
              )}
            </section>
          </div>

          {examples.length > 0 && (
            <section>
              <h3 className="section-subhead">Example rows with problems</h3>
              <div className="table-wrap" style={{ maxHeight: 260 }}>
                <table className="data-table">
                  <caption className="sr-only">Example rows that were rejected or repaired</caption>
                  <thead>
                    <tr><th scope="col" className="num">Row</th><th scope="col">Transaction</th><th scope="col">Problem</th></tr>
                  </thead>
                  <tbody>
                    {examples.map((example, index) => (
                      <tr key={`${example.row}-${index}`}>
                        <td className="num">{fmtInt(example.row)}</td>
                        <td>{example.txid ? <code title={example.txid}>{String(example.txid).length > 22 ? `${String(example.txid).slice(0, 12)}…${String(example.txid).slice(-6)}` : example.txid}</code> : <span className="muted">no id</span>}</td>
                        <td>{asArray(example.problems).join('; ') || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}
    </Collapsible>
  );
}
