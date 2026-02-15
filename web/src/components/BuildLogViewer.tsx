/**
 * BuildLogViewer -- terminal-style streaming log viewer with auto-scroll.
 * Color-coded by log level: info=white, warn=yellow, error=red, system=blue.
 */
import { useEffect, useRef } from 'react';

interface LogEntry {
  id: string;
  timestamp: string;
  source: string;
  level: string;
  message: string;
}

interface BuildLogViewerProps {
  logs: LogEntry[];
  maxHeight?: number;
}

const LEVEL_COLORS: Record<string, string> = {
  info: '#F8FAFC',
  warn: '#EAB308',
  error: '#EF4444',
  system: '#2563EB',
  debug: '#64748B',
};

function BuildLogViewer({ logs, maxHeight = 400 }: BuildLogViewerProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (bottomRef.current && typeof bottomRef.current.scrollIntoView === 'function') {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs.length]);

  return (
    <div
      data-testid="build-log-viewer"
      style={{
        background: '#0B1120',
        borderRadius: '8px',
        border: '1px solid #1E293B',
        padding: '12px 16px',
        maxHeight,
        overflowY: 'auto',
        fontFamily: 'monospace',
        fontSize: '0.75rem',
        lineHeight: 1.6,
      }}
    >
      {logs.length === 0 ? (
        <div style={{ color: '#64748B' }}>Waiting for build output...</div>
      ) : (
        logs.map((log) => {
          const color = LEVEL_COLORS[log.level] ?? LEVEL_COLORS.info;
          const ts = log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : '';
          return (
            <div key={log.id} style={{ color, display: 'flex', gap: '8px' }}>
              <span style={{ color: '#64748B', flexShrink: 0 }}>{ts}</span>
              <span style={{ color: '#94A3B8', flexShrink: 0 }}>[{log.source}]</span>
              <span style={{ wordBreak: 'break-word' }}>{log.message}</span>
            </div>
          );
        })
      )}
      <div ref={bottomRef} />
    </div>
  );
}

export type { LogEntry };
export default BuildLogViewer;
