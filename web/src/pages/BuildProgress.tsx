/**
 * BuildProgress -- real-time build progress visualization.
 * Shows phase progress bar, streaming logs, audit results, and cancel button.
 */
import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import { useWebSocket } from '../hooks/useWebSocket';
import AppShell from '../components/AppShell';
import PhaseProgressBar from '../components/PhaseProgressBar';
import type { Phase } from '../components/PhaseProgressBar';
import BuildLogViewer from '../components/BuildLogViewer';
import type { LogEntry } from '../components/BuildLogViewer';
import BuildAuditCard from '../components/BuildAuditCard';
import type { AuditCheck } from '../components/BuildAuditCard';
import ConfirmDialog from '../components/ConfirmDialog';
import EmptyState from '../components/EmptyState';
import Skeleton from '../components/Skeleton';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

interface BuildStatus {
  id: string;
  project_id: string;
  phase: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  loop_count: number;
  error_detail: string | null;
  created_at: string;
}

interface AuditResult {
  phase: string;
  iteration: number;
  overall: string;
  checks: AuditCheck[];
}

function BuildProgress() {
  const { projectId } = useParams<{ projectId: string }>();
  const { token } = useAuth();
  const { addToast } = useToast();
  const navigate = useNavigate();

  const [build, setBuild] = useState<BuildStatus | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [auditResults, setAuditResults] = useState<AuditResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [noBuild, setNoBuild] = useState(false);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);

  // Parse phase number from phase string like "Phase 3"
  const parsePhaseNum = (phaseStr: string): number => {
    const match = phaseStr.match(/\d+/);
    return match ? parseInt(match[0], 10) : 0;
  };

  // Generate phases array for the progress bar
  const generatePhases = useCallback((): Phase[] => {
    if (!build) return [];
    const totalPhases = 12; // Phase 0-11
    const currentPhase = parsePhaseNum(build.phase);
    const phases: Phase[] = [];
    for (let i = 0; i <= totalPhases - 1; i++) {
      let status: Phase['status'] = 'pending';
      if (i < currentPhase) status = 'pass';
      else if (i === currentPhase) {
        if (build.status === 'completed') status = 'pass';
        else if (build.status === 'failed') status = 'fail';
        else status = 'active';
      }
      phases.push({ label: `P${i}`, status });
    }
    return phases;
  }, [build]);

  // Fetch initial build status
  useEffect(() => {
    const fetchBuild = async () => {
      try {
        const res = await fetch(`${API_BASE}/projects/${projectId}/build/status`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          setBuild(await res.json());
        } else if (res.status === 400) {
          setNoBuild(true);
        } else {
          addToast('Failed to load build status');
        }
      } catch {
        addToast('Network error loading build status');
      } finally {
        setLoading(false);
      }
    };
    fetchBuild();
  }, [projectId, token, addToast]);

  // Fetch initial logs
  useEffect(() => {
    if (!build) return;
    const fetchLogs = async () => {
      try {
        const res = await fetch(`${API_BASE}/projects/${projectId}/build/logs?limit=500`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const data = await res.json();
          setLogs(data.items ?? []);
        }
      } catch {
        /* best effort */
      }
    };
    fetchLogs();
  }, [build?.id, projectId, token]);

  // Handle WebSocket events
  useWebSocket(
    useCallback(
      (data) => {
        const payload = data.payload as Record<string, unknown>;
        const eventProjectId = payload.project_id as string;
        if (eventProjectId !== projectId) return;

        switch (data.type) {
          case 'build_started':
            setBuild(payload.build as BuildStatus);
            setNoBuild(false);
            break;
          case 'build_log': {
            const log = payload as unknown as LogEntry;
            setLogs((prev) => [...prev, log]);
            break;
          }
          case 'phase_complete':
          case 'build_complete':
          case 'build_error':
          case 'build_cancelled':
            setBuild(payload.build as BuildStatus);
            if (data.type === 'build_complete') {
              addToast('Build completed successfully!', 'success');
            } else if (data.type === 'build_error') {
              addToast('Build failed: ' + (payload.error ?? 'Unknown error'));
            }
            break;
          case 'audit_pass':
          case 'audit_fail': {
            const result: AuditResult = {
              phase: (payload.phase as string) ?? '',
              iteration: (payload.iteration as number) ?? 1,
              overall: data.type === 'audit_pass' ? 'PASS' : 'FAIL',
              checks: (payload.checks as AuditCheck[]) ?? [],
            };
            setAuditResults((prev) => [...prev, result]);
            break;
          }
        }
      },
      [projectId, addToast],
    ),
  );

  const handleCancel = async () => {
    try {
      const res = await fetch(`${API_BASE}/projects/${projectId}/build/cancel`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const updated = await res.json();
        setBuild(updated);
        addToast('Build cancelled', 'info');
      } else {
        addToast('Failed to cancel build');
      }
    } catch {
      addToast('Network error cancelling build');
    }
    setShowCancelConfirm(false);
  };

  const handleStartBuild = async () => {
    try {
      const res = await fetch(`${API_BASE}/projects/${projectId}/build`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const newBuild = await res.json();
        setBuild(newBuild);
        setNoBuild(false);
        setLogs([]);
        setAuditResults([]);
        addToast('Build started', 'success');
      } else {
        const data = await res.json().catch(() => ({ detail: 'Failed to start build' }));
        addToast(data.detail || 'Failed to start build');
      }
    } catch {
      addToast('Network error starting build');
    }
  };

  if (loading) {
    return (
      <AppShell>
        <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
          <Skeleton style={{ width: '100%', height: '40px', marginBottom: '24px' }} />
          <Skeleton style={{ width: '100%', height: '300px', marginBottom: '16px' }} />
          <Skeleton style={{ width: '100%', height: '120px' }} />
        </div>
      </AppShell>
    );
  }

  if (noBuild) {
    return (
      <AppShell>
        <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px' }}>
            <button
              onClick={() => navigate(`/projects/${projectId}`)}
              style={{ background: 'transparent', color: '#94A3B8', border: '1px solid #334155', borderRadius: '6px', padding: '6px 12px', cursor: 'pointer', fontSize: '0.8rem' }}
            >
              Back
            </button>
            <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Build Progress</h2>
          </div>
          <EmptyState
            message="No builds yet. Start a build to see progress here."
            actionLabel="Start Build"
            onAction={handleStartBuild}
          />
        </div>
      </AppShell>
    );
  }

  const isActive = build && ['pending', 'running'].includes(build.status);
  const elapsed = build?.started_at
    ? Math.round((Date.now() - new Date(build.started_at).getTime()) / 1000)
    : 0;
  const elapsedStr = elapsed > 0 ? `${Math.floor(elapsed / 60)}m ${elapsed % 60}s` : '';

  return (
    <AppShell>
      <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <button
              onClick={() => navigate(`/projects/${projectId}`)}
              style={{ background: 'transparent', color: '#94A3B8', border: '1px solid #334155', borderRadius: '6px', padding: '6px 12px', cursor: 'pointer', fontSize: '0.8rem' }}
            >
              Back
            </button>
            <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Build Progress</h2>
            {build && (
              <span
                style={{
                  padding: '2px 10px',
                  borderRadius: '4px',
                  background: build.status === 'completed' ? '#14532D' : build.status === 'failed' ? '#7F1D1D' : '#1E3A5F',
                  color: build.status === 'completed' ? '#22C55E' : build.status === 'failed' ? '#EF4444' : '#2563EB',
                  fontSize: '0.7rem',
                  fontWeight: 700,
                  textTransform: 'uppercase',
                }}
              >
                {build.status}
              </span>
            )}
          </div>
          {isActive && (
            <button
              onClick={() => setShowCancelConfirm(true)}
              style={{
                background: 'transparent',
                color: '#EF4444',
                border: '1px solid #EF4444',
                borderRadius: '6px',
                padding: '6px 16px',
                cursor: 'pointer',
                fontSize: '0.8rem',
                fontWeight: 600,
              }}
            >
              Cancel Build
            </button>
          )}
        </div>

        {/* Build Summary Header */}
        {build && (
          <div style={{ background: '#1E293B', borderRadius: '8px', padding: '14px 20px', marginBottom: '16px', display: 'flex', gap: '24px', fontSize: '0.8rem', flexWrap: 'wrap' }}>
            <div>
              <span style={{ color: '#94A3B8' }}>Phase: </span>
              <span style={{ fontWeight: 600 }}>{build.phase}</span>
            </div>
            {elapsedStr && (
              <div>
                <span style={{ color: '#94A3B8' }}>Elapsed: </span>
                {elapsedStr}
              </div>
            )}
            {build.loop_count > 0 && (
              <div>
                <span style={{ color: '#EAB308' }}>Loopback:</span>{' '}
                <span style={{ color: '#EAB308', fontWeight: 600 }}>Iteration {build.loop_count}</span>
              </div>
            )}
            {build.error_detail && (
              <div style={{ color: '#EF4444', flex: '1 1 100%', marginTop: '4px', fontSize: '0.75rem' }}>
                Error: {build.error_detail}
              </div>
            )}
          </div>
        )}

        {/* Phase Progress Bar */}
        <div style={{ marginBottom: '20px' }}>
          <PhaseProgressBar phases={generatePhases()} />
        </div>

        {/* Audit Results */}
        {auditResults.length > 0 && (
          <div style={{ marginBottom: '20px' }}>
            <h3 style={{ margin: '0 0 12px', fontSize: '0.9rem', color: '#94A3B8' }}>Audit Results</h3>
            {auditResults.map((result, i) => (
              <BuildAuditCard
                key={i}
                phase={result.phase}
                iteration={result.iteration}
                checks={result.checks}
                overall={result.overall}
              />
            ))}
          </div>
        )}

        {/* Streaming Logs */}
        <div>
          <h3 style={{ margin: '0 0 12px', fontSize: '0.9rem', color: '#94A3B8' }}>Build Logs</h3>
          <BuildLogViewer logs={logs} maxHeight={500} />
        </div>
      </div>

      {showCancelConfirm && (
        <ConfirmDialog
          title="Cancel Build"
          message="Are you sure you want to cancel the active build? This cannot be undone."
          confirmLabel="Cancel Build"
          onConfirm={handleCancel}
          onCancel={() => setShowCancelConfirm(false)}
        />
      )}
    </AppShell>
  );
}

export default BuildProgress;
