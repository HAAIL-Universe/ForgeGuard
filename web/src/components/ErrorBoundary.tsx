import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * Catches unhandled rendering errors and displays a recovery UI.
 * Wrap each route independently so one page crash doesn't take
 * down the entire application.
 */
export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary]', error, info.componentStack);

    // Auto-reload once on chunk / dynamic-import failures so the user
    // doesn't have to manually click "Try Again" after a deploy or
    // Vite HMR cache invalidation.
    const msg = error.message ?? '';
    if (
      msg.includes('Failed to fetch dynamically imported module') ||
      msg.includes('Loading chunk') ||
      msg.includes('Loading CSS chunk')
    ) {
      const key = 'chunk_error_reload';
      if (!sessionStorage.getItem(key)) {
        sessionStorage.setItem(key, '1');
        window.location.reload();
        return;
      }
      // Already tried once — fall through to error UI
      sessionStorage.removeItem(key);
    }
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;

      return (
        <div
          style={{
            background: '#0F172A',
            color: '#CBD5E1',
            minHeight: '100vh',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 16,
            fontFamily: 'system-ui, sans-serif',
          }}
        >
          <h2 style={{ color: '#F87171', margin: 0 }}>Something went wrong</h2>
          <p style={{ maxWidth: 480, textAlign: 'center', margin: 0 }}>
            An unexpected error occurred. You can try reloading the page or
            clicking the button below to retry.
          </p>
          {this.state.error && (
            <pre
              style={{
                background: '#1E293B',
                padding: 12,
                borderRadius: 8,
                maxWidth: 600,
                overflow: 'auto',
                fontSize: 13,
                color: '#94A3B8',
              }}
            >
              {this.state.error.message}
            </pre>
          )}
          <button
            onClick={this.handleRetry}
            style={{
              background: '#2563EB',
              color: '#fff',
              border: 'none',
              borderRadius: 6,
              padding: '8px 24px',
              cursor: 'pointer',
              fontSize: 14,
            }}
          >
            Try Again
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
