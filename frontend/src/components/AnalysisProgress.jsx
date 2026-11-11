import { Spinner } from './common.jsx';

function clock(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

/**
 * Progress of the analysis that is running in the background: which stage the
 * pipeline is in, how far along it is, and for how long it has been running.
 * `compact` is the one-line form used in the top bar.
 */
export default function AnalysisProgress({ job, compact = false }) {
  const percent = Math.round(Math.max(0, Math.min(1, Number(job.fraction) || 0)) * 100);
  const stage = job.stage || 'Starting';
  return (
    <div className={`progress${compact ? ' progress-compact' : ''}`}>
      <span className="progress-label">
        <Spinner label="Analysis running" />
        <span>
          Analysing <strong>{job.name}</strong>
          <span className="progress-stage"> · {stage}</span>
        </span>
      </span>
      <div
        className="progress-track"
        role="progressbar"
        aria-label={`Analysis of ${job.name}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={`${stage}, ${percent}%`}
      >
        <span className="progress-fill" style={{ width: `${percent}%` }} />
      </div>
      <span className="progress-figure">{percent}%</span>
      <span className="elapsed" title="Time since the analysis started">{clock(job.elapsed)}</span>
    </div>
  );
}
