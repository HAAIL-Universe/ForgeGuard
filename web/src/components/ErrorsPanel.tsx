/**
 * ErrorsPanel — Aggregated error viewer for the build IDE.
 *
 * Displays deduplicated errors with iOS-style occurrence badges,
 * LLM-authored resolution notes, and category-appropriate action buttons:
 *   - Fixable errors   → 🔧 Fix button
 *   - Regeneratable    → 🔄 Regenerate button
 *   - Dismiss-only     → ✕ Dismiss button
 * Unresolved errors appear at the top; resolved ones collapse at the bottom.
 */
import { useState, useCallback, memo } from 'react';

/* ---------- Types ---------- */

export interface BuildError {
  id: string;
  fingerprint: string;
  first_seen: string;
  last_seen: string;
  occurrence_count: number;
  phase?: string;
  file_path?: string;
  source: string;
  severity: 'error' | 'fatal';
  message: string;
  resolved: boolean;
  resolved_at?: string;
  resolution_method?: 'auto-fix' | 'phase-complete' | 'dismissed';
  resolution_summary?: string;
  category?: 'fixable' | 'regeneratable' | 'dismiss_only';
}

interface ErrorsPanelProps {
  errors: BuildError[];
  onDismiss: (errorId: string) => void;
  onFix?: (errorId: string) => Promise<void>;
  buildStatus?: string;
}

/* ---------- Helpers ---------- */

function fmtTime(iso?: string): string {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return '';
  }
}

const METHOD_LABELS: Record<string, string> = {
  'auto-fix': '🔧 Auto-fix',
  'phase-complete': '✅ Phase completed',
  'dismissed': '✕ Dismissed',
};

/** Returns true when the build is in a state where error fixes are allowed. */
function canFix(buildStatus?: string): boolean {
  if (!buildStatus) return false;
  return ['paused', 'failed', 'completed', 'cancelled'].includes(buildStatus);
}

/* ---------- Sub-components ---------- */

const ErrorCard = memo(function ErrorCard({
  error,
  onDismiss,
  onFix,
  isFixing,
  buildStatus,
}: {
  error: BuildError;
  onDismiss: (id: string) => void;
  onFix?: (id: string) => void;
  isFixing: boolean;
  buildStatus?: string;
}) {
  const isResolved = error.resolved;
  const category = error.category || 'dismiss_only';
  const fixAllowed = canFix(buildStatus) && !isFixing;

  return (
    <div
      style={{
        padding: '10px 14px',
        borderBottom: '1px solid #1E293B',
        opacity: isResolved ? 0.6 : 1,
        transition: 'opacity 0.2s',
      }}
    >
      {/* Header row: severity dot · phase · file · badge · timestamp */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
        <span style={{
          width: '8px', height: '8px', borderRadius: '50%', flexShrink: 0,
          background: isResolved ? '#22C55E' : error.severity === 'fatal' ? '#DC2626' : '#EF4444',
        }} />

        {error.phase && (
          <span style={{ color: '#94A3B8', fontSize: '0.72rem', fontWeight: 600 }}>
            {error.phase}
          </span>
        )}

        {error.file_path && (
          <>
            <span style={{ color: '#475569', fontSize: '0.72rem' }}>·</span>
            <span style={{
              color: '#60A5FA', fontSize: '0.72rem', fontFamily: 'monospace',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              maxWidth: '200px',
            }}>
              {error.file_path}
            </span>
          </>
        )}

        {/* iOS-style occurrence badge */}
        {error.occurrence_count > 1 && (
          <span style={{
            background: '#DC2626',
            color: '#FFF',
            fontSize: '0.6rem',
            fontWeight: 700,
            borderRadius: '10px',
            padding: '1px 6px',
            minWidth: '18px',
            textAlign: 'center',
            lineHeight: '16px',
            flexShrink: 0,
          }}>
            ×{error.occurrence_count}
          </span>
        )}

        <span style={{ flex: 1 }} />

        <span style={{ color: '#475569', fontSize: '0.65rem', flexShrink: 0 }}>
          {fmtTime(error.first_seen)}
        </span>
      </div>

      {/* Error message */}
      <div style={{
        color: isResolved ? '#94A3B8' : '#F8FAFC',
        fontSize: '0.75rem',
        fontFamily: 'monospace',
        lineHeight: 1.5,
        wordBreak: 'break-word',
        paddingLeft: '16px',
        borderLeft: `2px solid ${isResolved ? '#22C55E44' : '#EF444488'}`,
        marginBottom: isResolved && error.resolution_summary ? '6px' : '0',
      }}>
        {error.message}
      </div>

      {/* Resolution note (LLM-authored) */}
      {isResolved && (error.resolution_method || error.resolution_summary) && (
        <div style={{
          paddingLeft: '16px',
          marginTop: '4px',
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
        }}>
          <span style={{ color: '#22C55E', fontSize: '0.7rem', fontWeight: 600 }}>
            {METHOD_LABELS[error.resolution_method || ''] || '✅ Resolved'}
          </span>
          {error.resolution_summary && (
            <span style={{ color: '#94A3B8', fontSize: '0.7rem' }}>
              — {error.resolution_summary}
            </span>
          )}
        </div>
      )}

      {/* Action buttons (only for unresolved errors) */}
      {!isResolved && (
        <div style={{ paddingLeft: '16px', marginTop: '6px', display: 'flex', gap: '6px' }}>
          {/* Fix / Regenerate button — only for fixable/regeneratable categories */}
          {category !== 'dismiss_only' && onFix && (
            <button
              onClick={() => onFix(error.id)}
              disabled={!fixAllowed}
              title={!canFix(buildStatus) ? 'Wait for build to pause or finish' : undefined}
              style={{
                background: fixAllowed ? '#1E3A5F' : '#1E293B',
                border: `1px solid ${fixAllowed ? '#60A5FA66' : '#334155'}`,
                borderRadius: '4px',
                color: fixAllowed ? '#60A5FA' : '#475569',
                fontSize: '0.65rem',
                fontWeight: 600,
                padding: '2px 8px',
                cursor: fixAllowed ? 'pointer' : 'not-allowed',
                transition: 'all 0.15s',
                opacity: isFixing ? 0.7 : 1,
              }}
            >
              {isFixing
                ? '⏳ Fixing...'
                : category === 'regeneratable'
                  ? '🔄 Regenerate'
                  : '🔧 Fix'}
            </button>
          )}

          {/* Dismiss button — always shown for unresolved */}
          <button
            onClick={() => onDismiss(error.id)}
            style={{
              background: 'transparent',
              border: '1px solid #334155',
              borderRadius: '4px',
              color: '#64748B',
              fontSize: '0.65rem',
              padding: '2px 8px',
              cursor: 'pointer',
              transition: 'all 0.15s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = '#475569';
              e.currentTarget.style.color = '#94A3B8';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = '#334155';
              e.currentTarget.style.color = '#64748B';
            }}
          >
            ✕ Dismiss
          </button>
        </div>
      )}
    </div>
  );
});

/* ---------- Main component ---------- */

function ErrorsPanel({ errors, onDismiss, onFix, buildStatus }: ErrorsPanelProps) {
  const [showResolved, setShowResolved] = useState(false);
  const [fixingIds, setFixingIds] = useState<Set<string>>(new Set());

  const handleFix = useCallback(async (errorId: string) => {
    if (!onFix) return;
    setFixingIds((prev) => new Set(prev).add(errorId));
    try {
      await onFix(errorId);
    } finally {
      setFixingIds((prev) => {
        const next = new Set(prev);
        next.delete(errorId);
        return next;
      });
    }
  }, [onFix]);

  const unresolved = errors.filter((e) => !e.resolved);
  const resolved = errors.filter((e) => e.resolved);

  return (
    <div
      data-testid="errors-panel"
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
        background: '#0B1120',
      }}
    >
      {/* Unresolved errors */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {unresolved.length === 0 && resolved.length === 0 && (
          <div style={{
            color: '#475569',
            fontSize: '0.8rem',
            textAlign: 'center',
            padding: '40px 20px',
          }}>
            No errors recorded
          </div>
        )}

        {unresolved.length === 0 && resolved.length > 0 && (
          <div style={{
            color: '#22C55E',
            fontSize: '0.8rem',
            textAlign: 'center',
            padding: '40px 20px',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: '4px',
          }}>
            <span style={{ fontSize: '1.5rem' }}>✅</span>
            All errors resolved
          </div>
        )}

        {unresolved.map((err) => (
          <ErrorCard
            key={err.id}
            error={err}
            onDismiss={onDismiss}
            onFix={onFix ? handleFix : undefined}
            isFixing={fixingIds.has(err.id)}
            buildStatus={buildStatus}
          />
        ))}

        {/* Resolved section — collapsible */}
        {resolved.length > 0 && (
          <>
            <button
              onClick={() => setShowResolved((v) => !v)}
              style={{
                width: '100%',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '8px 14px',
                background: '#0F172A',
                border: 'none',
                borderTop: '1px solid #1E293B',
                borderBottom: '1px solid #1E293B',
                cursor: 'pointer',
                color: '#64748B',
                fontSize: '0.7rem',
                fontWeight: 600,
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
              }}
            >
              <span style={{
                transform: showResolved ? 'rotate(90deg)' : 'rotate(0deg)',
                transition: 'transform 0.15s',
                display: 'inline-block',
                fontSize: '0.6rem',
              }}>
                ▶
              </span>
              Resolved ({resolved.length})
            </button>

            {showResolved && resolved.map((err) => (
              <ErrorCard
                key={err.id}
                error={err}
                onDismiss={onDismiss}
                isFixing={false}
                buildStatus={buildStatus}
              />
            ))}
          </>
        )}
      </div>
    </div>
  );
}

export default ErrorsPanel;
