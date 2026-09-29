import React from 'react';

/** Catches render errors so a single broken view never blanks the workspace. */
export default class ErrorBoundary extends React.Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    if (import.meta.env.DEV) {
      console.error('Render error:', error?.message, info?.componentStack);
    }
  }

  retry = () => this.setState({ error: null });

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className={`status-box status-error${this.props.compact ? '' : ' app-error'}`} role="alert">
        <div>
          <p className="status-title">This view could not be displayed</p>
          <div className="status-body">
            The rest of the workspace is still usable.
            {import.meta.env.DEV && this.state.error?.message && <code className="error-code">{this.state.error.message}</code>}
          </div>
        </div>
        <button type="button" className="btn btn-small" onClick={this.retry}>Retry</button>
      </div>
    );
  }
}
