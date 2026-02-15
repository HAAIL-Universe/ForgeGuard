/**
 * DevConsole — developer debug overlay for tracking build internals.
 *
 * Shows a running checklist of every micro-step in the build pipeline,
 * ticking off each one as WS events arrive. Helps pinpoint exactly
 * where a build stalls.
 */
import { useState, useEffect, useRef, useCallback } from 'react';

/* ------------------------------------------------------------------ */
/*  Types                                                             */
/* ------------------------------------------------------------------ */

export type StepStatus = 'pending' | 'active' | 'done' | 'error' | 'skipped';

export interface DevStep {
  id: string;
  /** Display label */
  label: string;
  /** Grouping header (Step 2, Step 3, etc.) */
  group: string;
  status: StepStatus;
  /** ISO timestamp when step became active */
  startedAt?: string;
  /** ISO timestamp when step completed */
  completedAt?: string;
  /** Extra detail (e.g. phase name, error message) */
  detail?: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  steps: DevStep[];
}

/* ------------------------------------------------------------------ */
/*  Step definitions — the master checklist                           */
/* ------------------------------------------------------------------ */

/** Canonical list of every trackable micro-step. */
export function createInitialSteps(): DevStep[] {
  return [
    // Step 2 — Backend Kick-Off
    { id: 'build_request',       group: 'Step 2 — Backend Kick-Off',       label: 'POST /build request sent',            status: 'pending' },
    { id: 'build_record',        group: 'Step 2 — Backend Kick-Off',       label: 'Build record created',                status: 'pending' },
    { id: 'working_dir',         group: 'Step 2 — Backend Kick-Off',       label: 'Working directory setup (clone/init)', status: 'pending' },
    { id: 'contracts_loaded',    group: 'Step 2 — Backend Kick-Off',       label: 'Contracts loaded into directive',      status: 'pending' },
    { id: 'agent_connected',     group: 'Step 2 — Backend Kick-Off',       label: 'Agent streaming connection opened',   status: 'pending' },

    // Step 3 — Build Loop
    { id: 'build_overview',      group: 'Step 3 — Build Loop',             label: 'Build overview received (phase list)', status: 'pending' },
    { id: 'phase_plan',          group: 'Step 3 — Build Loop',             label: 'Phase plan emitted (task checklist)',  status: 'pending' },
    { id: 'tool_calls',          group: 'Step 3 — Build Loop',             label: 'Tool calls executing',                status: 'pending' },
    { id: 'files_written',       group: 'Step 3 — Build Loop',             label: 'Files written to repo',               status: 'pending' },
    { id: 'tests_executed',      group: 'Step 3 — Build Loop',             label: 'Tests executed',                      status: 'pending' },
    { id: 'phase_signoff',       group: 'Step 3 — Build Loop',             label: 'Phase sign-off received',             status: 'pending' },

    // Step 4 — Audit Gate
    { id: 'audit_started',       group: 'Step 4 — Audit Gate',             label: 'Inline audit started (Sonnet)',       status: 'pending' },
    { id: 'audit_result',        group: 'Step 4 — Audit Gate',             label: 'Audit verdict received',              status: 'pending' },
    { id: 'recovery_plan',       group: 'Step 4 — Audit Gate',             label: 'Recovery planner (if audit fail)',     status: 'pending' },

    // Step 5 — Git Operations
    { id: 'git_commit',          group: 'Step 5 — Git Operations',         label: 'Git commit created',                  status: 'pending' },
    { id: 'git_push',            group: 'Step 5 — Git Operations',         label: 'Git push to remote',                  status: 'pending' },

    // Step 6 — Build Completion
    { id: 'final_commit',        group: 'Step 6 — Build Completion',       label: 'Final commit (build complete)',        status: 'pending' },
    { id: 'final_push',          group: 'Step 6 — Build Completion',       label: 'Final push to GitHub',                status: 'pending' },
    { id: 'build_complete',      group: 'Step 6 — Build Completion',       label: 'build_complete event received',        status: 'pending' },
    { id: 'cleanup',             group: 'Step 6 — Build Completion',       label: 'Cleanup & task teardown',              status: 'pending' },
  ];
}

/**
 * Maps a WS event type to an update function that marks relevant steps.
 * Returns an array of step mutations: { id, status, detail? }.
 */
export function mapEventToSteps(
  eventType: string,
  payload: Record<string, unknown>,
): Array<{ id: string; status: StepStatus; detail?: string }> {
  const now = new Date().toISOString();

  switch (eventType) {
    case 'build_started':
      return [
        { id: 'build_request', status: 'done', detail: 'Request accepted' },
        { id: 'build_record', status: 'done', detail: `Build ID: ${(payload.build as any)?.id ?? payload.id ?? '?'}` },
        { id: 'working_dir', status: 'active', detail: 'Setting up...' },
      ];

    case 'build_overview':
      return [
        { id: 'working_dir', status: 'done', detail: 'Ready' },
        { id: 'contracts_loaded', status: 'done' },
        { id: 'agent_connected', status: 'done' },
        { id: 'build_overview', status: 'done', detail: `${(payload.phases as any[])?.length ?? '?'} phases` },
      ];

    case 'build_log': {
      const msg = ((payload.message ?? payload.msg ?? '') as string).toLowerCase();
      // Detect specific log messages from the backend
      if (msg.includes('clone') || msg.includes('working directory'))
        return [{ id: 'working_dir', status: 'done', detail: msg.slice(0, 80) }];
      if (msg.includes('contracts loaded') || msg.includes('directive built'))
        return [{ id: 'contracts_loaded', status: 'done' }];
      if (msg.includes('streaming') || msg.includes('agent connected'))
        return [{ id: 'agent_connected', status: 'done' }];
      if (msg.includes('git commit'))
        return [{ id: 'git_commit', status: 'done', detail: msg.slice(0, 80) }];
      if (msg.includes('git push') || msg.includes('pushed'))
        return [{ id: 'git_push', status: 'done', detail: msg.slice(0, 80) }];
      if (msg.includes('audit') && msg.includes('start'))
        return [{ id: 'audit_started', status: 'active' }];
      return [];
    }

    case 'build_plan':
    case 'phase_plan':
      return [
        { id: 'phase_plan', status: 'done', detail: `${(payload.tasks as any[])?.length ?? '?'} tasks — ${payload.phase ?? ''}` },
        { id: 'tool_calls', status: 'active', detail: 'Executing...' },
      ];

    case 'tool_use':
      return [
        { id: 'tool_calls', status: 'active', detail: `${payload.tool_name}(${((payload.input_summary ?? '') as string).slice(0, 40)})` },
      ];

    case 'file_created':
      return [
        { id: 'files_written', status: 'active', detail: (payload.path ?? '') as string },
      ];

    case 'test_run':
      return [
        { id: 'tests_executed', status: payload.passed ? 'done' : 'error', detail: `${payload.command} → exit ${payload.exit_code}` },
      ];

    case 'phase_complete':
      return [
        { id: 'tool_calls', status: 'done' },
        { id: 'files_written', status: 'done' },
        { id: 'phase_signoff', status: 'done', detail: (payload.phase ?? '') as string },
        { id: 'audit_started', status: 'active', detail: 'Running audit...' },
        // Reset loop steps for the next phase
        { id: 'phase_plan', status: 'pending' },
      ];

    case 'audit_pass':
      return [
        { id: 'audit_started', status: 'done', detail: 'Audit complete' },
        { id: 'audit_result', status: 'done', detail: `PASS — ${payload.phase ?? ''}` },
        { id: 'recovery_plan', status: 'skipped' },
        { id: 'git_commit', status: 'active', detail: 'Committing phase...' },
      ];

    case 'audit_fail':
      return [
        { id: 'audit_started', status: 'done' },
        { id: 'audit_result', status: 'error', detail: `FAIL — ${payload.phase ?? ''} (loop ${payload.loop_count})` },
        { id: 'recovery_plan', status: 'active' },
      ];

    case 'recovery_plan':
      return [
        { id: 'recovery_plan', status: 'done', detail: `Plan generated for ${payload.phase}` },
        // Reset build-loop steps for retry
        { id: 'phase_plan', status: 'active', detail: 'Retrying phase...' },
        { id: 'tool_calls', status: 'pending' },
        { id: 'files_written', status: 'pending' },
        { id: 'tests_executed', status: 'pending' },
        { id: 'phase_signoff', status: 'pending' },
        { id: 'audit_started', status: 'pending' },
        { id: 'audit_result', status: 'pending' },
      ];

    case 'build_complete':
      return [
        { id: 'git_commit', status: 'done' },
        { id: 'git_push', status: 'done' },
        { id: 'final_commit', status: 'done' },
        { id: 'final_push', status: 'done' },
        { id: 'build_complete', status: 'done', detail: 'All phases complete' },
        { id: 'cleanup', status: 'done' },
      ];

    case 'build_error':
      return [
        { id: 'build_complete', status: 'error', detail: (payload.error_detail ?? payload.error ?? 'Unknown error') as string },
      ];

    case 'build_cancelled':
      return [
        { id: 'build_complete', status: 'error', detail: 'Cancelled by user' },
      ];

    case 'build_paused':
      return [
        { id: 'audit_result', status: 'error', detail: `Paused — ${payload.phase} (${payload.loop_count} failures)` },
      ];

    case 'build_resumed':
      return [
        { id: 'phase_plan', status: 'active', detail: `Resumed (${payload.action})` },
        { id: 'tool_calls', status: 'pending' },
        { id: 'tests_executed', status: 'pending' },
        { id: 'phase_signoff', status: 'pending' },
      ];

    case 'build_turn': {
      const compacted = payload.compacted as boolean;
      if (compacted) {
        return [{ id: 'agent_connected', status: 'done', detail: `Context compacted at turn ${payload.turn}` }];
      }
      return [];
    }

    default:
      return [];
  }
}

/* ------------------------------------------------------------------ */
/*  Styles                                                            */
/* ------------------------------------------------------------------ */

const overlayStyle: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(0, 0, 0, 0.65)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 200,
};

const modalStyle: React.CSSProperties = {
  background: '#0F172A',
  border: '1px solid #1E293B',
  borderRadius: '12px',
  width: '560px',
  maxHeight: '85vh',
  display: 'flex',
  flexDirection: 'column',
  boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
};

const headerStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  padding: '16px 20px',
  borderBottom: '1px solid #1E293B',
};

const bodyStyle: React.CSSProperties = {
  flex: 1,
  overflowY: 'auto',
  padding: '16px 20px',
};

const STATUS_ICON_MAP: Record<StepStatus, string> = {
  pending: '○',
  active: '◐',
  done: '✓',
  error: '✕',
  skipped: '—',
};

const STATUS_COLOR_MAP: Record<StepStatus, string> = {
  pending: '#475569',
  active: '#3B82F6',
  done: '#22C55E',
  error: '#EF4444',
  skipped: '#64748B',
};

/* ------------------------------------------------------------------ */
/*  Component                                                         */
/* ------------------------------------------------------------------ */

export default function DevConsole({ open, onClose, steps }: Props) {
  const bodyRef = useRef<HTMLDivElement>(null);

  /* Auto-scroll to the latest active/done step */
  useEffect(() => {
    if (!open) return;
    const active = bodyRef.current?.querySelector('[data-step-active="true"]');
    active?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [steps, open]);

  if (!open) return null;

  /* Group steps by their group label */
  const groups: { label: string; items: DevStep[] }[] = [];
  let currentGroup = '';
  for (const step of steps) {
    if (step.group !== currentGroup) {
      currentGroup = step.group;
      groups.push({ label: currentGroup, items: [] });
    }
    groups[groups.length - 1].items.push(step);
  }

  const doneCount = steps.filter((s) => s.status === 'done').length;
  const totalCount = steps.filter((s) => s.status !== 'skipped').length;
  const progressPct = totalCount > 0 ? Math.round((doneCount / totalCount) * 100) : 0;

  return (
    <div style={overlayStyle} onClick={onClose} data-testid="dev-console-overlay">
      <div style={modalStyle} onClick={(e) => e.stopPropagation()} data-testid="dev-console-modal">
        {/* Header */}
        <div style={headerStyle}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ fontSize: '1rem' }}>🛠</span>
            <h3 style={{ margin: 0, fontSize: '0.95rem', color: '#F8FAFC' }}>
              Dev Console
            </h3>
            <span style={{ fontSize: '0.7rem', color: '#64748B' }}>
              {doneCount}/{totalCount} ({progressPct}%)
            </span>
          </div>
          <button
            onClick={onClose}
            data-testid="dev-console-close"
            style={{
              background: 'transparent',
              border: 'none',
              color: '#94A3B8',
              cursor: 'pointer',
              fontSize: '1.2rem',
              padding: '4px 8px',
            }}
          >
            ✕
          </button>
        </div>

        {/* Progress bar */}
        <div style={{ padding: '0 20px', paddingTop: '8px' }}>
          <div style={{ background: '#1E293B', borderRadius: '4px', height: '4px', overflow: 'hidden' }}>
            <div
              style={{
                width: `${progressPct}%`,
                height: '100%',
                background: progressPct === 100 ? '#22C55E' : '#3B82F6',
                transition: 'width 0.3s ease',
              }}
            />
          </div>
        </div>

        {/* Body */}
        <div style={bodyStyle} ref={bodyRef}>
          {groups.map((group) => (
            <div key={group.label} style={{ marginBottom: '16px' }}>
              <div style={{
                fontSize: '0.72rem',
                fontWeight: 700,
                color: '#94A3B8',
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
                marginBottom: '8px',
                paddingBottom: '4px',
                borderBottom: '1px solid #1E293B',
              }}>
                {group.label}
              </div>
              {group.items.map((step) => {
                const isActive = step.status === 'active';
                return (
                  <div
                    key={step.id}
                    data-testid={`dev-step-${step.id}`}
                    data-step-active={isActive ? 'true' : undefined}
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: '10px',
                      padding: '6px 8px',
                      borderRadius: '4px',
                      background: isActive ? '#1E3A5F22' : 'transparent',
                      transition: 'background 0.2s',
                    }}
                  >
                    {/* Status icon */}
                    <span
                      style={{
                        color: STATUS_COLOR_MAP[step.status],
                        fontSize: '0.85rem',
                        width: '16px',
                        textAlign: 'center',
                        flexShrink: 0,
                        marginTop: '1px',
                        ...(isActive ? { animation: 'spin 1.2s linear infinite', display: 'inline-block' } : {}),
                      }}
                    >
                      {STATUS_ICON_MAP[step.status]}
                    </span>

                    {/* Label + detail */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{
                        fontSize: '0.78rem',
                        color: step.status === 'pending' ? '#64748B'
                          : step.status === 'skipped' ? '#475569'
                          : '#E2E8F0',
                        fontWeight: isActive ? 600 : 400,
                      }}>
                        {step.label}
                      </div>
                      {step.detail && (
                        <div style={{
                          fontSize: '0.65rem',
                          color: step.status === 'error' ? '#EF4444' : '#64748B',
                          marginTop: '1px',
                          fontFamily: 'monospace',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}>
                          {step.detail}
                        </div>
                      )}
                    </div>

                    {/* Timestamp */}
                    {step.completedAt && (
                      <span style={{ fontSize: '0.6rem', color: '#475569', flexShrink: 0, fontFamily: 'monospace' }}>
                        {new Date(step.completedAt).toLocaleTimeString('en-GB', { hour12: false })}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
