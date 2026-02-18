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
import DevConsole, { createInitialSteps, mapEventToSteps, DevStep } from '../components/DevConsole';
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
  target_type: string | null;
  target_ref: string | null;
}

interface PhaseDefinition {
  number: number;
  name: string;
  objective: string;
  deliverables: string[];
}

type PhaseStatus = 'pending' | 'active' | 'pass' | 'fail' | 'paused';

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
  category: 'activity' | 'output';
}

interface PlanTask {
  id: number;
  title: string;
  status: 'pending' | 'done';
}

interface OverviewPhase {
  number: number;
  name: string;
  objective: string;
  status: 'pending' | 'active' | 'passed' | 'failed' | 'paused';
}

interface PauseInfo {
  phase: string;
  loop_count: number;
  audit_findings: string;
  options: string[];
}

interface ManifestFile {
  path: string;
  purpose: string;
  status: 'pending' | 'generating' | 'done' | 'error';
  language: string;
  estimated_lines: number;
  size_bytes?: number;
  tokens_in?: number;
  tokens_out?: number;
  auditStatus?: 'pending' | 'pass' | 'fail' | 'fixing' | 'fixed';
  auditFindings?: string;
}

interface VerificationResult {
  syntax_errors: number;
  tests_passed: number;
  tests_failed: number;
  fixes_applied: number;
  test_output?: string;
}

interface GovernanceCheck {
  code: string;
  name: string;
  result: 'PASS' | 'FAIL' | 'WARN';
  detail: string;
  icon?: string;
  phase?: string;
}

interface GovernanceResult {
  passed: boolean;
  checks: GovernanceCheck[];
  blocking_failures: number;
  warnings: number;
}

/* -- Phase 45: Cognitive Dashboard types -- */

interface ReconData {
  total_files: number;
  total_lines: number;
  test_count: number;
  symbols_count: number;
  tables: string[];
}

interface DAGTask {
  id: string;
  title: string;
  file_path: string | null;
  status: 'pending' | 'in_progress' | 'completed' | 'failed' | 'blocked' | 'skipped';
  depends_on: string[];
}

interface DAGProgressData {
  total: number;
  completed: number;
  failed: number;
  blocked: number;
  in_progress: number;
  pending: number;
  skipped: number;
  percentage: number;
}

interface InvariantEntry {
  passed: boolean;
  expected: number;
  actual: number;
  constraint: string;
}

interface JournalEvent {
  timestamp: string;
  message: string;
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

/* ------------------------------------------------------------------ */
/*  Slash command definitions                                         */
/* ------------------------------------------------------------------ */

const SLASH_COMMANDS: { cmd: string; icon: string; desc: string; color: string }[] = [
  { cmd: '/stop',     icon: '\u23F9',        desc: 'Cancel the build immediately',            color: '#DC2626' },
  { cmd: '/start',    icon: '\u25B6',        desc: 'Resume or start build (optionally: /start phase N)', color: '#16A34A' },
  { cmd: '/status',   icon: '\uD83D\uDCCA',  desc: 'LLM-generated build status summary',     color: '#0891B2' },
  { cmd: '/status',   icon: '\uD83D\uDCCA',  desc: 'LLM-generated build status summary',     color: '#0891B2' },
  { cmd: '/pause',    icon: '\u23F8',        desc: 'Pause after the current file',            color: '#F59E0B' },
  { cmd: '/push',     icon: '\uD83D\uDE80',  desc: 'Push to GitHub (commits first)',          color: '#EA580C' },
  { cmd: '/compact',  icon: '\u267B',        desc: 'Compact context before next file',        color: '#7C3AED' },
  { cmd: '/commit',   icon: '\uD83D\uDCE4',  desc: 'Git add, commit, and push',               color: '#D97706' },
  { cmd: '/clear',    icon: '\u26A1',        desc: 'Stop and restart with fresh context',     color: '#0EA5E9' },
  { cmd: '/verify',   icon: '\uD83D\uDD0D',  desc: 'Run verification (syntax + tests)',        color: '#8B5CF6' },
  { cmd: '/pull',     icon: '\u2B07',        desc: 'Pull from GitHub and continue',             color: '#0D9488' },
];

/* ------------------------------------------------------------------ */
/*  ActivityLine — renders a single activity entry, collapsible if long */
/* ------------------------------------------------------------------ */

function ActivityLine({ entry, isLong }: { entry: ActivityEntry; isLong: boolean }) {
  const [expanded, setExpanded] = useState(false);

  if (!isLong) {
    return (
      <div style={{ color: LEVEL_COLOR[entry.level] ?? LEVEL_COLOR.info, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
        <span style={{ color: '#475569' }}>{entry.time}</span>{' '}
        {entry.message}
      </div>
    );
  }

  // Long messages: show first 200 chars collapsed, full text expanded
  const preview = entry.message.slice(0, 200) + '…';
  return (
    <div style={{ color: LEVEL_COLOR[entry.level] ?? LEVEL_COLOR.info, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
      <span style={{ color: '#475569' }}>{entry.time}</span>{' '}
      {expanded ? entry.message : preview}
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          background: 'none',
          border: 'none',
          color: '#3B82F6',
          cursor: 'pointer',
          fontSize: '0.7rem',
          marginLeft: '6px',
          padding: 0,
          fontFamily: 'inherit',
        }}
      >
        {expanded ? '▲ Show less' : '▼ Show more'}
      </button>
    </div>
  );
}

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
  const [contextWindowTokens, setContextWindowTokens] = useState({ input: 0, output: 0 });
  const [loading, setLoading] = useState(true);
  const [noBuild, setNoBuild] = useState(false);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const [showForceCancelConfirm, setShowForceCancelConfirm] = useState(false);

  const [planTasks, setPlanTasks] = useState<PlanTask[]>([]);
  const [planExpanded, setPlanExpanded] = useState(true);
  const [expandedFile, setExpandedFile] = useState<string | null>(null);
  const [fileContentCache, setFileContentCache] = useState<Map<string, string>>(new Map());
  const [fileContentLoading, setFileContentLoading] = useState(false);
  const [expandedChip, setExpandedChip] = useState<number | null>(null);
  const [chipGridHeight, setChipGridHeight] = useState<number | null>(null);
  const chipGridRef = useRef<HTMLDivElement>(null);
  const [overviewPhases, setOverviewPhases] = useState<OverviewPhase[]>([]);
  const [currentPhaseName, setCurrentPhaseName] = useState('');
  const [turnCount, setTurnCount] = useState(0);
  const [startTime] = useState(() => Date.now());
  const [elapsed, setElapsed] = useState(0);
  const [pauseInfo, setPauseInfo] = useState<PauseInfo | null>(null);
  const [showPauseModal, setShowPauseModal] = useState(false);
  const [interjectionText, setInterjectionText] = useState('');
  const [sendingInterject, setSendingInterject] = useState(false);
  const [queuedInterjections, setQueuedInterjections] = useState<{id: number; text: string; time: string; status: 'pending' | 'delivered'}[]>([]);
  const queueIdRef = useRef(0);
  const [slashMenuIdx, setSlashMenuIdx] = useState(0);
  const interjRef = useRef<HTMLInputElement>(null);
  const [resuming, setResuming] = useState(false);
  const [logSearch, setLogSearch] = useState('');
  const [activityTab, setActivityTab] = useState<'activity' | 'output'>('activity');
  const [devConsoleOpen, setDevConsoleOpen] = useState(false);
  const [devSteps, setDevSteps] = useState<DevStep[]>(createInitialSteps);
  const [manifestFiles, setManifestFiles] = useState<ManifestFile[]>([]);
  const [manifestExpanded, setManifestExpanded] = useState(true);
  const [expandedPhase, setExpandedPhase] = useState<number | null>(null);
  const [verification, setVerification] = useState<VerificationResult | null>(null);
  const [verificationExpanded, setVerificationExpanded] = useState(false);
  const [governance, setGovernance] = useState<GovernanceResult | null>(null);
  const [governanceExpanded, setGovernanceExpanded] = useState(false);
  const [activityStatus, setActivityStatus] = useState<string>('');
  /* Cost gate / circuit breaker state (Phase 35) */
  const [liveCost, setLiveCost] = useState({ total_cost_usd: 0, api_calls: 0, tokens_in: 0, tokens_out: 0, spend_cap: null as number | null, pct_used: 0 });
  const [costWarning, setCostWarning] = useState<string | null>(null);
  const [costExceeded, setCostExceeded] = useState<string | null>(null);
  const [circuitBreaking, setCircuitBreaking] = useState(false);
  /* Phase 45: Cognitive Dashboard state */
  const [reconData, setReconData] = useState<ReconData | null>(null);
  const [dagTasks, setDagTasks] = useState<DAGTask[]>([]);
  const [dagProgress, setDagProgress] = useState<DAGProgressData | null>(null);
  const [dagExpanded, setDagExpanded] = useState(false);
  const [invariants, setInvariants] = useState<Map<string, InvariantEntry>>(new Map());
  const [journalEvents, setJournalEvents] = useState<JournalEvent[]>([]);
  const [journalExpanded, setJournalExpanded] = useState(false);
  const [compactionCount, setCompactionCount] = useState(0);
  const feedEndRef = useRef<HTMLDivElement>(null);
  const feedContainerRef = useRef<HTMLDivElement>(null);
  const userScrolledUp = useRef(false);
  const phaseStartRef = useRef<number>(Date.now());
  const expandedFileRef = useRef<string | null>(null);

  // Keep ref in sync with state for use in WS callback
  expandedFileRef.current = expandedFile;

  /* ------ helpers ------ */

  const addActivity = useCallback((msg: string, level: ActivityEntry['level'] = 'info', category: ActivityEntry['category'] = 'activity') => {
    const time = new Date().toLocaleTimeString('en-GB', { hour12: false });
    setActivity((prev) => [...prev, { time, message: msg, level, category }]);
  }, []);

  const parsePhaseNum = (phaseStr: string): number => {
    const m = phaseStr.match(/\d+/);
    return m ? parseInt(m[0], 10) : 0;
  };

  /* ------ filtered activity for search + tab ------ */
  const filteredActivity = (() => {
    let items = activity.filter((e) => e.category === activityTab);
    if (logSearch) items = items.filter((e) => e.message.toLowerCase().includes(logSearch.toLowerCase()));
    return items;
  })();

  /* ------ auto-scroll feed (only when user is near bottom) ------ */
  useEffect(() => {
    if (userScrolledUp.current) return;
    const el = feedContainerRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [activity]);

  const handleFeedScroll = useCallback(() => {
    const el = feedContainerRef.current;
    if (!el) return;
    // If user is within 60px of bottom, consider them "at bottom"
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    userScrolledUp.current = !atBottom;
  }, []);

  /* ------ elapsed timer ------ */
  useEffect(() => {
    if (!build || !['pending', 'running', 'paused'].includes(build.status)) return;
    const ref = build.started_at ? new Date(build.started_at).getTime() : startTime;
    const iv = setInterval(() => setElapsed(Math.floor((Date.now() - ref) / 1000)), 1000);
    return () => clearInterval(iv);
  }, [build, startTime]);

  /* ------ fetch initial data ------ */
  useEffect(() => {
    const ac = new AbortController();
    const load = async () => {
      try {
        const hdr = { Authorization: `Bearer ${token}` };
        const sig = ac.signal;
        const [statusRes, phasesRes, logsRes] = await Promise.all([
          fetch(`${API_BASE}/projects/${projectId}/build/status`, {
            headers: hdr, signal: sig,
          }),
          fetch(`${API_BASE}/projects/${projectId}/build/phases`, {
            headers: hdr, signal: sig,
          }),
          fetch(`${API_BASE}/projects/${projectId}/build/logs?limit=500`, {
            headers: hdr, signal: sig,
          }),
        ]);

        if (ac.signal.aborted) return;

        if (statusRes.status === 400) {
          setNoBuild(true);
          setLoading(false);
          return;
        }

        let buildData: BuildStatus | null = null;
        if (statusRes.ok) {
          buildData = await statusRes.json();
          setBuild(buildData);

          /* Seed activity from historical logs */
          if (logsRes.ok) {
            const logData = await logsRes.json();
            const items = (logData.items ?? []) as {
              timestamp: string;
              message: string;
              level: string;
              source?: string;
            }[];
            setActivity(
              items.map((l) => {
                const src = l.source ?? '';
                const cat: ActivityEntry['category'] =
                  src === 'file' || src === 'builder' ? 'output' : 'activity';
                return {
                  time: new Date(l.timestamp).toLocaleTimeString('en-GB', { hour12: false }),
                  message: l.message,
                  level: (l.level ?? 'info') as ActivityEntry['level'],
                  category: cat,
                };
              }),
            );
          }

          /* Seed token totals from cost summary */
          try {
            const costRes = await fetch(`${API_BASE}/projects/${projectId}/build/summary`, {
              headers: hdr, signal: sig,
            });
            if (!ac.signal.aborted && costRes.ok) {
              const costData = await costRes.json();
              setTotalTokens({
                input: costData.cost?.total_input_tokens ?? 0,
                output: costData.cost?.total_output_tokens ?? 0,
              });
            }
          } catch { /* best effort */ }

          /* Seed file manifest from already-written files (survives refresh) */
          try {
            const filesRes = await fetch(`${API_BASE}/projects/${projectId}/build/files`, {
              headers: hdr, signal: sig,
            });
            if (!ac.signal.aborted && filesRes.ok) {
              const filesData = await filesRes.json();
              const items = (filesData.items ?? []) as { path: string; size_bytes: number; language: string }[];
              if (items.length > 0) {
                setManifestFiles(items.map((f) => ({
                  path: f.path,
                  purpose: '',
                  status: 'done' as const,
                  language: f.language || '',
                  estimated_lines: 0,
                  size_bytes: f.size_bytes,
                })));
              }
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
          const currentPhaseNum = buildData ? parsePhaseNum(buildData.phase) : 0;

          const map = new Map<number, PhaseState>();
          for (const def of defs) {
            let status: PhaseStatus = 'pending';
            if (buildData) {
              if (def.number < currentPhaseNum) status = 'pass';
              else if (def.number === currentPhaseNum) {
                if (buildData.status === 'completed') status = 'pass';
                else if (buildData.status === 'failed') status = 'fail';
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

          /* Seed overview phases grid (survives refresh) */
          if (buildData && defs.length > 0) {
            setOverviewPhases(defs.map((d) => {
              let ovStatus: OverviewPhase['status'] = 'pending';
              if (d.number < currentPhaseNum) ovStatus = 'passed';
              else if (d.number === currentPhaseNum) {
                if (buildData!.status === 'completed') ovStatus = 'passed';
                else if (buildData!.status === 'failed') ovStatus = 'failed';
                else if (buildData!.status === 'paused') ovStatus = 'paused';
                else ovStatus = 'active';
              }
              return { number: d.number, name: d.name, objective: d.objective, status: ovStatus };
            }));
          }
        }
      } catch {
        if (!ac.signal.aborted) addToast('Network error loading build');
      } finally {
        if (!ac.signal.aborted) setLoading(false);
      }
    };
    load();
    return () => ac.abort();
  }, [projectId, token, addToast]);

  /* ------ WebSocket handler ------ */
  useWebSocket(
    useCallback(
      (data) => {
        const payload = data.payload as Record<string, unknown>;
        const eventPid = payload.project_id as string;
        if (eventPid && eventPid !== projectId) return;

        /* Update DevConsole steps for every event */
        const stepUpdates = mapEventToSteps(data.type, payload);
        if (stepUpdates.length > 0) {
          const now = new Date().toISOString();
          setDevSteps((prev) => {
            const next = [...prev];
            for (const upd of stepUpdates) {
              const idx = next.findIndex((s) => s.id === upd.id);
              if (idx >= 0) {
                next[idx] = {
                  ...next[idx],
                  status: upd.status,
                  detail: upd.detail ?? next[idx].detail,
                  startedAt: upd.status === 'active' && !next[idx].startedAt ? now : next[idx].startedAt,
                  completedAt: upd.status === 'done' || upd.status === 'error' ? now : next[idx].completedAt,
                };
              }
            }
            return next;
          });
        }

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
            const src = (payload.source ?? '') as string;
            const cat: ActivityEntry['category'] =
              src === 'file' || src === 'builder' ? 'output' : 'activity';
            if (msg) addActivity(msg, lvl, cat);
            /* Phase 45: capture journal checkpoint events */
            if (src === 'journal_checkpoint') {
              setJournalEvents((prev) => [...prev, {
                timestamp: new Date().toLocaleTimeString('en-GB', { hour12: false }),
                message: msg,
              }]);
            }
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

            /* Also update overview phases */
            setOverviewPhases((prev) =>
              prev.map((p) => ({
                ...p,
                status: p.number === phaseNum ? 'passed' as const
                  : p.number === phaseNum + 1 && p.status === 'pending' ? 'active' as const
                  : p.status,
              })),
            );

            /* Reset manifest for next phase */
            setManifestFiles([]);
            setVerification(null);
            setGovernance(null);

            setBuild((prev) => prev ? { ...prev, phase: phase } : prev);
            phaseStartRef.current = Date.now();
            break;
          }

          case 'context_reset': {
            /* Plan-execute mode: each phase is independent, so reset the
               context window bar.  Total tokens (cost) are NOT reset —
               those track cumulative spend. */
            const droppedCount = (payload.dropped ?? 0) as number;
            setContextWindowTokens({ input: 0, output: 0 });
            addActivity(`Context reset — cleared ${droppedCount} cached files`, 'system');
            break;
          }

          case 'phase_transition': {
            const completed = payload.completed_phase as string;
            const nextPhase = payload.next_phase as string;
            const nextName = payload.next_phase_name as string;
            const nextObj = (payload.next_phase_objective ?? '') as string;
            addActivity(
              `🔄 ${completed} complete → ${nextPhase}: ${nextName}${nextObj ? ` — ${nextObj}` : ''}`,
              'system',
            );
            break;
          }

          case 'build_complete': {
            setBuild((prev) => prev ? { ...prev, status: 'completed' } : prev);
            setActivityStatus('');
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
            setActivityStatus('');
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
            setActivityStatus('');
            addActivity('Build cancelled by user', 'warn');
            break;
          }

          case 'audit_pass': {
            const phase = payload.phase as string;
            const auditPassNum = parsePhaseNum(phase);
            addActivity(`Audit PASS for ${phase}`, 'system');
            // Mark phase as passed in overview
            setOverviewPhases((prev) =>
              prev.map((p) => (p.number === auditPassNum ? { ...p, status: 'passed' as const } : p)),
            );
            break;
          }

          case 'audit_fail': {
            const phase = payload.phase as string;
            const loop = payload.loop_count as number;
            const auditFailNum = parsePhaseNum(phase);
            addActivity(`Audit FAIL for ${phase} (loop ${loop})`, 'warn');
            // Mark phase as failed in overview
            setOverviewPhases((prev) =>
              prev.map((p) => (p.number === auditFailNum ? { ...p, status: 'failed' as const } : p)),
            );
            break;
          }

          case 'recovery_plan': {
            const planPhase = payload.phase as string;
            const planText = (payload.plan_text ?? '') as string;
            if (planText) {
              addActivity(
                `Recovery plan for ${planPhase}:\n${planText}`,
                'warn',
              );
            }
            break;
          }

          case 'tool_use': {
            const toolName = (payload.tool_name ?? '') as string;
            const inputSummary = (payload.input_summary ?? '') as string;
            const resultSummary = (payload.result_summary ?? '') as string;
            addActivity(
              `🔧 ${toolName}(${inputSummary.slice(0, 80)}) → ${resultSummary.slice(0, 120)}`,
              'info',
            );
            break;
          }

          case 'test_run': {
            const testCmd = (payload.command ?? '') as string;
            const testPassed = payload.passed as boolean;
            const testSummary = (payload.summary ?? '') as string;
            const testExitCode = (payload.exit_code ?? -1) as number;
            addActivity(
              `${testPassed ? '✅' : '❌'} Tests ${testPassed ? 'PASS' : 'FAIL'}: ${testCmd} (exit ${testExitCode})${testSummary ? '\n' + testSummary.slice(0, 200) : ''}`,
              testPassed ? 'info' : 'warn',
            );
            break;
          }

          case 'file_created': {
            const filePath = (payload.path ?? '') as string;
            const sizeBytes = (payload.size_bytes ?? 0) as number;
            if (filePath) {
              addActivity(`File created: ${filePath} (${sizeBytes} bytes)`, 'info', 'output');
            }
            break;
          }

          /* --- Plan-Execute Architecture Events (Phase 21) --- */

          case 'file_manifest': {
            const files = (payload.files ?? []) as ManifestFile[];
            const phase = (payload.phase ?? '') as string;
            setManifestFiles(files.map((f) => ({ ...f, status: 'pending' })));
            setExpandedFile(null);
            setFileContentCache(new Map());
            addActivity(`File manifest for ${phase}: ${files.length} files`, 'system');
            break;
          }

          case 'file_generating': {
            const genPath = (payload.path ?? '') as string;
            if (genPath) {
              setManifestFiles((prev) => {
                const exists = prev.some((f) => f.path === genPath);
                if (exists) return prev.map((f) => (f.path === genPath ? { ...f, status: 'generating' as const } : f));
                return [...prev, { path: genPath, purpose: '', status: 'generating' as const, language: '', estimated_lines: 0 }];
              });
              addActivity(`Generating: ${genPath}`, 'info', 'output');
            }
            break;
          }

          case 'file_generated': {
            const genPath = (payload.path ?? '') as string;
            const sizeBytes = (payload.size_bytes ?? 0) as number;
            const tokensIn = (payload.tokens_in ?? 0) as number;
            const tokensOut = (payload.tokens_out ?? 0) as number;
            if (genPath) {
              setManifestFiles((prev) => {
                const exists = prev.some((f) => f.path === genPath);
                const updated = exists
                  ? prev.map((f) =>
                      f.path === genPath
                        ? { ...f, status: 'done' as const, size_bytes: sizeBytes, tokens_in: tokensIn, tokens_out: tokensOut, auditStatus: 'pending' as const }
                        : f,
                    )
                  : [...prev, { path: genPath, purpose: '', status: 'done' as const, language: '', estimated_lines: 0, size_bytes: sizeBytes, tokens_in: tokensIn, tokens_out: tokensOut, auditStatus: 'pending' as const }];
                return updated;
              });
              setTotalTokens((prev) => ({
                input: prev.input + tokensIn,
                output: prev.output + tokensOut,
              }));
              // Context window tracks the peak single-call usage (each file is independent)
              setContextWindowTokens((prev) => {
                const newTotal = tokensIn + tokensOut;
                const prevTotal = prev.input + prev.output;
                return newTotal > prevTotal ? { input: tokensIn, output: tokensOut } : prev;
              });
              addActivity(`✓ ${genPath} — generated (${sizeBytes} bytes, ${tokensIn + tokensOut} tokens)`, 'info', 'output');
              // Auto-fetch content if this file is currently expanded
              if (expandedFileRef.current === genPath) {
                fetch(`${API_BASE}/projects/${projectId}/build/files/${encodeURIComponent(genPath)}`, {
                  headers: { Authorization: `Bearer ${token}` },
                })
                  .then((res) => (res.ok ? res.json() : null))
                  .then((data) => {
                    if (data?.content) {
                      setFileContentCache((prev) => new Map(prev).set(genPath, data.content));
                    }
                  })
                  .catch(() => {});
              }
            }
            break;
          }

          case 'file_audited': {
            const auditPath = (payload.path ?? '') as string;
            const verdict = (payload.verdict ?? 'PASS') as string;
            const findings = (payload.findings ?? '') as string;
            const durationMs = (payload.duration_ms ?? 0) as number;
            if (auditPath) {
              setManifestFiles((prev) =>
                prev.map((f) =>
                  f.path === auditPath
                    ? {
                        ...f,
                        auditStatus: verdict === 'PASS' ? ('pass' as const) : ('fail' as const),
                        auditFindings: findings || undefined,
                      }
                    : f,
                ),
              );
              if (verdict === 'PASS') {
                addActivity(`✓✓ ${auditPath} — audited (pass, ${durationMs}ms)`, 'info');
              } else {
                addActivity(`✗ ${auditPath} — audited (FAIL, ${durationMs}ms)`, 'warn');
              }
            }
            break;
          }

          case 'file_fixing': {
            const fixPath = (payload.path ?? '') as string;
            const fixer = (payload.fixer ?? 'auditor') as string;
            if (fixPath) {
              setManifestFiles((prev) =>
                prev.map((f) =>
                  f.path === fixPath
                    ? { ...f, auditStatus: 'fixing' as const }
                    : f,
                ),
              );
              addActivity(`🔧 ${fixPath} — ${fixer} fixing...`, 'warn');
            }
            break;
          }

          case 'file_fixed': {
            const fixedPath = (payload.path ?? '') as string;
            const fixer = (payload.fixer ?? 'auditor') as string;
            const rounds = (payload.rounds ?? 1) as number;
            if (fixedPath) {
              setManifestFiles((prev) =>
                prev.map((f) =>
                  f.path === fixedPath
                    ? { ...f, auditStatus: 'fixed' as const, auditFindings: undefined }
                    : f,
                ),
              );
              addActivity(`✓ ${fixedPath} — fixed by ${fixer} (${rounds} round${rounds > 1 ? 's' : ''})`, 'info');
            }
            break;
          }

          case 'build_activity_status': {
            const status = (payload.status ?? '') as string;
            setActivityStatus(status);
            break;
          }

          case 'verification_result': {
            const result: VerificationResult = {
              syntax_errors: (payload.syntax_errors ?? 0) as number,
              tests_passed: (payload.tests_passed ?? 0) as number,
              tests_failed: (payload.tests_failed ?? 0) as number,
              fixes_applied: (payload.fixes_applied ?? 0) as number,
              test_output: (payload.test_output ?? '') as string,
            };
            setVerification(result);
            setVerificationExpanded(false);
            const parts = [];
            if (result.syntax_errors) parts.push(`${result.syntax_errors} syntax errors`);
            if (result.tests_passed) parts.push(`${result.tests_passed} tests passed`);
            if (result.tests_failed) parts.push(`${result.tests_failed} tests failed`);
            if (result.fixes_applied) parts.push(`${result.fixes_applied} fixes applied`);
            addActivity(`Verification: ${parts.join(', ') || 'clean'}`, result.syntax_errors || result.tests_failed ? 'warn' : 'system');
            break;
          }

          case 'governance_check': {
            const code = (payload.code ?? '') as string;
            const name = (payload.name ?? '') as string;
            const result = (payload.result ?? '') as string;
            const icon = result === 'PASS' ? '✅' : result === 'FAIL' ? '❌' : '⚠️';
            addActivity(`${icon} ${code}: ${name} — ${result}`, result === 'FAIL' ? 'error' : result === 'WARN' ? 'warn' : 'system');
            break;
          }

          case 'governance_pass':
          case 'governance_fail': {
            const checks = (payload.checks ?? []) as GovernanceCheck[];
            const passed = Boolean(payload.passed);
            const blocking = (payload.blocking_failures ?? 0) as number;
            const warnings = (payload.warnings ?? 0) as number;
            setGovernance({ passed, checks, blocking_failures: blocking, warnings });
            setGovernanceExpanded(!passed);
            const summary = `Governance: ${checks.length - blocking - warnings} pass, ${blocking} fail, ${warnings} warn`;
            addActivity(summary, passed ? 'system' : 'warn');
            break;
          }

          case 'build_overview': {
            const phases = (payload.phases ?? []) as OverviewPhase[];
            if (phases.length > 0) {
              setOverviewPhases(phases.map((p, i) => ({ ...p, status: i === 0 ? 'active' : 'pending' })));
              addActivity(`Build overview: ${phases.length} phases`, 'system');
            }
            break;
          }

          case 'build_plan':
          case 'phase_plan': {
            const tasks = (payload.tasks ?? []) as PlanTask[];
            const phase = (payload.phase ?? '') as string;
            if (tasks.length > 0) {
              setPlanTasks(tasks);
              addActivity(`Phase plan${phase ? ` (${phase})` : ''}: ${tasks.length} tasks`, 'system');
            }
            // Update overview bar + phase states active phase
            if (phase) {
              const planPhaseNum = parsePhaseNum(phase);
              setCurrentPhaseName(phase);
              setOverviewPhases((prev) =>
                prev.map((p) => ({
                  ...p,
                  status: p.number === planPhaseNum ? 'active' : p.status === 'active' ? 'pending' : p.status,
                })),
              );
              /* Also sync the left-column phase states */
              setPhaseStates((prev) => {
                const next = new Map(prev);
                for (const [num, ps] of next) {
                  if (num === planPhaseNum && ps.status !== 'pass') {
                    next.set(num, { ...ps, status: 'active' });
                  } else if (ps.status === 'active' && num !== planPhaseNum) {
                    next.set(num, { ...ps, status: 'pending' });
                  }
                }
                return next;
              });
            }
            break;
          }

          case 'plan_task_complete': {
            const taskId = payload.task_id as number;
            setPlanTasks((prev) =>
              prev.map((t) => (t.id === taskId ? { ...t, status: 'done' as const } : t)),
            );
            const task = planTasks.find((t) => t.id === taskId);
            if (task) addActivity(`Task ${taskId} done: ${task.title}`, 'system');
            break;
          }

          case 'build_turn': {
            const turn = (payload.turn ?? 0) as number;
            const compacted = payload.compacted as boolean;
            setTurnCount(turn);
            if (compacted) {
              setCompactionCount((c) => c + 1);
              addActivity(`🔄 Context compacted at turn ${turn}`, 'warn');
            }
            break;
          }

          case 'token_update': {
            const inTok = (payload.input_tokens ?? 0) as number;
            const outTok = (payload.output_tokens ?? 0) as number;
            setTotalTokens({ input: inTok, output: outTok });
            setContextWindowTokens({ input: inTok, output: outTok });
            break;
          }

          case 'build_paused': {
            const phase = payload.phase as string;
            const loop = (payload.loop_count ?? 0) as number;
            const findings = (payload.audit_findings ?? '') as string;
            const options = (payload.options ?? ['retry', 'skip', 'abort']) as string[];
            setBuild((prev) => prev ? { ...prev, status: 'paused' } : prev);
            setPauseInfo({ phase, loop_count: loop, audit_findings: findings, options });
            setShowPauseModal(true);
            addActivity(`Build paused on ${phase} after ${loop} failures`, 'warn');

            /* Mark current active phase as paused */
            const pausePhaseNum = parsePhaseNum(phase);
            setPhaseStates((prev) => {
              const next = new Map(prev);
              const ps = next.get(pausePhaseNum);
              if (ps) next.set(pausePhaseNum, { ...ps, status: 'paused' });
              return next;
            });
            setOverviewPhases((prev) =>
              prev.map((p) => (p.number === pausePhaseNum ? { ...p, status: 'paused' as const } : p)),
            );
            break;
          }

          case 'build_resumed': {
            const action = (payload.action ?? 'retry') as string;
            const phase = (payload.phase ?? '') as string;
            setBuild((prev) => prev ? { ...prev, status: 'running' } : prev);
            setPauseInfo(null);
            setShowPauseModal(false);
            addActivity(`Build resumed (${action}) on ${phase}`, 'system');

            /* Mark paused phase back to active */
            const resumePhaseNum = parsePhaseNum(phase);
            setPhaseStates((prev) => {
              const next = new Map(prev);
              const ps = next.get(resumePhaseNum);
              if (ps && ps.status === 'paused') next.set(resumePhaseNum, { ...ps, status: 'active' });
              return next;
            });
            break;
          }

          case 'build_interjection': {
            const msg = (payload.message ?? '') as string;
            addActivity(`Interjection delivered: ${msg.slice(0, 100)}`, 'system');
            // Mark the first pending interjection as delivered
            setQueuedInterjections((prev) => {
              const idx = prev.findIndex((q) => q.status === 'pending');
              if (idx < 0) return prev;
              const next = [...prev];
              next[idx] = { ...next[idx], status: 'delivered' };
              // Remove delivered items after 3 seconds
              setTimeout(() => {
                setQueuedInterjections((p) => p.filter((q) => q.id !== next[idx].id));
              }, 3000);
              return next;
            });
            break;
          }

          case 'cost_ticker': {
            setLiveCost({
              total_cost_usd: (payload.total_cost_usd ?? 0) as number,
              api_calls: (payload.api_calls ?? 0) as number,
              tokens_in: (payload.tokens_in ?? 0) as number,
              tokens_out: (payload.tokens_out ?? 0) as number,
              spend_cap: (payload.spend_cap ?? null) as number | null,
              pct_used: (payload.pct_used ?? 0) as number,
            });
            break;
          }

          case 'cost_warning': {
            const msg = (payload.message ?? '') as string;
            setCostWarning(msg);
            addActivity(`⚠️ Cost warning: ${msg}`, 'warn');
            break;
          }

          case 'cost_exceeded': {
            const msg = (payload.message ?? '') as string;
            setCostExceeded(msg);
            addActivity(`🛑 Cost exceeded: ${msg}`, 'error');
            break;
          }

          /* ---- Phase 45: Cognitive Dashboard events ---- */

          case 'recon_complete': {
            setReconData({
              total_files: (payload.total_files ?? 0) as number,
              total_lines: (payload.total_lines ?? 0) as number,
              test_count: (payload.test_count ?? 0) as number,
              symbols_count: (payload.symbols_count ?? 0) as number,
              tables: (payload.tables ?? []) as string[],
            });
            break;
          }

          case 'dag_initialized': {
            const dag = payload.dag as Record<string, unknown> | undefined;
            if (dag && Array.isArray(dag.nodes)) {
              const tasks: DAGTask[] = (dag.nodes as Record<string, unknown>[]).map((n) => ({
                id: (n.id ?? '') as string,
                title: (n.title ?? '') as string,
                file_path: (n.file_path ?? null) as string | null,
                status: (n.status ?? 'pending') as DAGTask['status'],
                depends_on: (n.depends_on ?? []) as string[],
              }));
              setDagTasks(tasks);
              setDagExpanded(true);
            }
            break;
          }

          case 'task_started': {
            const tid = (payload.task_id ?? '') as string;
            setDagTasks((prev) => prev.map((t) =>
              t.id === tid ? { ...t, status: 'in_progress' as const } : t,
            ));
            break;
          }

          case 'task_completed': {
            const tid = (payload.task_id ?? '') as string;
            setDagTasks((prev) => prev.map((t) =>
              t.id === tid ? { ...t, status: 'completed' as const } : t,
            ));
            break;
          }

          case 'task_failed': {
            const tid = (payload.task_id ?? '') as string;
            setDagTasks((prev) => prev.map((t) =>
              t.id === tid ? { ...t, status: 'failed' as const } : t,
            ));
            break;
          }

          case 'dag_progress': {
            setDagProgress({
              total: (payload.total ?? 0) as number,
              completed: (payload.completed ?? 0) as number,
              failed: (payload.failed ?? 0) as number,
              blocked: (payload.blocked ?? 0) as number,
              in_progress: (payload.in_progress ?? 0) as number,
              pending: (payload.pending ?? 0) as number,
              skipped: (payload.skipped ?? 0) as number,
              percentage: (payload.percentage ?? 0) as number,
            });
            break;
          }

          case 'invariant_check': {
            const name = (payload.name ?? '') as string;
            if (name) {
              setInvariants((prev) => {
                const next = new Map(prev);
                next.set(name, {
                  passed: Boolean(payload.passed),
                  expected: (payload.expected ?? 0) as number,
                  actual: (payload.actual ?? 0) as number,
                  constraint: (payload.constraint ?? '') as string,
                });
                return next;
              });
              if (!payload.passed) {
                addActivity(`❌ Invariant violation: ${name} (expected ${payload.expected}, got ${payload.actual})`, 'error');
              }
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

  const handleForceCancel = async () => {
    try {
      const res = await fetch(`${API_BASE}/projects/${projectId}/build/force-cancel`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const updated = await res.json();
        setBuild(updated);
        addToast('Build force-cancelled', 'info');
      } else {
        addToast('Failed to force-cancel build');
      }
    } catch {
      addToast('Network error force-cancelling build');
    }
    setShowForceCancelConfirm(false);
  };

  const handleCircuitBreak = async () => {
    setCircuitBreaking(true);
    try {
      const res = await fetch(`${API_BASE}/projects/${projectId}/build/circuit-break`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const updated = await res.json();
        setBuild(updated);
        addToast('Circuit breaker activated — all API calls halted', 'info');
      } else {
        addToast('Failed to activate circuit breaker');
      }
    } catch {
      addToast('Network error activating circuit breaker');
    }
    setCircuitBreaking(false);
  };

  const handleRetryBuild = async () => {
    setDevSteps(createInitialSteps());
    setDevSteps((prev) =>
      prev.map((s) =>
        s.id === 'build_request' ? { ...s, status: 'active' as const, startedAt: new Date().toISOString(), detail: 'Retrying...' } : s,
      ),
    );
    try {
      const body = build?.target_type
        ? JSON.stringify({ target_type: build.target_type, target_ref: build.target_ref })
        : undefined;
      const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
      if (body) headers['Content-Type'] = 'application/json';
      const res = await fetch(`${API_BASE}/projects/${projectId}/build`, {
        method: 'POST',
        headers,
        body,
      });
      if (res.ok) {
        const newBuild = await res.json();
        setBuild(newBuild);
        setNoBuild(false);
        setActivity([]);
        setTotalTokens({ input: 0, output: 0 });
        setContextWindowTokens({ input: 0, output: 0 });
        setOverviewPhases([]);
        addToast('Build restarted', 'success');
      } else {
        const data = await res.json().catch(() => ({ detail: 'Failed to retry build' }));
        addToast(data.detail || 'Failed to retry build');
      }
    } catch {
      addToast('Network error retrying build');
    }
  };

  const handleStartBuild = async () => {
    /* Mark first dev step immediately */
    setDevSteps(createInitialSteps());
    setDevSteps((prev) =>
      prev.map((s) =>
        s.id === 'build_request' ? { ...s, status: 'active' as const, startedAt: new Date().toISOString(), detail: 'Sending POST...' } : s,
      ),
    );
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
        setContextWindowTokens({ input: 0, output: 0 });
        addToast('Build started', 'success');
      } else {
        const data = await res.json().catch(() => ({ detail: 'Failed to start build' }));
        addToast(data.detail || 'Failed to start build');
      }
    } catch {
      addToast('Network error starting build');
    }
  };

  const handleResume = async (action: string) => {
    setResuming(true);
    try {
      const res = await fetch(`${API_BASE}/projects/${projectId}/build/resume`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      });
      if (res.ok) {
        setShowPauseModal(false);
        setPauseInfo(null);
        addToast(`Build ${action === 'abort' ? 'aborted' : 'resumed'}`, action === 'abort' ? 'info' : 'success');
      } else {
        const data = await res.json().catch(() => ({ detail: 'Failed to resume' }));
        addToast(data.detail || 'Failed to resume build');
      }
    } catch {
      addToast('Network error resuming build');
    } finally {
      setResuming(false);
    }
  };

  /* -- Slash command autocomplete -- */
  const slashPrefix = interjectionText.trim().toLowerCase();
  const showSlashMenu = slashPrefix.startsWith('/') && slashPrefix.length < 10;
  const filteredSlash = showSlashMenu
    ? SLASH_COMMANDS.filter((c) => c.cmd.startsWith(slashPrefix) && c.cmd !== slashPrefix)
    : [];

  const pickSlashCmd = (cmd: string) => {
    setInterjectionText(cmd);
    setSlashMenuIdx(0);
    interjRef.current?.focus();
  };

  const handleSlashKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (filteredSlash.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSlashMenuIdx((p) => (p + 1) % filteredSlash.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSlashMenuIdx((p) => (p - 1 + filteredSlash.length) % filteredSlash.length);
        return;
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && filteredSlash.length > 0 && slashPrefix !== filteredSlash[slashMenuIdx]?.cmd)) {
        e.preventDefault();
        pickSlashCmd(filteredSlash[slashMenuIdx].cmd);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setInterjectionText('');
        return;
      }
    }
    if (e.key === 'Enter') handleInterject();
  };

  const handleInterject = async () => {
    if (!interjectionText.trim()) return;
    const trimmed = interjectionText.trim().toLowerCase();
    setSendingInterject(true);

    // Slash commands route through the same interject endpoint (backend handles routing)
    const isSlashCmd = ['/stop', '/pause', '/start', '/compact', '/clear', '/commit', '/push', '/pull', '/status', '/verify', '/fix', '/continue'].includes(trimmed)
      || trimmed.startsWith('/start ') || trimmed.startsWith('/continue ') || trimmed.startsWith('/verify ') || trimmed.startsWith('/fix ');

    try {
      const res = await fetch(`${API_BASE}/projects/${projectId}/build/interject`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: interjectionText.trim() }),
      });
      if (res.ok) {
        const data = await res.json();
        if (isSlashCmd) {
          const msgs: Record<string, string> = {
            stopped: 'Build stopped',
            pause_requested: 'Pause requested \u2014 will pause after current file',
            resumed: 'Build resumed',
            started: 'Build started',
            continued: 'Build continuing from next phase',
            already_running: 'Build is already running',
            compact_requested: 'Context compaction requested \u2014 will compact before next file',
            cleared: 'Build cleared and restarting \u2014 fresh context',
            pushed: 'Pushed to GitHub',
            verifying: 'Running verification...',
            fix_queued: 'Fix request sent to builder',
            fix_started: 'Targeted fix in progress...',
            pulled: 'Pulled from GitHub — continuing build',
          };
          addToast(msgs[data.status] || data.message || 'Command sent', 'success');
          // Refresh build state after /stop or /start or /continue
          if (['stopped', 'started', 'resumed', 'cleared', 'continued', 'pulled'].includes(data.status)) {
            const bres = await fetch(`${API_BASE}/projects/${projectId}/build/status`, {
              headers: { Authorization: `Bearer ${token}` },
            });
            if (bres.ok) {
              const updated = await bres.json();
              setBuild(updated);
              if (data.status === 'stopped') setNoBuild(false);
            }
          }
        } else {
          addToast('Interjection sent', 'success');
          // Track queued interjection
          const qId = ++queueIdRef.current;
          const qTime = new Date().toLocaleTimeString('en-GB', { hour12: false });
          setQueuedInterjections((prev) => [...prev, { id: qId, text: interjectionText.trim(), time: qTime, status: 'pending' }]);
        }
        setInterjectionText('');
      } else {
        const data = await res.json().catch(() => ({ detail: 'Failed to send' }));
        addToast(data.detail || 'Failed to send');
      }
    } catch {
      addToast('Network error');
    } finally {
      setSendingInterject(false);
    }
  };

  /* ------ derived values ------ */

  const isActive = build && ['pending', 'running', 'paused'].includes(build.status);
  const buildModel = 'claude-opus-4-6';
  const contextWindow = 200_000;
  const ctxTok = contextWindowTokens.input + contextWindowTokens.output;
  const ctxPercent = Math.min(100, (ctxTok / contextWindow) * 100);
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
      <style>{`
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
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
                  background: build.status === 'completed' ? '#14532D' : build.status === 'failed' ? '#7F1D1D' : build.status === 'paused' ? '#78350F' : '#1E3A5F',
                  color: build.status === 'completed' ? '#22C55E' : build.status === 'failed' ? '#EF4444' : build.status === 'paused' ? '#F59E0B' : '#2563EB',
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
            {/* Dev Console toggle */}
            <button
              onClick={() => setDevConsoleOpen(true)}
              data-testid="dev-console-btn"
              title="Dev Console — build step tracker"
              style={{
                background: 'transparent',
                color: '#64748B',
                border: '1px solid #334155',
                borderRadius: '6px',
                padding: '5px 10px',
                cursor: 'pointer',
                fontSize: '0.9rem',
                lineHeight: 1,
              }}
            >
              🛠
            </button>
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
            {build?.status === 'failed' && (
              <button
                onClick={handleRetryBuild}
                data-testid="retry-build-btn"
                style={{
                  background: '#2563EB',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '6px',
                  padding: '6px 16px',
                  cursor: 'pointer',
                  fontSize: '0.8rem',
                  fontWeight: 600,
                }}
              >
                Retry Build
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
              
            {isActive && (
              <button
                onClick={() => setShowForceCancelConfirm(true)}
                title="Force-cancel: kills the build task immediately (use if Cancel doesn't respond)"
                style={{
                  background: 'transparent',
                  color: '#DC2626',
                  border: '1px solid #7F1D1D',
                  borderRadius: '6px',
                  padding: '6px 16px',
                  cursor: 'pointer',
                  fontSize: '0.8rem',
                  fontWeight: 600,
                  opacity: 0.7,
                }}
              >
                Force Cancel
              </button>
            )}</button>
            )}
          </div>
        </div>

        {/* ---- Phase 45: Recon Summary ---- */}
        {reconData && (
          <div data-testid="recon-summary" style={{ display: 'flex', alignItems: 'center', gap: '16px', padding: '8px 16px', marginBottom: '12px', background: '#0F172A', borderRadius: '6px', border: '1px solid #1E293B', fontSize: '0.72rem', color: '#64748B', flexWrap: 'wrap' }}>
            <span>📂 {reconData.total_files.toLocaleString()} files</span>
            <span>·</span>
            <span>{reconData.total_lines.toLocaleString()} lines</span>
            <span>·</span>
            <span>{reconData.symbols_count.toLocaleString()} symbols</span>
            <span>·</span>
            <span>{reconData.test_count.toLocaleString()} tests</span>
            <span>·</span>
            <span>{reconData.tables.length} tables</span>
            {compactionCount > 0 && (
              <>
                <span>·</span>
                <span style={{ color: '#A78BFA' }}>🔄 {compactionCount} compaction{compactionCount > 1 ? 's' : ''}</span>
              </>
            )}
          </div>
        )}

        {/* ---- Phase 45: Invariant Strip ---- */}
        {invariants.size > 0 && (
          <div data-testid="invariant-strip" style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '6px 16px', marginBottom: '12px', flexWrap: 'wrap' }}>
            {Array.from(invariants.entries()).map(([name, inv]) => {
              const ok = inv.passed;
              const arrow = inv.constraint === 'monotonic_up' ? ' \u2191' : inv.constraint === 'monotonic_down' ? ' \u2193' : '';
              return (
                <span
                  key={name}
                  data-testid={`invariant-badge-${name}`}
                  title={`${name}: expected ${inv.expected}, actual ${inv.actual} (${inv.constraint})`}
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: '4px',
                    padding: '2px 10px', borderRadius: '12px', fontSize: '0.68rem', fontWeight: 600,
                    background: ok ? '#0D2818' : '#7F1D1D',
                    color: ok ? '#22C55E' : '#FCA5A5',
                    border: `1px solid ${ok ? '#16532D' : '#DC2626'}`,
                    ...(ok ? {} : { boxShadow: '0 0 8px #DC262644' }),
                  }}
                >
                  {name.replace(/_/g, ' ')}: {inv.actual}{arrow} {ok ? '\u2713' : '\u2717'}
                </span>
              );
            })}
          </div>
        )}

        {/* ---- Two-column layout ---- */}
        <div style={twoColStyle}>

          {/* ======== LEFT: Phase Chips + Files ======== */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={{ ...cardStyle, padding: '12px 16px', minHeight: chipGridHeight != null && expandedChip !== null ? chipGridHeight : undefined, transition: 'min-height 0.2s ease' }}>
            <h3 style={{ margin: '0 0 12px', fontSize: '0.9rem', color: '#F8FAFC' }}>
              Phases ({doneCount}/{totalPhases})
            </h3>
            {(() => {
              const chips = overviewPhases.length > 0
                ? overviewPhases
                : phaseDefs.map((d) => ({ number: d.number, name: d.name, objective: d.objective, status: 'pending' as OverviewPhase['status'] }));
              const chipColors: Record<string, string> = {
                pending: '#475569', active: '#3B82F6', passed: '#22C55E', failed: '#EF4444', paused: '#F59E0B',
              };
              const chipBg: Record<string, string> = {
                pending: '#0F172A', active: '#1E3A5F', passed: '#0D2818', failed: '#7F1D1D', paused: '#78350F',
              };

              /* ── Expanded detail view ── */
              if (expandedChip !== null) {
                const ep = chips.find((p) => p.number === expandedChip);
                if (!ep) { setExpandedChip(null); return null; }
                const c = chipColors[ep.status] ?? '#475569';
                const b = chipBg[ep.status] ?? '#0F172A';
                const ps = phaseStates.get(ep.number);
                const deliverables = ps?.def.deliverables ?? phaseDefs.find((d) => d.number === ep.number)?.deliverables ?? [];
                const phaseElapsed = ps && ps.elapsed_ms > 0 ? `${Math.floor(ps.elapsed_ms / 60000)}m ${Math.floor((ps.elapsed_ms % 60000) / 1000)}s` : '';
                return (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                    {/* Header bar */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <button
                        onClick={() => setExpandedChip(null)}
                        style={{
                          background: 'transparent', border: 'none', color: '#64748B', cursor: 'pointer',
                          fontSize: '0.8rem', padding: '4px 8px', borderRadius: '4px', display: 'flex', alignItems: 'center', gap: '4px',
                        }}
                        onMouseOver={(e) => (e.currentTarget.style.color = '#F8FAFC')}
                        onMouseOut={(e) => (e.currentTarget.style.color = '#64748B')}
                      >
                        ← All phases
                      </button>
                      <div style={{ flex: 1 }} />
                      {ps && (ps.input_tokens > 0 || ps.output_tokens > 0) && (
                        <div style={{ display: 'flex', gap: '12px', fontSize: '0.65rem', color: '#64748B' }}>
                          <span>{ps.input_tokens.toLocaleString()} in</span>
                          <span>{ps.output_tokens.toLocaleString()} out</span>
                          {phaseElapsed && <span>{phaseElapsed}</span>}
                        </div>
                      )}
                    </div>

                    {/* Phase card */}
                    <div style={{
                      padding: '14px 16px', borderRadius: '8px', background: b, border: `1px solid ${c}44`,
                      ...(ep.status === 'active' ? { borderColor: c, boxShadow: `0 0 10px ${c}25` } : {}),
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
                        <span style={{ fontSize: '0.85rem', fontWeight: 700, color: c }}>{ep.number}</span>
                        <span style={{ fontSize: '0.85rem', fontWeight: 600, color: c }}>{ep.name}</span>
                        <span style={{
                          marginLeft: 'auto', fontSize: '0.6rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em',
                          padding: '2px 8px', borderRadius: '4px', background: `${c}20`, color: c,
                        }}>
                          {ep.status}
                        </span>
                      </div>
                      <p style={{ margin: '0 0 10px', fontSize: '0.75rem', lineHeight: 1.6, color: '#CBD5E1' }}>
                        {ep.objective}
                      </p>
                      {deliverables.length > 0 && (
                        <div style={{ borderTop: '1px solid #334155', paddingTop: '10px' }}>
                          <div style={{ fontSize: '0.65rem', fontWeight: 600, color: '#64748B', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '6px' }}>
                            Deliverables
                          </div>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                            {deliverables.map((d, i) => (
                              <div key={i} style={{ fontSize: '0.7rem', color: '#94A3B8', display: 'flex', gap: '6px' }}>
                                <span style={{ color: '#475569', flexShrink: 0 }}>•</span>
                                <span>{d}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                );
              }

              /* ── Chips grid ── */
              return (
                <div ref={chipGridRef} style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px' }}>
                  {chips.map((op) => {
                    const c = chipColors[op.status] ?? '#475569';
                    const b = chipBg[op.status] ?? '#0F172A';
                    const isActiveChip = op.status === 'active';
                    return (
                      <div
                        key={op.number}
                        title={op.objective || op.name}
                        onClick={() => {
                          if (chipGridRef.current) {
                            setChipGridHeight(chipGridRef.current.closest('[style]')?.getBoundingClientRect().height ?? null);
                          }
                          setExpandedChip(op.number);
                        }}
                        style={{
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '2px',
                          padding: '8px 10px',
                          borderRadius: '8px',
                          background: b,
                          border: `1px solid ${c}44`,
                          cursor: 'pointer',
                          ...(isActiveChip ? { borderColor: c, boxShadow: `0 0 8px ${c}30` } : {}),
                          transition: 'all 0.2s',
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <span style={{ fontSize: '0.7rem', fontWeight: 700, color: c, flexShrink: 0 }}>{op.number}</span>
                          <span style={{ fontSize: '0.7rem', fontWeight: 600, color: c, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {op.name}
                          </span>
                        </div>
                        <span style={{ fontSize: '0.6rem', color: '#64748B', overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' as const }}>
                          {op.objective}
                        </span>
                      </div>
                    );
                  })}
                </div>
              );
            })()}
          </div>

          {/* ======== Build Plan Panel ======== */}
          {planTasks.length > 0 && manifestFiles.length === 0 && (
            <div style={{ ...cardStyle, padding: '12px 16px' }}>
              <div
                style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer', marginBottom: planExpanded ? '10px' : 0 }}
                onClick={() => setPlanExpanded(!planExpanded)}
              >
                <h3 style={{ margin: 0, fontSize: '0.9rem', color: '#F8FAFC' }}>
                  Build Plan ({planTasks.filter((t) => t.status === 'done').length}/{planTasks.length})
                </h3>
                <span style={{ color: '#64748B', fontSize: '0.7rem', transition: 'transform 0.2s', transform: planExpanded ? 'rotate(180deg)' : 'rotate(0)' }}>▼</span>
              </div>
              {planExpanded && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }} data-testid="build-plan-panel">
                  {planTasks.map((task) => (
                    <div
                      key={task.id}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        padding: '5px 8px',
                        borderRadius: '4px',
                        background: task.status === 'done' ? '#0D2818' : '#0F172A',
                        fontSize: '0.75rem',
                      }}
                    >
                      <span style={{ color: task.status === 'done' ? '#22C55E' : '#475569', flexShrink: 0 }}>
                        {task.status === 'done' ? '✓' : '○'}
                      </span>
                      <span style={{ color: task.status === 'done' ? '#22C55E' : '#94A3B8', flex: 1, textDecoration: task.status === 'done' ? 'line-through' : 'none' }}>
                        {task.title}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* ======== Manifest Panel (Phase 21 — plan-execute) ======== */}
          <div style={{ ...cardStyle, padding: '12px 16px' }}>
            <div
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: manifestFiles.length > 0 ? 'pointer' : 'default', marginBottom: manifestExpanded && manifestFiles.length > 0 ? '10px' : 0 }}
              onClick={() => manifestFiles.length > 0 && setManifestExpanded(!manifestExpanded)}
            >
              <h3 style={{ margin: 0, fontSize: '0.9rem', color: '#F8FAFC' }}>
                Files{manifestFiles.length > 0 ? ` (${manifestFiles.filter((f) => f.status === 'done').length}/${manifestFiles.length})` : ''}
                {manifestFiles.some((f) => f.auditStatus) && (
                  <span style={{ fontSize: '0.7rem', color: '#64748B', marginLeft: '8px' }}>
                    audited {manifestFiles.filter((f) => f.auditStatus === 'pass' || f.auditStatus === 'fail' || f.auditStatus === 'fixing' || f.auditStatus === 'fixed').length}/{manifestFiles.filter((f) => f.status === 'done').length}
                    {manifestFiles.some((f) => f.auditStatus === 'fixing') && (
                      <span style={{ color: '#F59E0B', marginLeft: '4px' }}>
                        ({manifestFiles.filter((f) => f.auditStatus === 'fixing').length} fixing)
                      </span>
                    )}
                    {manifestFiles.some((f) => f.auditStatus === 'fixed') && (
                      <span style={{ color: '#22C55E', marginLeft: '4px' }}>
                        ({manifestFiles.filter((f) => f.auditStatus === 'fixed').length} fixed)
                      </span>
                    )}
                    {manifestFiles.some((f) => f.auditStatus === 'fail') && (
                      <span style={{ color: '#EF4444', marginLeft: '4px' }}>
                        ({manifestFiles.filter((f) => f.auditStatus === 'fail').length} failed)
                      </span>
                    )}
                  </span>
                )}
              </h3>
              {manifestFiles.length > 0 && (
                <span style={{ color: '#64748B', fontSize: '0.7rem', transition: 'transform 0.2s', transform: manifestExpanded ? 'rotate(180deg)' : 'rotate(0)' }}>▼</span>
              )}
            </div>
            {manifestFiles.length === 0 ? (
              <div style={{ padding: '12px 0 4px', fontSize: '0.72rem', color: '#475569', fontStyle: 'italic' }}>
                Waiting for file manifest…
              </div>
            ) : manifestExpanded ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', maxHeight: expandedFile ? '520px' : '280px', overflowY: 'auto', transition: 'max-height 0.3s ease' }} data-testid="manifest-panel">
                  {manifestFiles.map((f) => {
                    const isExpanded = expandedFile === f.path;
                    const isClickable = f.status === 'done' || f.status === 'generating';
                    return (
                      <div key={f.path}>
                        <div
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '8px',
                            padding: '5px 8px',
                            borderRadius: isExpanded ? '4px 4px 0 0' : '4px',
                            background: f.auditStatus === 'fail' ? '#2D1215'
                              : f.auditStatus === 'fixing' ? '#2D2215'
                              : f.status === 'done' ? '#0D2818'
                              : f.status === 'generating' ? '#1E3A5F'
                              : '#0F172A',
                            fontSize: '0.72rem',
                            transition: 'background 0.3s',
                            cursor: isClickable ? 'pointer' : 'default',
                            userSelect: 'none',
                          }}
                          onClick={() => {
                            if (!isClickable) return;
                            if (isExpanded) {
                              setExpandedFile(null);
                              return;
                            }
                            setExpandedFile(f.path);
                            // Fetch content for completed files
                            if (f.status === 'done' && !fileContentCache.has(f.path)) {
                              setFileContentLoading(true);
                              fetch(`${API_BASE}/projects/${projectId}/build/files/${encodeURIComponent(f.path)}`, {
                                headers: { Authorization: `Bearer ${token}` },
                              })
                                .then((res) => (res.ok ? res.json() : null))
                                .then((data) => {
                                  if (data?.content) {
                                    setFileContentCache((prev) => new Map(prev).set(f.path, data.content));
                                  }
                                })
                                .catch(() => {})
                                .finally(() => setFileContentLoading(false));
                            }
                          }}
                        >
                          <span style={{
                            color: f.auditStatus === 'fail' ? '#EF4444'
                              : f.auditStatus === 'fixing' ? '#F59E0B'
                              : f.auditStatus === 'fixed' ? '#22C55E'
                              : f.auditStatus === 'pass' ? '#22C55E'
                              : f.status === 'done' ? '#22C55E'
                              : f.status === 'generating' ? '#3B82F6'
                              : f.status === 'error' ? '#EF4444'
                              : '#475569',
                            flexShrink: 0,
                            animation: f.status === 'generating' || f.auditStatus === 'fixing' ? 'pulse 1.5s infinite' : 'none',
                          }}>
                            {f.auditStatus === 'fixed' ? '✓✓'
                              : f.auditStatus === 'fixing' ? '🔧'
                              : f.auditStatus === 'pass' ? '✓✓'
                              : f.auditStatus === 'fail' ? '✗'
                              : f.status === 'done' ? '✓'
                              : f.status === 'generating' ? '⟳'
                              : f.status === 'error' ? '✗'
                              : '○'}
                          </span>
                          <span style={{ color: f.auditStatus === 'fail' ? '#EF4444' : f.auditStatus === 'fixing' ? '#F59E0B' : f.status === 'done' ? '#22C55E' : '#94A3B8', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {f.path}
                          </span>
                          <span style={{ color: '#475569', flexShrink: 0, fontSize: '0.62rem', display: 'flex', alignItems: 'center', gap: '4px' }}>
                            {f.auditStatus === 'fixing' && (
                              <span style={{ color: '#F59E0B', fontSize: '0.58rem', animation: 'pulse 1.5s infinite' }}>fixing</span>
                            )}
                            {f.auditStatus === 'pending' && f.status === 'done' && (
                              <span style={{ color: '#F59E0B', fontSize: '0.58rem', animation: 'pulse 1.5s infinite' }}>auditing</span>
                            )}
                            {f.language && <span>{f.language}</span>}
                            {f.status === 'done' && f.size_bytes ? `${(f.size_bytes / 1024).toFixed(1)}k` : `~${f.estimated_lines}L`}
                            {isClickable && (
                              <span style={{ marginLeft: '4px', fontSize: '0.6rem', color: '#64748B', transition: 'transform 0.2s', transform: isExpanded ? 'rotate(180deg)' : 'rotate(0)', display: 'inline-block' }}>
                                ▼
                              </span>
                            )}
                          </span>
                        </div>
                        {/* ── Expanded code preview ── */}
                        {isExpanded && (
                          <div style={{
                            background: '#0B1120',
                            border: '1px solid #1E293B',
                            borderTop: 'none',
                            borderRadius: '0 0 4px 4px',
                            padding: '0',
                            maxHeight: '320px',
                            overflow: 'auto',
                            position: 'relative',
                          }}>
                            {f.status === 'generating' ? (
                              <div style={{ padding: '16px', display: 'flex', alignItems: 'center', gap: '8px', color: '#3B82F6', fontSize: '0.72rem' }}>
                                <span style={{ animation: 'pulse 1.5s infinite' }}>⟳</span>
                                <span>Generating — content will appear when complete…</span>
                              </div>
                            ) : fileContentLoading && !fileContentCache.has(f.path) ? (
                              <div style={{ padding: '16px', color: '#64748B', fontSize: '0.72rem' }}>Loading…</div>
                            ) : fileContentCache.has(f.path) ? (
                              (() => {
                                const code = fileContentCache.get(f.path) || '';
                                const lines = code.split('\n');
                                // Parse audit findings for line-number annotations
                                const lineAnnotations = new Map<number, string>();
                                if (f.auditStatus === 'fail' && f.auditFindings) {
                                  const findingLines = f.auditFindings.split('\n');
                                  for (const fl of findingLines) {
                                    const m = fl.match(/^L(\d+)(?:\s*-\s*L?(\d+))?:\s*(.+)/);
                                    if (m) {
                                      const start = parseInt(m[1], 10);
                                      const end = m[2] ? parseInt(m[2], 10) : start;
                                      const desc = m[3].trim();
                                      for (let ln = start; ln <= end; ln++) {
                                        lineAnnotations.set(ln, lineAnnotations.has(ln)
                                          ? lineAnnotations.get(ln) + '; ' + desc : desc);
                                      }
                                    }
                                  }
                                }
                                const hasAnnotations = lineAnnotations.size > 0;
                                const gutterW = String(lines.length).length;
                                return (
                                  <div style={{ margin: 0, padding: '10px 0', fontSize: '0.68rem', lineHeight: '1.5', color: '#CBD5E1', fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace", overflowX: 'auto', tabSize: 2 }}>
                                    {/* Findings summary banner */}
                                    {f.auditStatus === 'fail' && f.auditFindings && (
                                      <div style={{ padding: '6px 12px', marginBottom: '6px', background: '#3B1219', borderLeft: '3px solid #EF4444', fontSize: '0.66rem', color: '#FCA5A5' }}>
                                        <span style={{ fontWeight: 600, marginRight: '6px' }}>⚠ Audit findings:</span>
                                        {lineAnnotations.size > 0
                                          ? `${lineAnnotations.size} line${lineAnnotations.size > 1 ? 's' : ''} flagged`
                                          : f.auditFindings.split('\n').filter((l: string) => l.trim() && !l.includes('VERDICT:')).length + ' issue(s)'}
                                      </div>
                                    )}
                                    {lines.map((line, i) => {
                                      const lineNum = i + 1;
                                      const annotation = lineAnnotations.get(lineNum);
                                      const isFlagged = !!annotation;
                                      return (
                                        <div key={i}>
                                          <div style={{
                                            display: 'flex',
                                            background: isFlagged ? 'rgba(239,68,68,0.12)' : 'transparent',
                                            borderLeft: isFlagged ? '3px solid #EF4444' : '3px solid transparent',
                                          }}>
                                            {hasAnnotations && (
                                              <span style={{
                                                display: 'inline-block',
                                                width: `${gutterW + 1}ch`,
                                                textAlign: 'right',
                                                paddingRight: '8px',
                                                paddingLeft: '8px',
                                                color: isFlagged ? '#EF4444' : '#475569',
                                                userSelect: 'none',
                                                flexShrink: 0,
                                              }}>{lineNum}</span>
                                            )}
                                            <span style={{ paddingLeft: hasAnnotations ? '0' : '12px', paddingRight: '12px', whiteSpace: 'pre' }}>{line}</span>
                                          </div>
                                          {isFlagged && annotation && (
                                            <div style={{
                                              display: 'flex',
                                              alignItems: 'flex-start',
                                              paddingLeft: hasAnnotations ? `calc(${gutterW + 1}ch + 19px)` : '24px',
                                              paddingRight: '12px',
                                              paddingBottom: '2px',
                                              background: 'rgba(239,68,68,0.06)',
                                              borderLeft: '3px solid #EF4444',
                                            }}>
                                              <span style={{ color: '#F87171', fontSize: '0.62rem', fontStyle: 'italic', fontFamily: 'system-ui, sans-serif' }}>
                                                ↳ {annotation}
                                              </span>
                                            </div>
                                          )}
                                        </div>
                                      );
                                    })}
                                  </div>
                                );
                              })()
                            ) : (
                              <div style={{ padding: '16px', color: '#64748B', fontSize: '0.72rem' }}>Unable to load content</div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              ) : null}
              {verification && (
                <div style={{ marginTop: '8px' }}>
                  <div
                    style={{
                      padding: '8px 10px', background: '#0F172A', borderRadius: verificationExpanded ? '4px 4px 0 0' : '4px',
                      fontSize: '0.72rem', cursor: 'pointer', display: 'flex', alignItems: 'center',
                      userSelect: 'none',
                    }}
                    onClick={() => setVerificationExpanded(!verificationExpanded)}
                  >
                    <span style={{ color: '#64748B', marginRight: '8px' }}>Verification:</span>
                    {verification.syntax_errors > 0 && <span style={{ color: '#EF4444', marginRight: '8px' }}>{verification.syntax_errors} syntax errors</span>}
                    {verification.tests_passed > 0 && <span style={{ color: '#22C55E', marginRight: '8px' }}>{verification.tests_passed} tests passed</span>}
                    {verification.tests_failed > 0 && <span style={{ color: '#EF4444', marginRight: '8px' }}>{verification.tests_failed} tests failed</span>}
                    {verification.fixes_applied > 0 && <span style={{ color: '#F59E0B', marginRight: '8px' }}>{verification.fixes_applied} fixes applied</span>}
                    {!verification.syntax_errors && !verification.tests_failed && <span style={{ color: '#22C55E' }}>Clean</span>}
                    <span style={{ marginLeft: 'auto', color: '#475569', fontSize: '0.6rem', transition: 'transform 0.2s', transform: verificationExpanded ? 'rotate(180deg)' : 'rotate(0)' }}>▼</span>
                    {/* Fix button — only shown when there are errors */}
                    {(verification.syntax_errors > 0 || verification.tests_failed > 0) && verification.test_output && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          if (!verificationExpanded) {
                            // First click: expand to show the error
                            setVerificationExpanded(true);
                            return;
                          }
                          // Second click: send fix command to builder
                          const fixMsg = `/fix Fix the following verification error with minimal diff. Do not change unrelated code.\n\n--- Verification Output ---\n${verification.test_output}`;
                          fetch(`${API_BASE}/projects/${projectId}/build/interject`, {
                            method: 'POST',
                            headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
                            body: JSON.stringify({ message: fixMsg }),
                          })
                            .then((res) => {
                              if (res.ok) addToast('Fix request sent to builder', 'success');
                              else addToast('Failed to send fix request', 'error');
                            })
                            .catch(() => addToast('Failed to send fix request', 'error'));
                        }}
                        style={{
                          marginLeft: '8px',
                          padding: '2px 8px',
                          borderRadius: '4px',
                          border: 'none',
                          fontSize: '0.65rem',
                          fontWeight: 600,
                          cursor: 'pointer',
                          background: verificationExpanded ? '#16A34A' : '#1E293B',
                          color: verificationExpanded ? '#FFFFFF' : '#475569',
                          transition: 'all 0.2s',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '4px',
                          flexShrink: 0,
                        }}
                        title={verificationExpanded ? 'Send error to builder to fix' : 'Click to view errors first'}
                      >
                        🔧 {verificationExpanded ? 'Fix' : '?'}
                      </button>
                    )}
                  </div>
                  {verificationExpanded && verification.test_output && (
                    <div style={{
                      background: '#0B1120', border: '1px solid #1E293B', borderTop: 'none',
                      borderRadius: '0 0 4px 4px', maxHeight: '240px', overflow: 'auto',
                    }}>
                      <pre style={{
                        margin: 0, padding: '10px 12px', fontSize: '0.65rem', lineHeight: 1.5,
                        color: '#CBD5E1', fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                      }}>{verification.test_output}</pre>
                    </div>
                  )}
                </div>
              )}
              {governance && (
                <div style={{ marginTop: '8px' }}>
                  <div
                    style={{
                      padding: '8px 10px', background: '#0F172A', borderRadius: governanceExpanded ? '4px 4px 0 0' : '4px',
                      fontSize: '0.72rem', cursor: 'pointer', display: 'flex', alignItems: 'center',
                      userSelect: 'none',
                    }}
                    onClick={() => setGovernanceExpanded(!governanceExpanded)}
                  >
                    <span style={{ color: '#64748B', marginRight: '8px' }}>Governance:</span>
                    {governance.passed
                      ? <span style={{ color: '#22C55E' }}>All checks passed</span>
                      : (
                        <>
                          {governance.blocking_failures > 0 && <span style={{ color: '#EF4444', marginRight: '8px' }}>{governance.blocking_failures} blocking</span>}
                          {governance.warnings > 0 && <span style={{ color: '#F59E0B', marginRight: '8px' }}>{governance.warnings} warnings</span>}
                        </>
                      )
                    }
                    <span style={{ marginLeft: 'auto', color: '#475569', fontSize: '0.6rem', transition: 'transform 0.2s', transform: governanceExpanded ? 'rotate(180deg)' : 'rotate(0)' }}>▼</span>
                  </div>
                  {governanceExpanded && (
                    <div style={{
                      background: '#0B1120', border: '1px solid #1E293B', borderTop: 'none',
                      borderRadius: '0 0 4px 4px', padding: '8px 12px',
                    }}>
                      {governance.checks.map((c) => (
                        <div key={c.code} style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', padding: '4px 0', fontSize: '0.68rem', borderBottom: '1px solid #1E293B' }}>
                          <span style={{ flexShrink: 0 }}>{c.result === 'PASS' ? '✅' : c.result === 'FAIL' ? '❌' : '⚠️'}</span>
                          <span style={{ color: '#94A3B8', fontWeight: 600, minWidth: '24px' }}>{c.code}</span>
                          <span style={{ color: '#CBD5E1' }}>{c.name}</span>
                          <span style={{ color: c.result === 'PASS' ? '#475569' : '#F59E0B', marginLeft: 'auto', textAlign: 'right', fontSize: '0.62rem', maxWidth: '50%', wordBreak: 'break-word' }}>
                            {c.detail}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* ---- Phase 45: Task DAG Panel ---- */}
            {dagTasks.length > 0 && (
              <div data-testid="dag-panel" style={cardStyle}>
                <div
                  onClick={() => setDagExpanded(!dagExpanded)}
                  style={{ display: 'flex', alignItems: 'center', cursor: 'pointer', gap: '8px' }}
                >
                  <h3 style={{ margin: 0, fontSize: '0.8rem', color: '#F8FAFC', flex: 1 }}>
                    Task DAG{dagProgress ? ` (${dagProgress.completed}/${dagProgress.total} — ${Math.round(dagProgress.percentage)}%)` : ''}
                  </h3>
                  <span style={{ color: '#475569', fontSize: '0.6rem', transition: 'transform 0.2s', transform: dagExpanded ? 'rotate(180deg)' : 'rotate(0)' }}>▼</span>
                </div>
                {dagExpanded && dagProgress && (
                  <div style={{ margin: '8px 0 6px', height: '4px', borderRadius: '2px', background: '#1E293B', overflow: 'hidden' }}>
                    <div style={{ height: '100%', borderRadius: '2px', width: `${dagProgress.percentage}%`, background: dagProgress.failed > 0 ? '#EF4444' : '#22C55E', transition: 'width 0.3s ease' }} />
                  </div>
                )}
                {dagExpanded && dagTasks.map((t) => {
                  const icons: Record<string, string> = { pending: '\u23f3', in_progress: '\u2699\ufe0f', completed: '\u2705', failed: '\u274c', blocked: '\ud83d\udeab', skipped: '\u23ed' };
                  const colors: Record<string, string> = { pending: '#475569', in_progress: '#3B82F6', completed: '#22C55E', failed: '#EF4444', blocked: '#F59E0B', skipped: '#334155' };
                  return (
                    <div key={t.id} data-testid={`dag-task-${t.id}`} style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '3px 0', fontSize: '0.68rem', color: colors[t.status] ?? '#475569',
                      ...(t.status === 'in_progress' ? { animation: 'pulse 1.5s infinite' } : {}),
                    }}>
                      <span style={{ width: '18px', textAlign: 'center' }}>{icons[t.status] ?? '\u23f3'}</span>
                      <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {t.file_path ?? t.title}
                      </span>
                      {t.depends_on.length > 0 && (
                        <span style={{ color: '#334155', fontSize: '0.58rem' }}>dep: {t.depends_on.length}</span>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* ---- Phase 45: Journal Timeline ---- */}
            {journalEvents.length > 0 && (
              <div data-testid="journal-timeline" style={cardStyle}>
                <div
                  onClick={() => setJournalExpanded(!journalExpanded)}
                  style={{ display: 'flex', alignItems: 'center', cursor: 'pointer', gap: '8px' }}
                >
                  <h3 style={{ margin: 0, fontSize: '0.8rem', color: '#F8FAFC', flex: 1 }}>
                    Journal ({journalEvents.length})
                  </h3>
                  <span style={{ color: '#475569', fontSize: '0.6rem', transition: 'transform 0.2s', transform: journalExpanded ? 'rotate(180deg)' : 'rotate(0)' }}>▼</span>
                </div>
                {journalExpanded && journalEvents.map((e, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', padding: '3px 0', fontSize: '0.65rem' }}>
                    <span style={{ color: '#3B82F6' }}>●</span>
                    <span style={{ color: '#475569', flexShrink: 0 }}>{e.timestamp}</span>
                    <span style={{ color: '#94A3B8' }}>{e.message}</span>
                  </div>
                ))}
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
                {turnCount > 1 && (
                  <div style={metricBoxStyle}>
                    <span style={{ fontSize: '0.65rem', color: '#64748B', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Turns</span>
                    <span style={{ fontSize: '1.1rem', fontWeight: 700, color: '#A78BFA' }}>{turnCount}</span>
                  </div>
                )}
              </div>

              {/* Context window meter */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.65rem', color: '#94A3B8' }}>
                  <span>Context Window</span>
                  <span>{ctxTok.toLocaleString()} / {contextWindow.toLocaleString()} ({ctxPercent.toFixed(1)}%)</span>
                </div>
                <div style={{ width: '100%', height: '8px', background: '#0F172A', borderRadius: '4px', overflow: 'hidden' }}>
                  <div style={{ width: `${ctxPercent}%`, height: '100%', background: ctxColor, borderRadius: '4px', transition: 'width 0.4s ease' }} />
                </div>
              </div>

              {/* Spend cap progress meter */}
              {liveCost.spend_cap != null && liveCost.spend_cap > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', marginTop: '8px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.65rem', color: '#94A3B8' }}>
                    <span>Spend Cap</span>
                    <span>${liveCost.total_cost_usd.toFixed(4)} / ${liveCost.spend_cap.toFixed(2)} ({liveCost.pct_used.toFixed(1)}%)</span>
                  </div>
                  <div style={{ width: '100%', height: '8px', background: '#0F172A', borderRadius: '4px', overflow: 'hidden' }}>
                    <div style={{
                      width: `${Math.min(100, liveCost.pct_used)}%`, height: '100%',
                      background: liveCost.pct_used > 80 ? '#EF4444' : liveCost.pct_used > 50 ? '#F59E0B' : '#3B82F6',
                      borderRadius: '4px', transition: 'width 0.4s ease',
                    }} />
                  </div>
                </div>
              )}

              {/* Live API call counter */}
              {liveCost.api_calls > 0 && (
                <div style={{ display: 'flex', gap: '16px', marginTop: '8px', fontSize: '0.65rem', color: '#94A3B8' }}>
                  <span>API Calls: <span style={{ color: '#F8FAFC', fontWeight: 600 }}>{liveCost.api_calls}</span></span>
                  <span>Live Cost: <span style={{ color: '#22C55E', fontWeight: 600 }}>${liveCost.total_cost_usd.toFixed(4)}</span></span>
                </div>
              )}
            </div>

            {/* Cost warning/exceeded banners */}
            {costWarning && !costExceeded && (
              <div style={{ background: '#78350F', borderRadius: '6px', padding: '10px 16px', fontSize: '0.78rem', color: '#FDE68A', display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ fontSize: '1.1rem' }}>⚠️</span>
                <span>{costWarning}</span>
              </div>
            )}
            {costExceeded && (
              <div style={{ background: '#7F1D1D', borderRadius: '6px', padding: '10px 16px', fontSize: '0.78rem', color: '#FCA5A5', display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ fontSize: '1.1rem' }}>🛑</span>
                <span>{costExceeded}</span>
              </div>
            )}

            {/* Circuit Breaker */}
            {isActive && (
              <button
                onClick={handleCircuitBreak}
                disabled={circuitBreaking}
                data-testid="circuit-breaker-btn"
                style={{
                  background: '#DC2626', color: '#FFF', border: 'none', borderRadius: '8px',
                  padding: '10px 16px', cursor: circuitBreaking ? 'not-allowed' : 'pointer',
                  fontWeight: 700, fontSize: '0.85rem', width: '100%',
                  opacity: circuitBreaking ? 0.6 : 1,
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
                }}
              >
                <span style={{ fontSize: '1.1rem' }}>⚡</span>
                {circuitBreaking ? 'Stopping...' : 'CIRCUIT BREAKER — Kill All API Calls'}
              </button>
            )}
            {build?.error_detail && (
              <div style={{ background: '#7F1D1D', borderRadius: '6px', padding: '10px 16px', fontSize: '0.78rem', color: '#FCA5A5' }}>
                <strong>Error:</strong> {build.error_detail}
              </div>
            )}

            {/* -- Activity Feed -- */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  {(['activity', 'output'] as const).map((tab) => {
                    const active = activityTab === tab;
                    const count = activity.filter((e) => e.category === tab).length;
                    return (
                      <button
                        key={tab}
                        onClick={() => setActivityTab(tab)}
                        data-testid={`tab-${tab}`}
                        style={{
                          background: active ? '#1E293B' : 'transparent',
                          color: active ? '#F8FAFC' : '#64748B',
                          border: active ? '1px solid #334155' : '1px solid transparent',
                          borderRadius: '6px',
                          padding: '3px 10px',
                          cursor: 'pointer',
                          fontSize: '0.78rem',
                          fontWeight: active ? 600 : 400,
                          lineHeight: 1.4,
                        }}
                      >
                        {tab === 'activity' ? 'Activity' : 'Build Output'}
                        {count > 0 && (
                          <span style={{ marginLeft: '5px', opacity: 0.6, fontSize: '0.7rem' }}>
                            {count}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
                <input
                  type="text"
                  placeholder="Search logs..."
                  value={logSearch}
                  onChange={(e) => setLogSearch(e.target.value)}
                  data-testid="log-search-input"
                  style={{
                    background: '#0F172A',
                    border: '1px solid #334155',
                    borderRadius: '4px',
                    padding: '4px 8px',
                    color: '#F8FAFC',
                    fontSize: '0.7rem',
                    outline: 'none',
                    width: '180px',
                  }}
                />
              </div>
              {/* Live activity status bar */}
              {activityStatus && (
                <div
                  data-testid="activity-status-bar"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '6px 12px',
                    background: 'linear-gradient(90deg, #0F172A 0%, #1E293B 100%)',
                    border: '1px solid #334155',
                    borderRadius: '6px',
                    marginBottom: '6px',
                    fontSize: '0.75rem',
                    color: '#94A3B8',
                    fontFamily: 'monospace',
                  }}
                >
                  <span
                    style={{
                      display: 'inline-block',
                      width: '14px',
                      height: '14px',
                      border: '2px solid #334155',
                      borderTopColor: '#3B82F6',
                      borderRadius: '50%',
                      animation: 'spin 0.8s linear infinite',
                      flexShrink: 0,
                    }}
                  />
                  <span style={{ color: '#E2E8F0' }}>{activityStatus}</span>
                </div>
              )}
              <div ref={feedContainerRef} onScroll={handleFeedScroll} style={feedStyle} data-testid="build-activity-feed">
                {filteredActivity.length === 0 ? (
                  <div style={{ color: '#475569' }}>
                    {logSearch
                      ? 'No matching logs'
                      : activityTab === 'output'
                        ? 'No build output yet...'
                        : 'Waiting for build activity...'}
                  </div>
                ) : (
                  filteredActivity.map((entry, i) => {
                    const isLong = entry.message.length > 300;
                    return (
                      <ActivityLine key={i} entry={entry} isLong={isLong} />
                    );
                  })
                )}
                <div ref={feedEndRef} />
              </div>
            </div>

            {/* -- Queued Interjections -- */}
            {queuedInterjections.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginTop: '6px', padding: '0 2px' }}>
                {queuedInterjections.map((q) => (
                  <div
                    key={q.id}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '6px',
                      padding: '3px 10px',
                      borderRadius: '12px',
                      fontSize: '0.65rem',
                      background: q.status === 'delivered' ? '#0D2818' : '#1E293B',
                      border: `1px solid ${q.status === 'delivered' ? '#22C55E40' : '#475569'}`,
                      color: q.status === 'delivered' ? '#22C55E' : '#94A3B8',
                      transition: 'all 0.3s',
                      maxWidth: '300px',
                    }}
                  >
                    <span style={{
                      width: '6px', height: '6px', borderRadius: '50%', flexShrink: 0,
                      background: q.status === 'delivered' ? '#22C55E' : '#F59E0B',
                      animation: q.status === 'pending' ? 'pulse 1.5s infinite' : 'none',
                    }} />
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {q.text.length > 60 ? q.text.slice(0, 57) + '...' : q.text}
                    </span>
                    <span style={{ color: '#475569', fontSize: '0.58rem', flexShrink: 0 }}>
                      {q.status === 'delivered' ? '✓' : q.time}
                    </span>
                    {q.status === 'pending' && (
                      <button
                        onClick={() => setQueuedInterjections((prev) => prev.filter((i) => i.id !== q.id))}
                        style={{
                          background: 'none', border: 'none', color: '#64748B', cursor: 'pointer',
                          padding: '0 2px', fontSize: '0.6rem', lineHeight: 1, flexShrink: 0,
                        }}
                        title="Dismiss"
                      >×</button>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* -- Interjection Input -- */}
            {(isActive || build?.status === 'completed' || build?.status === 'failed' || build?.status === 'cancelled') && (
              <div style={{ display: 'flex', gap: '8px', marginTop: '8px', position: 'relative' }} data-testid="interjection-bar">
                {/* Slash command autocomplete */}
                {filteredSlash.length > 0 && (
                  <div style={{
                    position: 'absolute',
                    bottom: '100%',
                    left: 0,
                    marginBottom: '4px',
                    background: '#1E293B',
                    border: '1px solid #475569',
                    borderRadius: '8px',
                    padding: '4px 0',
                    minWidth: '280px',
                    zIndex: 50,
                    boxShadow: '0 -4px 16px rgba(0,0,0,0.4)',
                  }}>
                    {filteredSlash.map((c, i) => (
                      <div
                        key={c.cmd}
                        onClick={() => pickSlashCmd(c.cmd)}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '10px',
                          padding: '6px 12px',
                          cursor: 'pointer',
                          background: i === slashMenuIdx ? '#334155' : 'transparent',
                          fontSize: '0.8rem',
                        }}
                        onMouseEnter={() => setSlashMenuIdx(i)}
                      >
                        <span style={{
                          width: '22px',
                          height: '22px',
                          borderRadius: '4px',
                          background: c.color,
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          fontSize: '0.7rem',
                          flexShrink: 0,
                        }}>{c.icon}</span>
                        <span style={{ color: '#F8FAFC', fontWeight: 600 }}>{c.cmd}</span>
                        <span style={{ color: '#94A3B8', fontSize: '0.72rem', marginLeft: 'auto' }}>{c.desc}</span>
                      </div>
                    ))}
                  </div>
                )}
                <input
                  ref={interjRef}
                  type="text"
                  placeholder={isActive ? 'Type / for commands or send feedback...' : '/start to begin a new build...'}
                  value={interjectionText}
                  onChange={(e) => { setInterjectionText(e.target.value); setSlashMenuIdx(0); }}
                  onKeyDown={handleSlashKey}
                  style={{
                    flex: 1,
                    background: '#0F172A',
                    border: `1px solid ${interjectionText.trim().startsWith('/') ? '#F59E0B' : '#334155'}`,
                    borderRadius: '6px',
                    padding: '8px 12px',
                    color: '#F8FAFC',
                    fontSize: '0.8rem',
                    outline: 'none',
                  }}
                />
                <button
                  onClick={handleInterject}
                  disabled={sendingInterject || !interjectionText.trim()}
                  style={{
                    background: SLASH_COMMANDS.find((c) => c.cmd === interjectionText.trim().toLowerCase())?.color ?? '#2563EB',
                    color: '#fff',
                    border: 'none',
                    borderRadius: '6px',
                    padding: '8px 16px',
                    cursor: sendingInterject || !interjectionText.trim() ? 'not-allowed' : 'pointer',
                    fontSize: '0.8rem',
                    fontWeight: 600,
                    opacity: sendingInterject || !interjectionText.trim() ? 0.5 : 1,
                  }}
                >
                  {(() => {
                    if (sendingInterject) return 'Sending...';
                    const match = SLASH_COMMANDS.find((c) => c.cmd === interjectionText.trim().toLowerCase());
        

      {showForceCancelConfirm && (
        <ConfirmDialog
          title="Force Cancel Build"
          message="This will immediately kill the build task and mark it as failed. Use this only if the normal Cancel isn't responding. This cannot be undone."
          confirmLabel="Force Cancel"
          onConfirm={handleForceCancel}
          onCancel={() => setShowForceCancelConfirm(false)}
        />
      )}            return match ? `${match.icon} ${match.cmd.slice(1).charAt(0).toUpperCase()}${match.cmd.slice(2)}` : 'Interject';
                  })()}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Dev Console Modal */}
      <DevConsole open={devConsoleOpen} onClose={() => setDevConsoleOpen(false)} steps={devSteps} />

      {showCancelConfirm && (
        <ConfirmDialog
          title="Cancel Build"
          message="Are you sure you want to cancel the active build? This cannot be undone."
          confirmLabel="Cancel Build"
          onConfirm={handleCancel}
          onCancel={() => setShowCancelConfirm(false)}
        />
      )}

      {showPauseModal && pauseInfo && (
        <div
          data-testid="pause-modal"
          style={{
            position: 'fixed', inset: 0,
            background: 'rgba(0,0,0,0.6)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            zIndex: 100,
          }}
        >
          <div style={{
            background: '#1E293B',
            borderRadius: '12px',
            padding: '24px 32px',
            maxWidth: '480px',
            width: '100%',
            boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
            position: 'relative',
          }}>
            <button
              onClick={() => { setShowPauseModal(false); setPauseInfo(null); }}
              style={{
                position: 'absolute', top: '12px', right: '12px',
                background: 'transparent', border: 'none', color: '#64748B',
                fontSize: '1.2rem', cursor: 'pointer', padding: '4px 8px',
                lineHeight: 1,
              }}
              title="Dismiss"
            >
              ✕
            </button>
            <h3 style={{ margin: '0 0 8px', color: '#F59E0B', fontSize: '1rem' }}>
              ⏸ Build Paused
            </h3>
            <p style={{ color: '#94A3B8', fontSize: '0.82rem', margin: '0 0 12px' }}>
              <strong>{pauseInfo.phase}</strong> — {pauseInfo.loop_count} consecutive audit
              failure{pauseInfo.loop_count !== 1 ? 's' : ''}.
            </p>
            <p style={{ color: '#CBD5E1', fontSize: '0.78rem', margin: '0 0 20px' }}>
              {pauseInfo.audit_findings}
            </p>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              <button
                onClick={() => handleResume('retry')}
                disabled={resuming}
                style={{
                  background: '#2563EB', color: '#fff', border: 'none', borderRadius: '6px',
                  padding: '8px 16px', cursor: 'pointer', fontSize: '0.8rem', fontWeight: 600,
                }}
              >
                Retry Phase
              </button>
              <button
                onClick={() => handleResume('skip')}
                disabled={resuming}
                style={{
                  background: '#F59E0B', color: '#0F172A', border: 'none', borderRadius: '6px',
                  padding: '8px 16px', cursor: 'pointer', fontSize: '0.8rem', fontWeight: 600,
                }}
              >
                Skip Phase
              </button>
              <button
                onClick={() => handleResume('edit')}
                disabled={resuming}
                style={{
                  background: 'transparent', color: '#94A3B8', border: '1px solid #334155',
                  borderRadius: '6px', padding: '8px 16px', cursor: 'pointer',
                  fontSize: '0.8rem', fontWeight: 600,
                }}
              >
                Edit & Retry
              </button>
              <button
                onClick={() => handleResume('abort')}
                disabled={resuming}
                style={{
                  background: 'transparent', color: '#EF4444', border: '1px solid #EF4444',
                  borderRadius: '6px', padding: '8px 16px', cursor: 'pointer',
                  fontSize: '0.8rem', fontWeight: 600,
                }}
              >
                Abort Build
              </button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}

export default BuildProgress;
