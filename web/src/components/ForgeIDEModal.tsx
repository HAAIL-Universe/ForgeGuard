/**
 * ForgeIDEModal — full-screen IDE overlay showing live upgrade execution.
 *
 * Layout: dark terminal aesthetic with:
 *   - Left panel: task tracker (step list with status indicators)
 *   - Right panel: live activity log (auto-scrolling terminal)
 *   - Bottom: proposed file changes as they arrive
 *
 * Receives WS events: upgrade_started, upgrade_log, upgrade_task_start,
 * upgrade_task_complete, upgrade_file_diff, upgrade_complete.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { useWebSocket } from '../hooks/useWebSocket';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

/* ---------- Types ---------- */

interface UpgradeTask {
  id: string;
  name: string;
  priority: string;
  effort: string;
  forge_automatable: boolean;
  category: string;
  status: 'pending' | 'running' | 'proposed' | 'skipped' | 'error';
  changes_count?: number;
  steps?: string[];
  worker?: 'opus' | 'sonnet' | 'haiku';
}

interface TokenBucket {
  input: number;
  output: number;
  total: number;
}

interface TokenUsage {
  opus: TokenBucket;
  sonnet: TokenBucket;
  haiku: TokenBucket;
  total: number;
}

const EMPTY_BUCKET: TokenBucket = { input: 0, output: 0, total: 0 };
const EMPTY_TOKENS: TokenUsage = { opus: { ...EMPTY_BUCKET }, sonnet: { ...EMPTY_BUCKET }, haiku: { ...EMPTY_BUCKET }, total: 0 };

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

const WORKER_COLORS: Record<string, string> = {
  opus: '#D946EF',   // purple
  sonnet: '#38BDF8', // blue
  haiku: '#F472B6',  // pink
};

interface LogEntry {
  timestamp: string;
  source: string;
  level: string;
  message: string;
}

interface FileDiff {
  task_id: string;
  file: string;
  action: string;
  description: string;
  before_snippet?: string;
  after_snippet?: string;
}

interface ForgeIDEModalProps {
  runId: string;
  repoName: string;
  onClose: () => void;
}

/* ---------- Level colors ---------- */

const LEVEL_COLORS: Record<string, string> = {
  info: '#E2E8F0',
  warn: '#EAB308',
  error: '#EF4444',
  system: '#38BDF8',
  thinking: '#A78BFA',
  debug: '#64748B',
  command: '#22C55E',
};

const LEVEL_ICONS: Record<string, string> = {
  info: '',
  warn: '⚠',
  error: '✗',
  system: '▸',
  thinking: '◉',
};

/* ---------- Component ---------- */

export default function ForgeIDEModal({ runId, repoName, onClose }: ForgeIDEModalProps) {
  const { token } = useAuth();

  /* State */
  const [tasks, setTasks] = useState<UpgradeTask[]>([]);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [fileDiffs, setFileDiffs] = useState<FileDiff[]>([]);
  const [status, setStatus] = useState<'connecting' | 'running' | 'paused' | 'stopping' | 'stopped' | 'completed' | 'error'>('connecting');
  const [totalTasks, setTotalTasks] = useState(0);
  const [completedTasks, setCompletedTasks] = useState(0);
  const [expandedDiff, setExpandedDiff] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<'activity' | 'changes'>('activity');
  const [tokenUsage, setTokenUsage] = useState<TokenUsage>({ ...EMPTY_TOKENS });
  const [narratorEnabled, setNarratorEnabled] = useState(false);
  const [narrations, setNarrations] = useState<{ text: string; timestamp: string }[]>([]);
  const [leftTab, setLeftTab] = useState<'tasks' | 'narrator'>('tasks');
  const [narratorLoading, setNarratorLoading] = useState(false);
  const [cmdInput, setCmdInput] = useState('');
  const [cmdSuggestions, setCmdSuggestions] = useState<string[]>([]);
  const [cmdHistoryArr, setCmdHistoryArr] = useState<string[]>([]);
  const [historyIdx, setHistoryIdx] = useState(-1);
  const cmdInputRef = useRef<HTMLInputElement>(null);

  const SLASH_COMMANDS: Record<string, string> = {
    '/pause':  'Pause execution after current task finishes',
    '/resume': 'Resume a paused execution',
    '/stop':   'Abort execution (current task finishes first)',
    '/push':   'Show proposed changes & push readiness',
    '/status': 'Print current progress summary',
    '/help':   'Show available slash commands',
    '/clear':  'Clear the activity log',
  };

  /* Refs */
  const logEndRef = useRef<HTMLDivElement>(null);
  const userScrolled = useRef(false);
  const logContainerRef = useRef<HTMLDivElement>(null);
  const startedRef = useRef(false);

  /* Auto-scroll management */
  const handleScroll = useCallback(() => {
    const el = logContainerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    userScrolled.current = !atBottom;
  }, []);

  useEffect(() => {
    if (!userScrolled.current && logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs.length]);

  /* Start execution (guarded against React strict-mode double-fire) */
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    const start = async () => {
      try {
        const res = await fetch(`${API_BASE}/scout/runs/${runId}/execute-upgrade`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const data = await res.json();
          setStatus('running');
          setTotalTasks(data.total_tasks);
        } else {
          const err = await res.json().catch(() => ({ detail: 'Failed to start' }));
          const detail = err.detail || 'Failed to start upgrade';
          // "already in progress" means a prior call succeeded — treat as running
          if (typeof detail === 'string' && detail.toLowerCase().includes('already in progress')) {
            setStatus('running');
            return;
          }
          setLogs((prev) => [
            ...prev,
            { timestamp: new Date().toISOString(), source: 'system', level: 'error', message: detail },
          ]);
          setStatus('error');
        }
      } catch {
        setLogs((prev) => [
          ...prev,
          { timestamp: new Date().toISOString(), source: 'system', level: 'error', message: 'Network error starting upgrade' },
        ]);
        setStatus('error');
      }
    };
    start();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, token]);

  /* WS event handler */
  useWebSocket(
    useCallback(
      (data: { type: string; payload: any }) => {
        const p = data.payload;
        if (!p || p.run_id !== runId) return;

        switch (data.type) {
          case 'upgrade_started':
            setStatus('running');
            setTotalTasks(p.total_tasks || 0);
            if (p.narrator_enabled != null) setNarratorEnabled(p.narrator_enabled);
            if (Array.isArray(p.tasks)) {
              setTasks(p.tasks.map((t: any) => ({ ...t, status: 'pending', worker: t.worker })));
            }
            break;

          case 'upgrade_log':
            setLogs((prev) => [...prev, {
              timestamp: p.timestamp,
              source: p.source,
              level: p.level,
              message: p.message,
            }]);
            break;

          case 'upgrade_task_start':
            setTasks((prev) =>
              prev.map((t) =>
                t.id === p.task_id ? { ...t, status: 'running', steps: p.steps, worker: p.worker || t.worker } : t,
              ),
            );
            break;

          case 'upgrade_task_complete':
            setTasks((prev) =>
              prev.map((t) =>
                t.id === p.task_id
                  ? { ...t, status: p.status || 'proposed', changes_count: p.changes_count, worker: p.worker || t.worker }
                  : t,
              ),
            );
            setCompletedTasks((prev) => Math.max(prev, prev + 1));
            if (p.token_cumulative) {
              setTokenUsage({
                opus: p.token_cumulative.opus || { ...EMPTY_BUCKET },
                sonnet: p.token_cumulative.sonnet || { ...EMPTY_BUCKET },
                haiku: p.token_cumulative.haiku || { ...EMPTY_BUCKET },
                total: p.token_cumulative.total || 0,
              });
            }
            break;

          case 'upgrade_token_tick':
            setTokenUsage({
              opus: p.opus || { ...EMPTY_BUCKET },
              sonnet: p.sonnet || { ...EMPTY_BUCKET },
              haiku: p.haiku || { ...EMPTY_BUCKET },
              total: p.total || 0,
            });
            break;

          case 'upgrade_file_diff':
            setFileDiffs((prev) => [...prev, {
              task_id: p.task_id,
              file: p.file,
              action: p.action,
              description: p.description,
              before_snippet: p.before_snippet,
              after_snippet: p.after_snippet,
            }]);
            break;

          case 'upgrade_complete':
            setStatus(p.status === 'error' ? 'error' : p.status === 'stopped' ? 'stopped' : 'completed');
            if (p.tokens) {
              setTokenUsage({
                opus: p.tokens.opus || { ...EMPTY_BUCKET },
                sonnet: p.tokens.sonnet || { ...EMPTY_BUCKET },
                haiku: p.tokens.haiku || { ...EMPTY_BUCKET },
                total: p.tokens.total || 0,
              });
            }
            break;

          case 'upgrade_paused':
            setStatus('paused');
            break;

          case 'upgrade_resumed':
            setStatus('running');
            break;

          case 'upgrade_stopping':
            setStatus('stopping');
            break;

          case 'upgrade_clear_logs':
            setLogs([]);
            break;

          case 'upgrade_narration':
            setNarrations((prev) => [...prev, { text: p.text, timestamp: p.timestamp }]);
            setNarratorLoading(false);
            break;
        }
      },
      [runId],
    ),
  );

  /* Polling fallback */
  useEffect(() => {
    if (status !== 'running' && status !== 'paused') return;
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/scout/runs/${runId}/upgrade-status`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) return;
        const data = await res.json();
        setCompletedTasks(data.completed_tasks || 0);
        if (data.tokens) {
          setTokenUsage({
            opus: data.tokens.opus || { ...EMPTY_BUCKET },
            sonnet: data.tokens.sonnet || { ...EMPTY_BUCKET },
            haiku: data.tokens.haiku || { ...EMPTY_BUCKET },
            total: data.tokens.total || 0,
          });
        }
        if (data.narrator_enabled != null) setNarratorEnabled(data.narrator_enabled);
        if (data.status === 'paused') setStatus('paused');
        else if (data.status === 'completed' || data.status === 'error' || data.status === 'stopped') {
          setStatus(data.status as any);
          clearInterval(interval);
        }
        // Backfill logs if WS missed them
        if (Array.isArray(data.logs) && data.logs.length > logs.length) {
          setLogs(data.logs);
        }
      } catch { /* silent */ }
    }, 4000);
    return () => clearInterval(interval);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, runId, token]);

  /* Send a slash command */
  const sendCmd = useCallback(async (cmd: string) => {
    const trimmed = cmd.trim();
    if (!trimmed) return;
    setCmdHistoryArr((prev) => [trimmed, ...prev.filter((c) => c !== trimmed)].slice(0, 50));
    setHistoryIdx(-1);
    setCmdInput('');
    setCmdSuggestions([]);

    // Render user input as a log line locally
    setLogs((prev) => [...prev, {
      timestamp: new Date().toISOString(),
      source: 'user',
      level: 'system',
      message: `> ${trimmed}`,
    }]);

    try {
      await fetch(`${API_BASE}/scout/runs/${runId}/command`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ command: trimmed }),
      });
    } catch {
      setLogs((prev) => [...prev, {
        timestamp: new Date().toISOString(),
        source: 'system',
        level: 'error',
        message: 'Failed to send command (network error)',
      }]);
    }
  }, [runId, token]);

  /* Command input change — filter suggestions */
  const handleCmdChange = useCallback((val: string) => {
    setCmdInput(val);
    setHistoryIdx(-1);
    if (val.startsWith('/') && val.length > 0) {
      const matches = Object.keys(SLASH_COMMANDS).filter((c) => c.startsWith(val.toLowerCase()));
      setCmdSuggestions(matches);
    } else {
      setCmdSuggestions([]);
    }
  }, []);

  /* Signal backend when narrator tab is toggled — saves tokens */
  useEffect(() => {
    const watching = leftTab === 'narrator';
    if (watching) setNarratorLoading(true);
    fetch(`${API_BASE}/scout/runs/${runId}/narrator`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ watching }),
    }).catch(() => { /* silent */ });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leftTab, runId, token]);

  /* Progress percentage */
  const pct = totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0;

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 9999,
      background: 'rgba(0,0,0,0.85)', backdropFilter: 'blur(4px)',
      display: 'flex', flexDirection: 'column',
    }}>
      {/* ── Header Bar ── */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: '12px',
        padding: '10px 20px', background: '#0F172A',
        borderBottom: '1px solid #1E293B', flexShrink: 0,
      }}>
        <div style={{
          width: '10px', height: '10px', borderRadius: '50%',
          background: status === 'running' ? '#22C55E' : status === 'completed' ? '#3B82F6' : status === 'error' ? '#EF4444' : status === 'paused' ? '#F59E0B' : status === 'stopping' || status === 'stopped' ? '#F97316' : '#64748B',
          animation: status === 'running' ? 'pulse 1.5s ease-in-out infinite' : status === 'paused' ? 'pulse 2.5s ease-in-out infinite' : 'none',
        }} />
        <span style={{ fontFamily: 'monospace', fontSize: '0.85rem', fontWeight: 600, color: '#F1F5F9' }}>
          FORGE IDE
        </span>
        <span style={{ color: '#64748B', fontSize: '0.75rem' }}>—</span>
        <span style={{ color: '#94A3B8', fontSize: '0.75rem', fontFamily: 'monospace' }}>
          {repoName}
        </span>

        {/* Status badge */}
        <span style={{
          padding: '2px 10px', borderRadius: '10px', fontSize: '0.65rem',
          fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px',
          background: status === 'running' ? '#14532D' : status === 'completed' ? '#1E3A5F' : status === 'error' ? '#7F1D1D' : status === 'paused' ? '#78350F' : status === 'stopping' || status === 'stopped' ? '#7C2D12' : '#1E293B',
          color: status === 'running' ? '#22C55E' : status === 'completed' ? '#3B82F6' : status === 'error' ? '#EF4444' : status === 'paused' ? '#F59E0B' : status === 'stopping' || status === 'stopped' ? '#F97316' : '#64748B',
        }}>
          {status === 'connecting' ? 'Connecting…' : status}
        </span>

        {/* Progress bar */}
        {totalTasks > 0 && (
          <div style={{ flex: 1, maxWidth: '200px', marginLeft: '8px' }}>
            <div style={{ background: '#1E293B', borderRadius: '4px', height: '6px', overflow: 'hidden' }}>
              <div style={{
                height: '100%', borderRadius: '4px',
                background: status === 'error' ? '#EF4444' : 'linear-gradient(90deg, #3B82F6, #22C55E)',
                width: `${pct}%`, transition: 'width 0.5s ease',
              }} />
            </div>
            <div style={{ fontSize: '0.6rem', color: '#64748B', marginTop: '2px', textAlign: 'right' }}>
              {completedTasks}/{totalTasks} tasks — {pct}%
            </div>
          </div>
        )}

        {/* Token counter */}
        {(tokenUsage.total > 0 || status === 'running') && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: '10px',
            marginLeft: '12px', padding: '3px 12px',
            background: '#1E293B', borderRadius: '8px',
            border: '1px solid #334155',
          }}>
            <span style={{ fontSize: '0.6rem', color: '#64748B', fontWeight: 600, letterSpacing: '0.5px' }}>
              ⚡ TOKENS
            </span>
            {/* Opus counter */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
              <span style={{
                fontSize: '0.5rem', fontWeight: 700, padding: '1px 5px',
                borderRadius: '3px', background: '#D946EF22', color: '#D946EF',
              }}>
                OPUS
              </span>
              <span style={{
                fontSize: '0.7rem', fontFamily: 'monospace', color: '#E2E8F0',
                fontWeight: 600, minWidth: '44px', textAlign: 'right',
              }}>
                {fmtTokens(tokenUsage.opus.total)}
              </span>
            </div>
            {/* Sonnet counter (planning / IDE) */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
              <span style={{
                fontSize: '0.5rem', fontWeight: 700, padding: '1px 5px',
                borderRadius: '3px', background: '#38BDF822', color: '#38BDF8',
              }}>
                SONNET
              </span>
              <span style={{
                fontSize: '0.7rem', fontFamily: 'monospace', color: '#E2E8F0',
                fontWeight: 600, minWidth: '44px', textAlign: 'right',
              }}>
                {fmtTokens(tokenUsage.sonnet.total)}
              </span>
            </div>
            {/* Haiku counter (narrator) */}
            {narratorEnabled && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                <span style={{
                  fontSize: '0.5rem', fontWeight: 700, padding: '1px 5px',
                  borderRadius: '3px', background: '#F472B622', color: '#F472B6',
                }}>
                  HAIKU
                </span>
                <span style={{
                  fontSize: '0.7rem', fontFamily: 'monospace', color: '#E2E8F0',
                  fontWeight: 600, minWidth: '44px', textAlign: 'right',
                }}>
                  {fmtTokens(tokenUsage.haiku.total)}
                </span>
              </div>
            )}
            {/* Total */}
            <div style={{ borderLeft: '1px solid #334155', paddingLeft: '8px' }}>
              <span style={{
                fontSize: '0.65rem', fontFamily: 'monospace', color: '#94A3B8',
              }}>
                Σ {fmtTokens(tokenUsage.total)}
              </span>
            </div>
          </div>
        )}

        <div style={{ flex: 1 }} />

        {/* Close button */}
        <button
          onClick={onClose}
          style={{
            background: status === 'running' ? '#334155' : '#1E293B',
            color: '#94A3B8', border: '1px solid #334155', borderRadius: '6px',
            padding: '4px 14px', cursor: 'pointer', fontSize: '0.75rem',
          }}
        >
          {status === 'running' ? '⏹ Close' : status === 'paused' ? '⏸ Close' : '✕ Close'}
        </button>
      </div>

      {/* ── Main Content ── */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>

        {/* ── Left Panel: Tasks / Narrator ── */}
        <div style={{
          width: '280px', flexShrink: 0,
          background: '#0F172A', borderRight: '1px solid #1E293B',
          display: 'flex', flexDirection: 'column',
        }}>
          {/* Left tab switcher */}
          <div style={{
            display: 'flex', borderBottom: '1px solid #1E293B', flexShrink: 0,
          }}>
            {(['tasks', 'narrator'] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setLeftTab(tab)}
                style={{
                  flex: 1, padding: '7px 0', fontSize: '0.6rem', fontWeight: 600,
                  background: 'transparent', border: 'none', cursor: 'pointer',
                  color: leftTab === tab ? '#F1F5F9' : '#475569',
                  borderBottom: leftTab === tab ? '2px solid #3B82F6' : '2px solid transparent',
                  textTransform: 'uppercase', letterSpacing: '0.5px',
                }}
              >
                {tab === 'tasks' ? `Tasks (${tasks.length})` : `🎙️ Narrator${narratorEnabled ? '' : ' ⏻'}`}
              </button>
            ))}
          </div>

          {/* Tasks tab */}
          {leftTab === 'tasks' && (
            <div style={{ flex: 1, overflowY: 'auto', padding: '12px' }}>
              {tasks.length === 0 && status === 'connecting' && (
                <div style={{ color: '#475569', fontSize: '0.75rem', padding: '8px' }}>
                  Initializing…
                </div>
              )}

              {tasks.map((task, i) => (
                <div
                  key={task.id}
                  style={{
                    display: 'flex', flexDirection: 'column', gap: '4px',
                    padding: '8px 10px', marginBottom: '4px',
                    background: task.status === 'running' ? '#1E293B' : 'transparent',
                    borderRadius: '6px',
                    borderLeft: `3px solid ${
                      task.status === 'running' ? '#3B82F6' :
                      task.status === 'proposed' ? '#22C55E' :
                      task.status === 'skipped' ? '#F59E0B' :
                      task.status === 'error' ? '#EF4444' : '#334155'
                    }`,
                    transition: 'all 0.3s ease',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <span style={{ fontSize: '0.85rem', flexShrink: 0 }}>
                      {task.status === 'running' ? '⚡' :
                      task.status === 'proposed' ? '✅' :
                      task.status === 'skipped' ? '⏭' :
                      task.status === 'error' ? '❌' : '○'}
                    </span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{
                        fontSize: '0.75rem', fontWeight: 500,
                        color: task.status === 'running' ? '#F1F5F9' :
                              task.status === 'proposed' ? '#22C55E' :
                              task.status === 'pending' ? '#94A3B8' : '#64748B',
                        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                      }}>
                        {task.name}
                      </div>
                      <div style={{ display: 'flex', gap: '4px', marginTop: '2px' }}>
                        <span style={{
                          fontSize: '0.55rem', padding: '0 4px', borderRadius: '3px',
                          background: task.priority === 'high' ? '#7F1D1D' : task.priority === 'medium' ? '#78350F' : '#1E3A5F',
                          color: task.priority === 'high' ? '#EF4444' : task.priority === 'medium' ? '#F59E0B' : '#3B82F6',
                        }}>
                          {task.priority}
                        </span>
                        {task.worker && (
                          <span style={{
                            fontSize: '0.5rem', fontWeight: 700, padding: '0 4px', borderRadius: '3px',
                            background: (WORKER_COLORS[task.worker] || '#64748B') + '22',
                            color: WORKER_COLORS[task.worker] || '#64748B',
                            letterSpacing: '0.3px',
                          }}>
                            {task.worker.toUpperCase()}
                          </span>
                        )}
                        <span style={{ fontSize: '0.55rem', color: '#475569' }}>
                          {task.category}
                        </span>
                      </div>
                    </div>
                    {task.changes_count != null && task.changes_count > 0 && (
                      <span style={{
                        fontSize: '0.6rem', color: '#22C55E', fontFamily: 'monospace',
                      }}>
                        +{task.changes_count}
                      </span>
                    )}
                  </div>

                  {/* Running: show animated dots */}
                  {task.status === 'running' && (
                    <div style={{
                      fontSize: '0.65rem', color: '#3B82F6', marginTop: '2px',
                      fontFamily: 'monospace',
                    }}>
                      <span className="forge-ide-dots">Analyzing</span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Narrator tab */}
          {leftTab === 'narrator' && (
            <div style={{ flex: 1, overflowY: 'auto', padding: '12px' }}>
              {!narratorEnabled ? (
                <div style={{ color: '#475569', fontSize: '0.75rem', padding: '8px', lineHeight: 1.6 }}>
                  <div style={{ color: '#F59E0B', fontSize: '0.7rem', marginBottom: '8px' }}>⚠ Narrator unavailable</div>
                  Add a second Anthropic API key in Settings to enable the Haiku narrator.
                  It provides plain-English explanations of what's happening during the upgrade.
                </div>
              ) : narrations.length === 0 ? (
                <div style={{ color: '#475569', fontSize: '0.75rem', padding: '8px', lineHeight: 1.6 }}>
                  {narratorLoading ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#F472B6' }}>
                      <span className="forge-ide-dots">Loading narration</span>
                    </div>
                  ) : (
                    <>
                      <div style={{ color: '#F472B6', fontSize: '0.7rem', marginBottom: '8px' }}>🎙️ Narrator Ready</div>
                      Narration will appear here as tasks are processed.
                      Powered by Haiku — lightweight, fast, and cheap.
                    </>
                  )}
                </div>
              ) : (
                narrations.map((n, i) => {
                  const ts = n.timestamp ? new Date(n.timestamp).toLocaleTimeString() : '';
                  return (
                    <div
                      key={i}
                      style={{
                        padding: '10px 12px', marginBottom: '8px',
                        background: '#1E293B', borderRadius: '8px',
                        borderLeft: '3px solid #F472B6',
                      }}
                    >
                      <div style={{
                        fontSize: '0.55rem', color: '#64748B', marginBottom: '4px',
                        display: 'flex', alignItems: 'center', gap: '6px',
                      }}>
                        <span style={{
                          fontSize: '0.5rem', fontWeight: 700, padding: '1px 5px',
                          borderRadius: '3px', background: '#F472B622', color: '#F472B6',
                        }}>
                          HAIKU
                        </span>
                        <span>{ts}</span>
                      </div>
                      <div style={{
                        fontSize: '0.8rem', color: '#E2E8F0', lineHeight: 1.5,
                        fontFamily: 'system-ui, -apple-system, sans-serif',
                      }}>
                        {n.text}
                      </div>
                    </div>
                  );
                })
              )}
              {narratorLoading && narrations.length > 0 && (
                <div style={{
                  padding: '8px 12px', color: '#F472B6', fontSize: '0.7rem',
                  fontFamily: 'monospace',
                }}>
                  <span className="forge-ide-dots">Narrating</span>
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Right Panel: Activity Log + Changes ── */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: '#0B1120' }}>

          {/* Tab bar */}
          <div style={{
            display: 'flex', borderBottom: '1px solid #1E293B', flexShrink: 0,
          }}>
            {(['activity', 'changes'] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                style={{
                  padding: '8px 20px', fontSize: '0.75rem', fontWeight: 500,
                  background: 'transparent', border: 'none', cursor: 'pointer',
                  color: activeTab === tab ? '#F1F5F9' : '#64748B',
                  borderBottom: activeTab === tab ? '2px solid #3B82F6' : '2px solid transparent',
                  textTransform: 'uppercase', letterSpacing: '0.5px',
                }}
              >
                {tab === 'activity' ? `Activity (${logs.length})` : `Changes (${fileDiffs.length})`}
              </button>
            ))}
          </div>

          {/* Activity log */}
          {activeTab === 'activity' && (
            <div
              ref={logContainerRef}
              onScroll={handleScroll}
              style={{
                flex: 1, overflowY: 'auto', padding: '12px 16px',
                fontFamily: '"Cascadia Code", "Fira Code", "JetBrains Mono", monospace',
                fontSize: '0.75rem', lineHeight: 1.7,
              }}
            >
              {logs.length === 0 ? (
                <div style={{ color: '#475569', padding: '20px 0' }}>
                  {status === 'connecting' ? 'Connecting to Forge IDE…' : 'Waiting for output…'}
                </div>
              ) : (
                logs.map((log, i) => {
                  const color = LEVEL_COLORS[log.level] ?? LEVEL_COLORS.info;
                  const icon = LEVEL_ICONS[log.level] ?? '';
                  const ts = log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : '';
                  const isThinking = log.level === 'thinking';
                  return (
                    <div
                      key={i}
                      style={{
                        color,
                        display: 'flex', gap: '8px',
                        padding: isThinking ? '1px 0 1px 12px' : '1px 0',
                        borderLeft: isThinking ? '2px solid #7C3AED33' : 'none',
                        opacity: log.level === 'debug' ? 0.5 : 1,
                      }}
                    >
                      <span style={{ color: '#334155', flexShrink: 0, width: '70px', fontSize: '0.65rem', paddingTop: '2px' }}>
                        {ts}
                      </span>
                      {icon && (
                        <span style={{ flexShrink: 0, width: '14px', textAlign: 'center', fontSize: '0.7rem' }}>
                          {icon}
                        </span>
                      )}
                      <span style={{ wordBreak: 'break-word' }}>
                        {log.message}
                      </span>
                    </div>
                  );
                })
              )}
              <div ref={logEndRef} />
            </div>
          )}

          {/* Command input bar — always visible on activity tab */}
          {activeTab === 'activity' && (
            <div style={{
              flexShrink: 0, borderTop: '1px solid #1E293B',
              background: '#0F172A', padding: '0', position: 'relative',
            }}>
              {/* Autocomplete dropdown */}
              {cmdSuggestions.length > 0 && (
                <div style={{
                  position: 'absolute', bottom: '100%', left: 0, right: 0,
                  background: '#1E293B', border: '1px solid #334155',
                  borderBottom: 'none', borderRadius: '6px 6px 0 0',
                  maxHeight: '200px', overflowY: 'auto', zIndex: 10,
                }}>
                  {cmdSuggestions.map((cmd) => (
                    <div
                      key={cmd}
                      onClick={() => { setCmdInput(cmd + ' '); setCmdSuggestions([]); cmdInputRef.current?.focus(); }}
                      style={{
                        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                        padding: '6px 12px', cursor: 'pointer',
                        borderBottom: '1px solid #334155',
                      }}
                      onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = '#334155'; }}
                      onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'transparent'; }}
                    >
                      <span style={{ fontFamily: 'monospace', fontSize: '0.8rem', color: '#22C55E', fontWeight: 600 }}>
                        {cmd}
                      </span>
                      <span style={{ fontSize: '0.65rem', color: '#64748B', marginLeft: '12px' }}>
                        {SLASH_COMMANDS[cmd]}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              <div style={{
                display: 'flex', alignItems: 'center', gap: '8px',
                padding: '8px 12px',
              }}>
                <span style={{
                  color: '#22C55E', fontFamily: 'monospace', fontSize: '0.8rem',
                  fontWeight: 700, flexShrink: 0,
                }}>
                  ❯
                </span>
                <input
                  ref={cmdInputRef}
                  type="text"
                  value={cmdInput}
                  onChange={(e) => handleCmdChange(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      // If suggestions visible and only 1, auto-pick it
                      if (cmdSuggestions.length === 1 && cmdInput.startsWith('/') && !cmdInput.includes(' ')) {
                        sendCmd(cmdSuggestions[0]);
                      } else {
                        sendCmd(cmdInput);
                      }
                    } else if (e.key === 'Escape') {
                      setCmdSuggestions([]);
                    } else if (e.key === 'Tab' && cmdSuggestions.length > 0) {
                      e.preventDefault();
                      setCmdInput(cmdSuggestions[0]);
                      setCmdSuggestions([]);
                    } else if (e.key === 'ArrowUp') {
                      e.preventDefault();
                      if (cmdHistoryArr.length > 0) {
                        const next = Math.min(historyIdx + 1, cmdHistoryArr.length - 1);
                        setHistoryIdx(next);
                        setCmdInput(cmdHistoryArr[next]);
                        setCmdSuggestions([]);
                      }
                    } else if (e.key === 'ArrowDown') {
                      e.preventDefault();
                      if (historyIdx > 0) {
                        const next = historyIdx - 1;
                        setHistoryIdx(next);
                        setCmdInput(cmdHistoryArr[next]);
                      } else {
                        setHistoryIdx(-1);
                        setCmdInput('');
                      }
                      setCmdSuggestions([]);
                    }
                  }}
                  placeholder="Type / for commands\u2026"
                  style={{
                    flex: 1, background: 'transparent', border: 'none', outline: 'none',
                    color: '#E2E8F0', fontFamily: '"Cascadia Code", "Fira Code", "JetBrains Mono", monospace',
                    fontSize: '0.8rem', caretColor: '#22C55E',
                  }}
                  autoComplete="off"
                  spellCheck={false}
                />
                {/* Quick action buttons */}
                {status === 'running' && (
                  <button
                    onClick={() => sendCmd('/pause')}
                    title="Pause"
                    style={{
                      background: '#78350F', color: '#F59E0B', border: 'none',
                      borderRadius: '4px', padding: '2px 8px', cursor: 'pointer',
                      fontSize: '0.65rem', fontWeight: 600,
                    }}
                  >
                    ⏸ Pause
                  </button>
                )}
                {status === 'paused' && (
                  <button
                    onClick={() => sendCmd('/resume')}
                    title="Resume"
                    style={{
                      background: '#14532D', color: '#22C55E', border: 'none',
                      borderRadius: '4px', padding: '2px 8px', cursor: 'pointer',
                      fontSize: '0.65rem', fontWeight: 600,
                    }}
                  >
                    ▶ Resume
                  </button>
                )}
                {(status === 'running' || status === 'paused') && (
                  <button
                    onClick={() => sendCmd('/stop')}
                    title="Stop"
                    style={{
                      background: '#7F1D1D', color: '#EF4444', border: 'none',
                      borderRadius: '4px', padding: '2px 8px', cursor: 'pointer',
                      fontSize: '0.65rem', fontWeight: 600,
                    }}
                  >
                    ⏹ Stop
                  </button>
                )}
              </div>
            </div>
          )}

          {/* File changes tab */}
          {activeTab === 'changes' && (
            <div style={{ flex: 1, overflowY: 'auto', padding: '12px 16px' }}>
              {fileDiffs.length === 0 ? (
                <div style={{ color: '#475569', padding: '20px 0', fontSize: '0.8rem' }}>
                  {status === 'running' ? 'File changes will appear here as tasks are analysed…' : 'No file changes proposed.'}
                </div>
              ) : (
                fileDiffs.map((diff, i) => {
                  const actionColor = diff.action === 'create' ? '#22C55E' : diff.action === 'delete' ? '#EF4444' : '#3B82F6';
                  const actionIcon = diff.action === 'create' ? '➕' : diff.action === 'delete' ? '🗑️' : '✏️';
                  const isExpanded = expandedDiff === i;
                  return (
                    <div
                      key={i}
                      style={{
                        marginBottom: '6px', background: '#0F172A',
                        borderRadius: '6px', border: '1px solid #1E293B',
                        overflow: 'hidden',
                      }}
                    >
                      <div
                        onClick={() => setExpandedDiff(isExpanded ? null : i)}
                        style={{
                          display: 'flex', alignItems: 'center', gap: '8px',
                          padding: '8px 12px', cursor: 'pointer',
                        }}
                      >
                        <span>{actionIcon}</span>
                        <span style={{
                          fontFamily: 'monospace', fontSize: '0.8rem', color: '#E2E8F0',
                        }}>
                          {diff.file}
                        </span>
                        <span style={{
                          fontSize: '0.6rem', fontWeight: 600, padding: '1px 6px',
                          borderRadius: '4px', background: actionColor + '22', color: actionColor,
                          textTransform: 'uppercase',
                        }}>
                          {diff.action}
                        </span>
                        <span style={{ flex: 1 }} />
                        <span style={{ fontSize: '0.7rem', color: '#64748B' }}>
                          {diff.task_id}
                        </span>
                        <span style={{ color: '#475569', fontSize: '0.7rem' }}>
                          {isExpanded ? '▾' : '▸'}
                        </span>
                      </div>

                      {isExpanded && (
                        <div style={{ padding: '0 12px 12px', borderTop: '1px solid #1E293B' }}>
                          <div style={{ fontSize: '0.75rem', color: '#94A3B8', padding: '8px 0 6px' }}>
                            {diff.description}
                          </div>
                          {diff.before_snippet && (
                            <div style={{ marginBottom: '6px' }}>
                              <div style={{ fontSize: '0.6rem', color: '#EF4444', textTransform: 'uppercase', marginBottom: '2px' }}>
                                Before
                              </div>
                              <pre style={{
                                background: '#1A0000', border: '1px solid #7F1D1D',
                                borderRadius: '4px', padding: '8px', fontSize: '0.7rem',
                                color: '#FCA5A5', overflow: 'auto', maxHeight: '150px', margin: 0,
                                fontFamily: '"Cascadia Code", monospace',
                              }}>
                                {diff.before_snippet}
                              </pre>
                            </div>
                          )}
                          {diff.after_snippet && (
                            <div>
                              <div style={{ fontSize: '0.6rem', color: '#22C55E', textTransform: 'uppercase', marginBottom: '2px' }}>
                                After
                              </div>
                              <pre style={{
                                background: '#001A00', border: '1px solid #14532D',
                                borderRadius: '4px', padding: '8px', fontSize: '0.7rem',
                                color: '#86EFAC', overflow: 'auto', maxHeight: '150px', margin: 0,
                                fontFamily: '"Cascadia Code", monospace',
                              }}>
                                {diff.after_snippet}
                              </pre>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          )}
        </div>
      </div>

      {/* ── Footer ── */}
      {(status === 'completed' || status === 'stopped') && (
        <div style={{
          padding: '12px 20px', background: '#0F172A', borderTop: '1px solid #1E293B',
          display: 'flex', alignItems: 'center', gap: '12px', flexShrink: 0,
        }}>
          <span style={{ fontSize: '0.8rem', color: status === 'stopped' ? '#F97316' : '#22C55E' }}>
            {status === 'stopped' ? '🛑 Execution stopped by user' : '✅ Upgrade analysis complete'}
          </span>
          <span style={{ color: '#64748B', fontSize: '0.75rem' }}>
            {fileDiffs.length} file change(s) proposed across {completedTasks} task(s)
            {tokenUsage.total > 0 && ` · ${fmtTokens(tokenUsage.total)} tokens used`}
          </span>
          <div style={{ flex: 1 }} />
          <button
            onClick={() => setActiveTab('changes')}
            style={{
              background: '#1E3A5F', color: '#3B82F6', border: 'none',
              borderRadius: '6px', padding: '6px 16px', cursor: 'pointer',
              fontSize: '0.75rem', fontWeight: 500,
            }}
          >
            📝 View Changes
          </button>
          <button
            onClick={onClose}
            style={{
              background: '#334155', color: '#F1F5F9', border: 'none',
              borderRadius: '6px', padding: '6px 16px', cursor: 'pointer',
              fontSize: '0.75rem', fontWeight: 500,
            }}
          >
            Done
          </button>
        </div>
      )}

      {/* CSS animation for pulse + dots */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
        .forge-ide-dots::after {
          content: '';
          animation: dots 1.5s steps(4, end) infinite;
        }
        @keyframes dots {
          0% { content: ''; }
          25% { content: '.'; }
          50% { content: '..'; }
          75% { content: '...'; }
        }
      `}</style>
    </div>
  );
}
