/**
 * BuildProgress -- real-time build progress visualization.
 *
 * Two-column layout:
 *   Left  (40%) — Phase checklist with summaries, status icons, per-phase tokens
 *   Right (60%) — Token/cost metrics card, live activity feed, cancel button
 *
 * All data streamed via WebSocket; initial state fetched from REST.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import { useWebSocket } from '../hooks/useWebSocket';
import AppShell from '../components/AppShell';
import ConfirmDialog from '../components/ConfirmDialog';
import EmptyState from '../components/EmptyState';
import Skeleton from '../components/Skeleton';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

/* ------------------------------------------------------------------ */
/*  Pricing — matches backend _MODEL_PRICING                          */
/* ------------------------------------------------------------------ */

const MODEL_PRICING: Record<string, { input: number; output: number }> = {
  'claude-opus-4':     { input: 15 / 1_000_000, output: 75 / 1_000_000 },
  'claude-sonnet-4':   { input: 3 / 1_000_000,  output: 15 / 1_000_000 },
  'claude-haiku-4':    { input: 1 / 1_000_000,  output: 5 / 1_000_000 },
  'claude-3-5-sonnet': { input: 3 / 1_000_000,  output: 15 / 1_000_000 },
};
const DEFAULT_PRICING = { input: 15 / 1_000_000, output: 75 / 1_000_000 };

function getTokenCost(model: string, input: number, output: number): number {
  for (const [prefix, rates] of Object.entries(MODEL_PRICING)) {
    if (model.startsWith(prefix)) {
      return input * rates.input + output * rates.output;
    }
  }
  return input * DEFAULT_PRICING.input + output * DEFAULT_PRICING.output;
}

/* ------------------------------------------------------------------ */
/*  Types                                                             */
/* ------------------------------------------------------------------ */

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

interface PhaseDefinition {
  number: number;
  name: string;
  objective: string;
  deliverables: string[];
}

type PhaseStatus = 'pending' | 'active' | 'pass' | 'fail';

interface PhaseState {
  def: PhaseDefinition;
  status: PhaseStatus;
  input_tokens: number;
  output_tokens: number;
  elapsed_ms: number;
}

interface ActivityEntry {
  time: string;
  message: string;
  level: 'info' | 'warn' | 'error' | 'system';
}

interface BuildFile {
  path: string;
  size_bytes: number;
  language: string;
  created_at: string;
}

/* ------------------------------------------------------------------ */
/*  Styles                                                            */
/* ------------------------------------------------------------------ */

const pageStyle: React.CSSProperties = {
  padding: '24px',
  maxWidth: '1280px',
  margin: '0 auto',
};

const twoColStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: '2fr 3fr',
  gap: '20px',
  alignItems: 'start',
};

const cardStyle: React.CSSProperties = {
  background: '#1E293B',
  borderRadius: '8px',
  padding: '16px 20px',
};

const phaseRowStyle = (isActive: boolean): React.CSSProperties => ({
  display: 'flex',
  gap: '10px',
  padding: '10px 12px',
  borderRadius: '6px',
  background: isActive ? '#1E3A5F' : 'transparent',
  borderLeft: isActive ? '3px solid #2563EB' : '3px solid transparent',
  transition: 'background 0.2s',
  cursor: 'pointer',
});

const metricBoxStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '2px',
  flex: 1,
  minWidth: '100px',
};

const feedStyle: React.CSSProperties = {
  background: '#0B1120',
  borderRadius: '8px',
  border: '1px solid #1E293B',
  padding: '12px 16px',
  maxHeight: '420px',
  overflowY: 'auto',
  fontFamily: 'monospace',
  fontSize: '0.72rem',
  lineHeight: 1.7,
};

const LEVEL_COLOR: Record<string, string> = {
  info: '#F8FAFC',
  warn: '#EAB308',
  error: '#EF4444',
  system: '#2563EB',
};

const STATUS_ICON: Record<PhaseStatus, string> = {
  pending: '○',
  active: '◐',
  pass: '●',
  fail: '✕',
};

const STATUS_COLOR: Record<PhaseStatus, string> = {
  pending: '#475569',
  active: '#2563EB',
  pass: '#22C55E',
  fail: '#EF4444',
};

/* ------------------------------------------------------------------ */
/*  Component                                                         */
/* ------------------------------------------------------------------ */

function BuildProgress() {
  const { projectId } = useParams<{ projectId: string }>();
  const { token } = useAuth();
  const { addToast } = useToast();
  const navigate = useNavigate();

  /* State */
  const [build, setBuild] = useState<BuildStatus | null>(null);
  const [phaseDefs, setPhaseDefs] = useState<PhaseDefinition[]>([]);
  const [phaseStates, setPhaseStates] = useState<Map<number, PhaseState>>(new Map());
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [totalTokens, setTotalTokens] = useState({ input: 0, output: 0 });
  const [loading, setLoading] = useState(true);
  const [noBuild, setNoBuild] = useState(false);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const [expandedPhase, setExpandedPhase] = useState<number | null>(null);
  const [buildFiles, setBuildFiles] = useState<BuildFile[]>([]);
  const [filesExpanded, setFilesExpanded] = useState(true);
  const [startTime] = useState(() => Date.now());
  const [elapsed, setElapsed] = useState(0);
  const feedEndRef = useRef<HTMLDivElement>(null);
  const phaseStartRef = useRef<number>(Date.now());

  /* ------ helpers ------ */

  const addActivity = useCallback((msg: string, level: ActivityEntry['level'] = 'info') => {
    const time = new Date().toLocaleTimeString('en-GB', { hour12: false });
    setActivity((prev) => [...prev, { time, message: msg, level }]);
  }, []);

  const parsePhaseNum = (phaseStr: string): number => {
    const m = phaseStr.match(/\d+/);
    return m ? parseInt(m[0], 10) : 0;
  };

  /* ------ auto-scroll feed ------ */
  useEffect(() => {
    feedEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [activity]);

  /* ------ elapsed timer ------ */
  useEffect(() => {
    if (!build || !['pending', 'running'].includes(build.status)) return;
    const ref = build.started_at ? new Date(build.started_at).getTime() : startTime;
    const iv = setInterval(() => setElapsed(Math.floor((Date.now() - ref) / 1000)), 1000);
    return () => clearInterval(iv);
  }, [build, startTime]);

  /* ------ fetch initial data ------ */
  useEffect(() => {
    const load = async () => {
      try {
        const [statusRes, phasesRes, logsRes] = await Promise.all([
          fetch(`${API_BASE}/projects/${projectId}/build/status`, {
            headers: { Authorization: `Bearer ${token}` },
          }),
          fetch(`${API_BASE}/projects/${projectId}/build/phases`, {
            headers: { Authorization: `Bearer ${token}` },
          }),
          fetch(`${API_BASE}/projects/${projectId}/build/logs?limit=500`, {
            headers: { Authorization: `Bearer ${token}` },
          }),
        ]);

        if (statusRes.status === 400) {
          setNoBuild(true);
          setLoading(false);
          return;
        }

        if (statusRes.ok) {
          const buildData: BuildStatus = await statusRes.json();
          setBuild(buildData);

          /* Seed activity from historical logs */
          if (logsRes.ok) {
            const logData = await logsRes.json();
            const items = (logData.items ?? []) as {
              timestamp: string;
              message: string;
              level: string;
            }[];
            setActivity(
              items.map((l) => ({
                time: new Date(l.timestamp).toLocaleTimeString('en-GB', { hour12: false }),
                message: l.message,
                level: (l.level ?? 'info') as ActivityEntry['level'],
              })),
            );
          }

          /* Seed token totals from cost summary */
          try {
            const costRes = await fetch(`${API_BASE}/projects/${projectId}/build/summary`, {
              headers: { Authorization: `Bearer ${token}` },
            });
            if (costRes.ok) {
              const costData = await costRes.json();
              setTotalTokens({
                input: costData.cost?.total_input_tokens ?? 0,
                output: costData.cost?.total_output_tokens ?? 0,
              });
            }
          } catch { /* best effort */ }

          /* Seed build files */
          try {
            const filesRes = await fetch(`${API_BASE}/projects/${projectId}/build/files`, {
              headers: { Authorization: `Bearer ${token}` },
            });
            if (filesRes.ok) {
              const filesData = await filesRes.json();
              setBuildFiles(filesData.items ?? []);
            }
          } catch { /* best effort */ }
        } else {
          addToast('Failed to load build');
        }

        /* Phase definitions */
        if (phasesRes.ok) {
          const defs: PhaseDefinition[] = await phasesRes.json();
          setPhaseDefs(defs);

          /* Build initial phase states from current build status */
          const statusData: BuildStatus | null = statusRes.ok ? await statusRes.json().catch(() => null) : null;
          const currentPhaseNum = statusData ? parsePhaseNum(statusData.phase) : 0;

          const map = new Map<number, PhaseState>();
          for (const def of defs) {
            let status: PhaseStatus = 'pending';
            if (statusData) {
              if (def.number < currentPhaseNum) status = 'pass';
              else if (def.number === currentPhaseNum) {
                if (statusData.status === 'completed') status = 'pass';
                else if (statusData.status === 'failed') status = 'fail';
                else status = 'active';
              }
            }
            map.set(def.number, {
              def,
              status,
              input_tokens: 0,
              output_tokens: 0,
              elapsed_ms: 0,
            });
          }
          setPhaseStates(map);
        }
      } catch {
        addToast('Network error loading build');
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [projectId, token, addToast]);

  /* ------ WebSocket handler ------ */
  useWebSocket(
    useCallback(
      (data) => {
        const payload = data.payload as Record<string, unknown>;
        const eventPid = payload.project_id as string;
        if (eventPid && eventPid !== projectId) return;

        switch (data.type) {
          case 'build_started': {
            setBuild(payload.build as BuildStatus ?? payload as unknown as BuildStatus);
            setNoBuild(false);
            addActivity('Build started', 'system');

            /* Set all phases to pending, first phase active */
            setPhaseStates((prev) => {
              const next = new Map(prev);
              for (const [num, ps] of next) {
                next.set(num, { ...ps, status: num === 0 ? 'active' : 'pending' });
              }
              return next;
            });
            phaseStartRef.current = Date.now();
            break;
          }

          case 'build_log': {
            const msg = (payload.message ?? payload.msg ?? '') as string;
            const lvl = (payload.level ?? 'info') as ActivityEntry['level'];
            if (msg) addActivity(msg, lvl);
            break;
          }

          case 'phase_complete': {
            const phase = payload.phase as string;
            const phaseNum = parsePhaseNum(phase);
            const inTok = (payload.input_tokens ?? 0) as number;
            const outTok = (payload.output_tokens ?? 0) as number;
            const elapsed_ms = Date.now() - phaseStartRef.current;

            addActivity(`✓ ${phase} complete (${inTok.toLocaleString()} in / ${outTok.toLocaleString()} out)`, 'system');

            /* Accumulate total tokens */
            setTotalTokens((prev) => ({
              input: prev.input + inTok,
              output: prev.output + outTok,
            }));

            /* Update phase states: mark this phase pass, next phase active */
            setPhaseStates((prev) => {
              const next = new Map(prev);
              const current = next.get(phaseNum);
              if (current) {
                next.set(phaseNum, {
                  ...current,
                  status: 'pass',
                  input_tokens: inTok,
                  output_tokens: outTok,
                  elapsed_ms,
                });
              }
              /* Mark next phase active */
              const nextPhase = next.get(phaseNum + 1);
              if (nextPhase && nextPhase.status === 'pending') {
                next.set(phaseNum + 1, { ...nextPhase, status: 'active' });
              }
              return next;
            });

            setBuild((prev) => prev ? { ...prev, phase: phase } : prev);
            phaseStartRef.current = Date.now();
            break;
          }

          case 'build_complete': {
            setBuild((prev) => prev ? { ...prev, status: 'completed' } : prev);
            const totalIn = (payload.total_input_tokens ?? 0) as number;
            const totalOut = (payload.total_output_tokens ?? 0) as number;
            if (totalIn || totalOut) {
              setTotalTokens({ input: totalIn, output: totalOut });
            }
            addActivity('Build completed successfully!', 'system');

            /* Mark all phases as pass */
            setPhaseStates((prev) => {
              const next = new Map(prev);
              for (const [num, ps] of next) {
                if (ps.status !== 'fail') next.set(num, { ...ps, status: 'pass' });
              }
              return next;
            });
            addToast('Build completed!', 'success');
            break;
          }

          case 'build_error': {
            setBuild((prev) => prev ? { ...prev, status: 'failed', error_detail: (payload.error_detail ?? payload.error ?? '') as string } : prev);
            addActivity(`Build failed: ${payload.error_detail ?? payload.error ?? 'Unknown error'}`, 'error');

            /* Mark current active phase as fail */
            setPhaseStates((prev) => {
              const next = new Map(prev);
              for (const [num, ps] of next) {
                if (ps.status === 'active') next.set(num, { ...ps, status: 'fail' });
              }
              return next;
            });
            break;
          }

          case 'build_cancelled': {
            setBuild((prev) => prev ? { ...prev, status: 'cancelled' } : prev);
            addActivity('Build cancelled by user', 'warn');
            break;
          }

          case 'audit_pass': {
            const phase = payload.phase as string;
            addActivity(`Audit PASS for ${phase}`, 'system');
            break;
          }

          case 'audit_fail': {
            const phase = payload.phase as string;
            const loop = payload.loop_count as number;
            addActivity(`Audit FAIL for ${phase} (loop ${loop})`, 'warn');
            break;
          }

          case 'file_created': {
            const filePath = (payload.path ?? '') as string;
            const sizeBytes = (payload.size_bytes ?? 0) as number;
            const language = (payload.language ?? '') as string;
            if (filePath) {
              setBuildFiles((prev) => {
                // Avoid duplicates (same path)
                if (prev.some((f) => f.path === filePath)) return prev;
                return [...prev, { path: filePath, size_bytes: sizeBytes, language, created_at: new Date().toISOString() }];
              });
              addActivity(`File created: ${filePath} (${sizeBytes} bytes)`, 'info');
            }
            break;
          }
        }
      },
      [projectId, addActivity, addToast],
    ),
  );

  /* ------ actions ------ */

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
        setActivity([]);
        setTotalTokens({ input: 0, output: 0 });
        addToast('Build started', 'success');
      } else {
        const data = await res.json().catch(() => ({ detail: 'Failed to start build' }));
        addToast(data.detail || 'Failed to start build');
      }
    } catch {
      addToast('Network error starting build');
    }
  };

  /* ------ derived values ------ */

  const isActive = build && ['pending', 'running'].includes(build.status);
  const buildModel = 'claude-opus-4-6';
  const contextWindow = 200_000;
  const totalTok = totalTokens.input + totalTokens.output;
  const ctxPercent = Math.min(100, (totalTok / contextWindow) * 100);
  const ctxColor = ctxPercent > 80 ? '#EF4444' : ctxPercent > 50 ? '#F59E0B' : '#22C55E';
  const estimatedCost = getTokenCost(buildModel, totalTokens.input, totalTokens.output);
  const elapsedStr = elapsed > 0 ? `${Math.floor(elapsed / 60)}m ${elapsed % 60}s` : '0s';

  const doneCount = Array.from(phaseStates.values()).filter((p) => p.status === 'pass').length;
  const totalPhases = phaseStates.size || phaseDefs.length;

  /* ------ render: loading ------ */

  if (loading) {
    return (
      <AppShell>
        <div style={pageStyle}>
          <Skeleton style={{ width: '100%', height: '40px', marginBottom: '24px' }} />
          <div style={twoColStyle}>
            <Skeleton style={{ width: '100%', height: '400px' }} />
            <div>
              <Skeleton style={{ width: '100%', height: '120px', marginBottom: '16px' }} />
              <Skeleton style={{ width: '100%', height: '300px' }} />
            </div>
          </div>
        </div>
      </AppShell>
    );
  }

  /* ------ render: no build ------ */

  if (noBuild) {
    return (
      <AppShell>
        <div style={pageStyle}>
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

  /* ------ render: main ------ */

  return (
    <AppShell>
      <div style={pageStyle}>
        {/* ---- Header ---- */}
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
          <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
            {build?.status === 'completed' && (
              <button
                onClick={() => navigate(`/projects/${projectId}/build/complete`)}
                style={{
                  background: '#16A34A',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '6px',
                  padding: '6px 16px',
                  cursor: 'pointer',
                  fontSize: '0.8rem',
                  fontWeight: 600,
                }}
              >
                View Summary
              </button>
            )}
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
        </div>

        {/* ---- Two-column layout ---- */}
        <div style={twoColStyle}>

          {/* ======== LEFT: Phase Checklist + Files ======== */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={{ ...cardStyle, padding: '12px 16px' }}>
            <h3 style={{ margin: '0 0 12px', fontSize: '0.9rem', color: '#F8FAFC' }}>
              Phases ({doneCount}/{totalPhases})
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
              {(phaseStates.size > 0
                ? Array.from(phaseStates.entries()).sort((a, b) => a[0] - b[0]).map(([num, ps]) => ({ num, ps }))
                : phaseDefs.map((d) => ({ num: d.number, ps: { def: d, status: 'pending' as PhaseStatus, input_tokens: 0, output_tokens: 0, elapsed_ms: 0 } }))
              ).map(({ num, ps }) => {
                const isExp = expandedPhase === num;
                const isActivePhase = ps.status === 'active';
                const phaseElapsed = ps.elapsed_ms > 0 ? `${Math.floor(ps.elapsed_ms / 60000)}m ${Math.floor((ps.elapsed_ms % 60000) / 1000)}s` : '';

                return (
                  <div key={num}>
                    <div
                      style={phaseRowStyle(isActivePhase)}
                      onClick={() => setExpandedPhase(isExp ? null : num)}
                    >
                      {/* Status icon */}
                      <span style={{ color: STATUS_COLOR[ps.status], fontSize: '1rem', width: '20px', textAlign: 'center', flexShrink: 0 }}>
                        {isActivePhase ? (
                          <span style={{ display: 'inline-block', animation: 'spin 1.2s linear infinite' }}>◐</span>
                        ) : (
                          STATUS_ICON[ps.status]
                        )}
                      </span>

                      {/* Phase name + objective */}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: '0.8rem', fontWeight: 600, color: STATUS_COLOR[ps.status] }}>
                          Phase {num} — {ps.def.name}
                        </div>
                        <div style={{ fontSize: '0.68rem', color: '#94A3B8', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {ps.def.objective}
                        </div>
                      </div>

                      {/* Per-phase tokens (when done) */}
                      {ps.status === 'pass' && (ps.input_tokens > 0 || ps.output_tokens > 0) && (
                        <div style={{ fontSize: '0.6rem', color: '#64748B', textAlign: 'right', flexShrink: 0 }}>
                          <div>{ps.input_tokens.toLocaleString()} in</div>
                          <div>{ps.output_tokens.toLocaleString()} out</div>
                          {phaseElapsed && <div>{phaseElapsed}</div>}
                        </div>
                      )}

                      {/* Expand chevron */}
                      <span style={{ color: '#475569', fontSize: '0.65rem', flexShrink: 0, transition: 'transform 0.15s', transform: isExp ? 'rotate(180deg)' : 'rotate(0)' }}>▼</span>
                    </div>

                    {/* Expanded deliverables */}
                    {isExp && ps.def.deliverables.length > 0 && (
                      <div style={{ paddingLeft: '40px', paddingRight: '12px', paddingBottom: '8px' }}>
                        {ps.def.deliverables.map((d, i) => (
                          <div key={i} style={{ fontSize: '0.68rem', color: '#94A3B8', paddingTop: '3px', display: 'flex', gap: '6px' }}>
                            <span style={{ color: '#475569' }}>•</span>
                            <span>{d}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {/* ======== Files Panel ======== */}
          {buildFiles.length > 0 && (
            <div style={{ ...cardStyle, padding: '12px 16px' }}>
              <div
                style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer', marginBottom: filesExpanded ? '10px' : 0 }}
                onClick={() => setFilesExpanded(!filesExpanded)}
              >
                <h3 style={{ margin: 0, fontSize: '0.9rem', color: '#F8FAFC' }}>
                  Files ({buildFiles.length})
                </h3>
                <span style={{ color: '#64748B', fontSize: '0.7rem', transition: 'transform 0.2s', transform: filesExpanded ? 'rotate(180deg)' : 'rotate(0)' }}>▼</span>
              </div>
              {filesExpanded && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', maxHeight: '260px', overflowY: 'auto' }} data-testid="build-files-panel">
                  {buildFiles.map((f) => (
                    <div
                      key={f.path}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        padding: '5px 8px',
                        borderRadius: '4px',
                        background: '#0F172A',
                        fontSize: '0.72rem',
                      }}
                    >
                      <span style={{ color: '#64748B', flexShrink: 0 }}>📄</span>
                      <span style={{ color: '#F8FAFC', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.path}</span>
                      <span style={{ color: '#475569', flexShrink: 0, fontSize: '0.65rem' }}>
                        {f.language && <span style={{ marginRight: '6px', color: '#64748B' }}>{f.language}</span>}
                        {f.size_bytes > 0 && `${(f.size_bytes / 1024).toFixed(1)}k`}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          </div>

          {/* ======== RIGHT: Metrics + Activity Feed ======== */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>

            {/* -- Metrics Card -- */}
            <div style={cardStyle}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px', marginBottom: '12px' }}>
                <div style={metricBoxStyle}>
                  <span style={{ fontSize: '0.65rem', color: '#64748B', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Input Tokens</span>
                  <span style={{ fontSize: '1.1rem', fontWeight: 700, color: '#F8FAFC' }}>{totalTokens.input.toLocaleString()}</span>
                </div>
                <div style={metricBoxStyle}>
                  <span style={{ fontSize: '0.65rem', color: '#64748B', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Output Tokens</span>
                  <span style={{ fontSize: '1.1rem', fontWeight: 700, color: '#F8FAFC' }}>{totalTokens.output.toLocaleString()}</span>
                </div>
                <div style={metricBoxStyle}>
                  <span style={{ fontSize: '0.65rem', color: '#64748B', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Est. Cost</span>
                  <span style={{ fontSize: '1.1rem', fontWeight: 700, color: '#22C55E' }}>${estimatedCost.toFixed(4)}</span>
                </div>
                <div style={metricBoxStyle}>
                  <span style={{ fontSize: '0.65rem', color: '#64748B', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Elapsed</span>
                  <span style={{ fontSize: '1.1rem', fontWeight: 700, color: '#F8FAFC' }}>{elapsedStr}</span>
                </div>
                <div style={metricBoxStyle}>
                  <span style={{ fontSize: '0.65rem', color: '#64748B', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Model</span>
                  <span style={{ fontSize: '0.85rem', fontWeight: 600, color: '#A78BFA' }}>{buildModel}</span>
                </div>
                {(build?.loop_count ?? 0) > 0 && (
                  <div style={metricBoxStyle}>
                    <span style={{ fontSize: '0.65rem', color: '#64748B', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Loopbacks</span>
                    <span style={{ fontSize: '1.1rem', fontWeight: 700, color: '#EAB308' }}>{build?.loop_count}</span>
                  </div>
                )}
              </div>

              {/* Context window meter */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.65rem', color: '#94A3B8' }}>
                  <span>Context Window</span>
                  <span>{totalTok.toLocaleString()} / {contextWindow.toLocaleString()} ({ctxPercent.toFixed(1)}%)</span>
                </div>
                <div style={{ width: '100%', height: '8px', background: '#0F172A', borderRadius: '4px', overflow: 'hidden' }}>
                  <div style={{ width: `${ctxPercent}%`, height: '100%', background: ctxColor, borderRadius: '4px', transition: 'width 0.4s ease' }} />
                </div>
              </div>
            </div>

            {/* -- Error banner -- */}
            {build?.error_detail && (
              <div style={{ background: '#7F1D1D', borderRadius: '6px', padding: '10px 16px', fontSize: '0.78rem', color: '#FCA5A5' }}>
                <strong>Error:</strong> {build.error_detail}
              </div>
            )}

            {/* -- Activity Feed -- */}
            <div>
              <h3 style={{ margin: '0 0 8px', fontSize: '0.85rem', color: '#94A3B8' }}>Activity</h3>
              <div style={feedStyle} data-testid="build-activity-feed">
                {activity.length === 0 ? (
                  <div style={{ color: '#475569' }}>Waiting for build output...</div>
                ) : (
                  activity.map((entry, i) => (
                    <div key={i} style={{ color: LEVEL_COLOR[entry.level] ?? LEVEL_COLOR.info }}>
                      <span style={{ color: '#475569' }}>{entry.time}</span>{' '}
                      {entry.message}
                    </div>
                  ))
                )}
                <div ref={feedEndRef} />
              </div>
            </div>
          </div>
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
