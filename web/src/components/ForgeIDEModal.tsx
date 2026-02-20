/**
 * ForgeIDEModal — full-screen IDE overlay showing live upgrade execution.
 *
 * Layout: dark terminal aesthetic with:
 *   - Left panel: task tracker (step list with status indicators)
 *   - Right panel: live activity log (auto-scrolling terminal)
 *   - Bottom: proposed file changes as they arrive
 *
 * Receives WS events: upgrade_started, upgrade_log, upgrade_task_start,
 * upgrade_task_complete, upgrade_file_diff, upgrade_file_checklist,
 * upgrade_file_progress, upgrade_complete.
 */
import { useState, useEffect, useRef, useCallback, memo, useMemo } from 'react';
import { useAuth } from '../context/AuthContext';
import { useWebSocket } from '../hooks/useWebSocket';
import ErrorsPanel, { type BuildError } from './ErrorsPanel';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

/* ---------- Error fingerprinting (mirrors backend logic) ---------- */

function errorFingerprint(source: string, severity: string, message: string): string {
  let normalized = message.replace(/line \d+/g, 'line N');
  normalized = normalized.replace(/0x[0-9a-fA-F]+/g, '0xADDR');
  normalized = normalized.replace(
    /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi,
    'UUID',
  );
  return `${source}:${severity}:${normalized}`;
}

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
  worker?: 'sonnet' | 'opus' | 'system';
  /** Present when this log entry is a clickable scratchpad write */
  scratchpad?: {
    key: string;
    content: string;
    fullLength: number;
    role: string;
  };
  /** Present when this is a tier progress entry */
  tier?: {
    index: number;
    action: 'start' | 'complete';
    fileCount: number;
    files?: string[];
  };
  /** Present when this is an LLM thinking/prompt event */
  thinking?: {
    purpose: string;
    systemPrompt: string;
    userMessagePreview: string;
    userMessageLength: number;
    file?: string;
    contractsIncluded?: string[];
    contextFiles?: string[];
    files?: string[];
  };
}

function classifyWorker(msg: string): 'sonnet' | 'opus' | 'system' {
  if (msg.includes('[Sonnet]')) return 'sonnet';
  if (msg.includes('[Opus]') || msg.includes('[Opus-2]')) return 'opus';
  return 'system';
}

interface FileDiff {
  task_id: string;
  file: string;
  action: string;
  description: string;
  before_snippet?: string;
  after_snippet?: string;
  audit_status?: 'pending' | 'auditing' | 'passed' | 'failed';
  findings?: string[];
}

interface ChecklistItem {
  file: string;
  action: string;
  description: string;
  status: 'pending' | 'written' | 'auditing' | 'passed' | 'failed' | 'rejected';
  tierIndex?: number;
}

/* ---------- Agent tracking types ---------- */

interface AgentFileEntry {
  path: string;
  displayPath: string;
  status: 'pending' | 'building' | 'done';
}

interface AgentInfo {
  agentId: string;
  tier: number;
  files: AgentFileEntry[];
  status: 'running' | 'done';
  startedAt: string;
}

interface TierAgentGroup {
  tier: number;
  agents: AgentInfo[];
  commonPrefix: string;
}

/* ---------- Build mode types ---------- */

interface BuildPhase {
  number: number;
  name: string;
  objective: string;
  deliverables?: string[];
}

interface CostData {
  total_cost_usd: number;
  api_calls: number;
  tokens_in: number;
  tokens_out: number;
  spend_cap: number | null;
  pct_used: number;
}

/* Per-million pricing (matches backend _MODEL_PRICING) */
const TOKEN_PRICING = {
  opus:   { input: 15, output: 75 },
  sonnet: { input: 3,  output: 15 },
  haiku:  { input: 1,  output: 5 },
};

function estimateCost(usage: TokenUsage): number {
  return (
    (usage.opus.input * TOKEN_PRICING.opus.input +
     usage.opus.output * TOKEN_PRICING.opus.output +
     usage.sonnet.input * TOKEN_PRICING.sonnet.input +
     usage.sonnet.output * TOKEN_PRICING.sonnet.output +
     usage.haiku.input * TOKEN_PRICING.haiku.input +
     usage.haiku.output * TOKEN_PRICING.haiku.output) / 1_000_000
  );
}

function fmtCost(n: number): string {
  if (n >= 1) return `$${n.toFixed(2)}`;
  if (n >= 0.01) return `$${n.toFixed(3)}`;
  if (n > 0) return `$${n.toFixed(4)}`;
  return '$0.00';
}

interface ForgeIDEModalProps {
  runId?: string;            // upgrade mode
  projectId?: string;        // build mode
  repoName: string;
  onClose: () => void;
  mode?: 'upgrade' | 'build';
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

/* ---------- File checklist (Opus progress) ---------- */

const ACTION_ICON: Record<string, string> = { modify: '✏️', create: '➕', delete: '🗑️' };

const FileChecklist = memo(function FileChecklist({ items }: { items: ChecklistItem[] }) {
  if (items.length === 0) return null;
  const done = items.filter(i => i.status !== 'pending').length;
  const hasTiers = items.some(i => i.tierIndex !== undefined && i.tierIndex >= 0);

  // Group items by tier if tiers exist
  const tierGroups: Map<number, ChecklistItem[]> = new Map();
  if (hasTiers) {
    for (const it of items) {
      const t = it.tierIndex ?? -1;
      if (!tierGroups.has(t)) tierGroups.set(t, []);
      tierGroups.get(t)!.push(it);
    }
  }

  const renderItem = (item: ChecklistItem, i: number) => {
    const icon = item.status === 'pending' ? '☐'
      : item.status === 'written' ? '☑'
      : item.status === 'passed' ? '✅'
      : item.status === 'failed' ? '❌'
      : item.status === 'rejected' ? '🚫'
      : '⏳';
    const color = item.status === 'pending' ? '#475569'
      : item.status === 'written' ? '#A78BFA'
      : item.status === 'passed' ? '#22C55E'
      : item.status === 'failed' ? '#EF4444'
      : item.status === 'rejected' ? '#EF4444'
      : '#94A3B8';
    const strike = item.status !== 'pending';
    return (
      <div key={i} style={{ display: 'flex', gap: '6px', lineHeight: 1.6, color }}>
        <span style={{ flexShrink: 0, width: '16px' }}>{icon}</span>
        <span style={{ textDecoration: strike ? 'line-through' : 'none', opacity: strike ? 0.6 : 1 }}>
          {ACTION_ICON[item.action] || '📄'} {item.file}
        </span>
      </div>
    );
  };

  return (
    <div style={{
      borderTop: '1px solid #1E293B', padding: '6px 12px',
      background: '#0D0D1A', fontSize: '0.7rem', flexShrink: 0,
      maxHeight: '35vh', overflowY: 'auto',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
        <span style={{ color: '#94A3B8', fontWeight: 700, letterSpacing: '0.5px', fontSize: '0.6rem' }}>FILE PROGRESS</span>
        <span style={{ color: '#94A3B8', fontSize: '0.6rem', fontVariantNumeric: 'tabular-nums' }}>{done}/{items.length}</span>
      </div>
      {/* Progress bar */}
      <div style={{ height: '3px', background: '#1E293B', borderRadius: '2px', marginBottom: '5px', overflow: 'hidden' }}>
        <div style={{
          height: '100%', borderRadius: '2px',
          width: `${(done / items.length) * 100}%`,
          background: 'linear-gradient(90deg, #D946EF, #A855F7)',
          transition: 'width 0.4s ease',
        }} />
      </div>
      {hasTiers ? (
        // Render grouped by tier
        Array.from(tierGroups.entries()).sort(([a], [b]) => a - b).map(([tierIdx, tierItems]) => {
          const tierDone = tierItems.filter(x => x.status !== 'pending').length;
          return (
            <div key={`tier-${tierIdx}`} style={{ marginBottom: '6px' }}>
              <div style={{
                color: '#60A5FA', fontSize: '0.6rem', fontWeight: 600,
                padding: '2px 0', borderBottom: '1px solid #1E293B44',
                marginBottom: '2px',
              }}>
                {tierIdx >= 0 ? `⚡ TIER ${tierIdx}` : '📋 UNGROUPED'} ({tierDone}/{tierItems.length})
              </div>
              {tierItems.map((item, i) => renderItem(item, tierIdx * 1000 + i))}
            </div>
          );
        })
      ) : (
        // Flat list
        items.map((item, i) => renderItem(item, i))
      )}
    </div>
  );
});

/* ---------- Agent Panel (grouped Opus display) ---------- */

const AgentFileRow = memo(function AgentFileRow({ file, accentColor }: { file: AgentFileEntry; accentColor: string }) {
  const icon = file.status === 'done' ? '✅' : file.status === 'building' ? '⏳' : '○';
  const color = file.status === 'done' ? '#22C55E' : file.status === 'building' ? accentColor : '#475569';
  return (
    <div style={{ display: 'flex', gap: '6px', lineHeight: 1.6, color, fontSize: '0.7rem', paddingLeft: '16px' }}>
      <span style={{ flexShrink: 0, width: '14px' }}>{icon}</span>
      <span style={{
        opacity: file.status === 'done' ? 0.6 : 1,
        textDecoration: file.status === 'done' ? 'line-through' : 'none',
      }}>
        {file.displayPath}
      </span>
    </div>
  );
});

const AgentSection = memo(function AgentSection({ agent, accentColor }: { agent: AgentInfo; accentColor: string }) {
  const [collapsed, setCollapsed] = useState(agent.status === 'done');
  const doneCount = agent.files.filter(f => f.status === 'done').length;
  const total = agent.files.length;
  const pct = total > 0 ? Math.round((doneCount / total) * 100) : 0;
  const isDone = agent.status === 'done';

  // Auto-collapse when done, auto-expand when running
  useEffect(() => {
    setCollapsed(isDone);
  }, [isDone]);

  return (
    <div style={{ marginBottom: '4px' }}>
      <div
        onClick={() => setCollapsed(!collapsed)}
        style={{
          display: 'flex', alignItems: 'center', gap: '6px',
          padding: '3px 8px', cursor: 'pointer',
          background: isDone ? '#22C55E08' : `${accentColor}0C`,
          borderLeft: `2px solid ${isDone ? '#22C55E44' : accentColor + '66'}`,
          borderRadius: '2px', fontSize: '0.65rem',
          color: isDone ? '#22C55E' : accentColor,
          fontWeight: 600, letterSpacing: '0.3px',
        }}
      >
        <span style={{ fontSize: '0.6rem' }}>{collapsed ? '▶' : '▼'}</span>
        <span>{agent.agentId.replace('agent-', 'Agent ')}</span>
        <span style={{ color: '#64748B', fontWeight: 400 }}>— Tier {agent.tier}</span>
        <span style={{ marginLeft: 'auto', fontVariantNumeric: 'tabular-nums', color: '#94A3B8', fontWeight: 400 }}>
          ({doneCount}/{total})
        </span>
        {!isDone && (
          <span style={{ fontSize: '0.55rem', color: accentColor }}>⚡</span>
        )}
      </div>
      {/* Mini progress bar */}
      <div style={{ height: '2px', background: '#1E293B', marginTop: '1px', overflow: 'hidden' }}>
        <div style={{
          height: '100%', width: `${pct}%`,
          background: isDone ? '#22C55E' : `linear-gradient(90deg, ${accentColor}, #A855F7)`,
          transition: 'width 0.3s ease',
        }} />
      </div>
      {!collapsed && (
        <div style={{ padding: '2px 0' }}>
          {agent.files.map((f) => (
            <AgentFileRow key={f.path} file={f} accentColor={accentColor} />
          ))}
        </div>
      )}
    </div>
  );
});

const AgentPanel = memo(function AgentPanel({
  agents,
  status,
  opusLogs,
  label,
  labelColor,
  emptyText,
}: {
  agents: AgentInfo[];
  status: string;
  opusLogs: LogEntry[];
  label: string;
  labelColor: string;
  emptyText: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const scrolledAway = useRef(false);
  const prevCount = useRef(agents.length);
  const [expandedThinking, setExpandedThinking] = useState<Set<number>>(new Set());

  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const remaining = el.scrollHeight - el.scrollTop - el.clientHeight;
    scrolledAway.current = remaining > 60;
  }, []);

  useEffect(() => {
    if (agents.length !== prevCount.current) {
      prevCount.current = agents.length;
      if (!scrolledAway.current && containerRef.current) {
        requestAnimationFrame(() => {
          const el = containerRef.current;
          if (el) el.scrollTop = el.scrollHeight;
        });
      }
    }
  }, [agents.length]);

  // Auto-expand the latest thinking entry, collapse the previous one.
  // Flat log fallback uses plain index; agent view uses i+10000 offset.
  const thinkingLogs = opusLogs.filter(l => !!l.thinking);
  useEffect(() => {
    if (agents.length === 0) {
      // Flat log fallback — find latest thinking entry by scanning opusLogs
      let latest = -1;
      for (let j = opusLogs.length - 1; j >= 0; j--) {
        if (opusLogs[j].thinking) { latest = j; break; }
      }
      if (latest >= 0) setExpandedThinking(new Set([latest]));
    } else {
      // Agent-based view — thinking entries use i+10000 offset
      if (thinkingLogs.length > 0) {
        setExpandedThinking(new Set([thinkingLogs.length - 1 + 10000]));
      }
    }
  }, [opusLogs.length, thinkingLogs.length, agents.length]);

  const totalFiles = agents.reduce((n, a) => n + a.files.length, 0);
  const doneFiles = agents.reduce((n, a) => n + a.files.filter(f => f.status === 'done').length, 0);
  const activeAgents = agents.filter(a => a.status === 'running').length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', flex: 1, minHeight: 0 }}>
      {/* Panel header */}
      <div style={{
        padding: '4px 12px', borderBottom: '1px solid #1E293B',
        fontSize: '0.6rem', fontWeight: 700, color: labelColor,
        letterSpacing: '0.5px', background: labelColor + '08',
        display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0,
      }}>
        <span style={{
          width: '6px', height: '6px', borderRadius: '50%',
          background: labelColor, display: 'inline-block',
        }} />
        {label}
        {agents.length > 0 && (
          <span style={{ color: '#475569', fontSize: '0.55rem', fontWeight: 400 }}>
            {activeAgents > 0 ? `${activeAgents} active` : ''} · {doneFiles}/{totalFiles} files
          </span>
        )}
      </div>
      {/* Agent list */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        style={{
          flex: 1, overflowY: 'auto', padding: '6px 8px',
          fontFamily: '"Cascadia Code", "Fira Code", "JetBrains Mono", monospace',
          fontSize: '0.75rem', lineHeight: 1.5,
        }}
      >
        {agents.length === 0 ? (
          opusLogs.length === 0 ? (
            <div style={{ color: '#475569', padding: '12px 0', fontSize: '0.7rem' }}>
              {emptyText}
            </div>
          ) : (
            /* Fall back to flat log display when no agent events yet */
            opusLogs.map((log, i) => {
              const color = LEVEL_COLORS[log.level] ?? LEVEL_COLORS.info;
              const ts = log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : '';
              const isLLMThinking = !!log.thinking;
              const isExpanded = isLLMThinking && expandedThinking.has(i);
              return (
                <div key={i}>
                  <div
                    style={{
                      display: 'flex', gap: '8px', padding: isLLMThinking ? '3px 0 3px 12px' : '1px 0',
                      color: isLLMThinking ? labelColor : color,
                      borderLeft: isLLMThinking ? `2px solid ${labelColor}66` : 'none',
                      background: isLLMThinking ? `${labelColor}0A` : undefined,
                      cursor: isLLMThinking ? 'pointer' : undefined,
                      borderRadius: isLLMThinking ? '2px' : undefined,
                    }}
                    onClick={isLLMThinking ? () => {
                      setExpandedThinking((prev) => {
                        const next = new Set(prev);
                        if (next.has(i)) next.delete(i); else next.add(i);
                        return next;
                      });
                    } : undefined}
                  >
                    <span style={{ color: '#334155', flexShrink: 0, width: '70px', fontSize: '0.65rem' }}>{ts}</span>
                    <span style={{ wordBreak: 'break-word' }}>
                      {log.message}
                      {isLLMThinking && (
                        <span style={{ color: labelColor, fontSize: '0.6rem', marginLeft: '6px', opacity: 0.7 }}>
                          {isExpanded ? '▼' : '▶'} {(log.thinking!.userMessageLength / 1000).toFixed(1)}k chars
                        </span>
                      )}
                    </span>
                  </div>
                  {isExpanded && log.thinking && (
                    <div style={{
                      margin: '2px 0 6px 86px', background: '#0F172A',
                      border: `1px solid ${labelColor}33`, borderRadius: '4px',
                      fontSize: '0.7rem', lineHeight: 1.5, overflow: 'hidden',
                    }}>
                      <div style={{ padding: '6px 10px', borderBottom: `1px solid ${labelColor}22` }}>
                        <div style={{ color: labelColor, fontSize: '0.6rem', fontWeight: 700, marginBottom: '3px' }}>SYSTEM PROMPT</div>
                        <pre style={{ margin: 0, color: '#94A3B8', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: '120px', overflowY: 'auto' }}>
                          <code>{log.thinking.systemPrompt}</code>
                        </pre>
                      </div>
                      <div style={{ padding: '6px 10px' }}>
                        <div style={{ color: labelColor, fontSize: '0.6rem', fontWeight: 700, marginBottom: '3px' }}>
                          USER MESSAGE ({(log.thinking.userMessageLength / 1000).toFixed(1)}k chars)
                        </div>
                        <pre style={{ margin: 0, color: '#CBD5E1', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: '200px', overflowY: 'auto' }}>
                          <code>{log.thinking.userMessagePreview}</code>
                          {log.thinking.userMessageLength > log.thinking.userMessagePreview.length && (
                            <span style={{ color: '#64748B', fontStyle: 'italic' }}>
                              {'\n'}... truncated ({log.thinking.userMessageLength - log.thinking.userMessagePreview.length} more chars)
                            </span>
                          )}
                        </pre>
                      </div>
                    </div>
                  )}
                </div>
              );
            })
          )
        ) : (
          <>
            {/* Show Opus thinking entries above agent sections */}
            {opusLogs.filter(l => !!l.thinking).map((log, i) => {
              const ts = log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : '';
              const isExpanded = expandedThinking.has(i + 10000);
              return (
                <div key={`think-${i}`} style={{ marginBottom: '4px' }}>
                  <div
                    style={{
                      display: 'flex', gap: '8px', padding: '3px 0 3px 12px',
                      color: labelColor, borderLeft: `2px solid ${labelColor}66`,
                      background: `${labelColor}0A`, cursor: 'pointer', borderRadius: '2px',
                    }}
                    onClick={() => {
                      setExpandedThinking((prev) => {
                        const next = new Set(prev);
                        const key = i + 10000;
                        if (next.has(key)) next.delete(key); else next.add(key);
                        return next;
                      });
                    }}
                  >
                    <span style={{ color: '#334155', flexShrink: 0, width: '70px', fontSize: '0.65rem' }}>{ts}</span>
                    <span style={{ wordBreak: 'break-word' }}>
                      {log.message}
                      <span style={{ color: labelColor, fontSize: '0.6rem', marginLeft: '6px', opacity: 0.7 }}>
                        {isExpanded ? '▼' : '▶'} {(log.thinking!.userMessageLength / 1000).toFixed(1)}k chars
                      </span>
                    </span>
                  </div>
                  {isExpanded && log.thinking && (
                    <div style={{
                      margin: '2px 0 6px 86px', background: '#0F172A',
                      border: `1px solid ${labelColor}33`, borderRadius: '4px',
                      fontSize: '0.7rem', lineHeight: 1.5, overflow: 'hidden',
                    }}>
                      <div style={{ padding: '6px 10px', borderBottom: `1px solid ${labelColor}22` }}>
                        <div style={{ color: labelColor, fontSize: '0.6rem', fontWeight: 700, marginBottom: '3px' }}>SYSTEM PROMPT</div>
                        <pre style={{ margin: 0, color: '#94A3B8', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: '120px', overflowY: 'auto' }}>
                          <code>{log.thinking.systemPrompt}</code>
                        </pre>
                      </div>
                      <div style={{ padding: '6px 10px' }}>
                        <div style={{ color: labelColor, fontSize: '0.6rem', fontWeight: 700, marginBottom: '3px' }}>
                          USER MESSAGE ({(log.thinking.userMessageLength / 1000).toFixed(1)}k chars)
                        </div>
                        <pre style={{ margin: 0, color: '#CBD5E1', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: '200px', overflowY: 'auto' }}>
                          <code>{log.thinking.userMessagePreview}</code>
                          {log.thinking.userMessageLength > log.thinking.userMessagePreview.length && (
                            <span style={{ color: '#64748B', fontStyle: 'italic' }}>
                              {'\n'}... truncated ({log.thinking.userMessageLength - log.thinking.userMessagePreview.length} more chars)
                            </span>
                          )}
                        </pre>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
            {agents.map((agent) => (
              <AgentSection key={agent.agentId} agent={agent} accentColor={labelColor} />
            ))}
          </>
        )}
      </div>
    </div>
  );
});

/* ---------- Active log line (own timer) ---------- */

/**
 * A single "thinking" log line that self-updates its elapsed timer
 * every second. Isolates re-renders so the parent log list stays stable.
 */
const ActiveLogLine = memo(function ActiveLogLine({
  log,
  color,
  icon,
  accentColor,
}: {
  log: LogEntry;
  color: string;
  icon: string;
  accentColor: string;
}) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!log.timestamp) return;
    // Seed with current elapsed so it doesn't flash "0s"
    setElapsed(Math.max(0, Math.floor((Date.now() - new Date(log.timestamp).getTime()) / 1000)));
    const id = setInterval(() => {
      setElapsed(Math.max(0, Math.floor((Date.now() - new Date(log.timestamp).getTime()) / 1000)));
    }, 1000);
    return () => clearInterval(id);
  }, [log.timestamp]);

  const ts = log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : '';
  const elapsedLabel = elapsed >= 3 ? `${elapsed}s` : '';

  return (
    <div
      style={{
        color,
        display: 'flex',
        padding: '1px 0 1px 12px',
        borderLeft: `2px solid ${accentColor}88`,
        background: `${accentColor}0A`,
      }}
    >
      <span style={{ color: '#334155', flexShrink: 0, width: '70px', fontSize: '0.65rem', paddingTop: '2px' }}>
        {ts}
      </span>
      <span style={{ color: accentColor, flexShrink: 0, width: '28px', fontSize: '0.65rem', paddingTop: '2px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontWeight: 700 }}>
        {elapsedLabel}
      </span>
      {icon && (
        <span style={{ flexShrink: 0, width: '14px', textAlign: 'center', fontSize: '0.7rem', marginLeft: '8px' }}>
          {icon}
        </span>
      )}
      <span style={{ wordBreak: 'break-word', marginLeft: icon ? '8px' : '30px' }}>
        {log.message.replace(/…$/, '')}
        <span className="forge-ide-dots" />
      </span>
    </div>
  );
});

/* ---------- Log Pane (reusable per-worker panel) ---------- */

const LogPane = memo(function LogPane({
  logs: panelLogs,
  status,
  label,
  labelColor,
  emptyText,
}: {
  logs: LogEntry[];
  status: string;
  label: string;
  labelColor: string;
  emptyText: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const scrolledAway = useRef(false);
  const prevLogCount = useRef(panelLogs.length);
  const [expandedScratchpads, setExpandedScratchpads] = useState<Set<number>>(new Set());
  const [expandedThinking, setExpandedThinking] = useState<Set<number>>(new Set());

  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    // User is "scrolled away" only if they've scrolled up more than
    // ~60px from the absolute bottom — tight threshold so auto-scroll
    // kicks back in as soon as they nudge back down.
    const remaining = el.scrollHeight - el.scrollTop - el.clientHeight;
    scrolledAway.current = remaining > 60;
  }, []);

  useEffect(() => {
    // New logs arrived — auto-scroll to the very bottom unless the user
    // has deliberately scrolled up to read earlier output.
    if (panelLogs.length !== prevLogCount.current) {
      prevLogCount.current = panelLogs.length;
      if (!scrolledAway.current && containerRef.current) {
        // Use rAF so the DOM has painted the new rows first
        requestAnimationFrame(() => {
          const el = containerRef.current;
          if (el) el.scrollTop = el.scrollHeight;
        });
      }
    }
  }, [panelLogs.length]);

  // Auto-expand the latest expandable (thinking/scratchpad) entry,
  // collapsing any previously auto-expanded one.
  useEffect(() => {
    let latestThinking = -1;
    let latestScratchpad = -1;
    for (let j = panelLogs.length - 1; j >= 0; j--) {
      if (latestThinking < 0 && panelLogs[j].thinking) latestThinking = j;
      if (latestScratchpad < 0 && panelLogs[j].scratchpad) latestScratchpad = j;
      if (latestThinking >= 0 && latestScratchpad >= 0) break;
    }
    if (latestThinking >= 0) setExpandedThinking(new Set([latestThinking]));
    if (latestScratchpad >= 0) setExpandedScratchpads(new Set([latestScratchpad]));
  }, [panelLogs.length]);

  // Find last active thinking line per worker in THIS panel.
  // A worker is only "active" if its most recent log line is the
  // thinking line — any subsequent non-thinking message (e.g.
  // "All tasks planned") means thinking is over for that worker.
  const activeIndices = new Set<number>();
  if (status === 'running') {
    const seenWorkers = new Set<string>();
    for (let j = panelLogs.length - 1; j >= 0; j--) {
      const wm = panelLogs[j].message.match(/\[(\w+)\]/);
      const worker = wm ? wm[1] : '_default';
      if (seenWorkers.has(worker)) continue;
      if (panelLogs[j].level === 'thinking' && panelLogs[j].message.endsWith('…')) {
        activeIndices.add(j);
      }
      // Mark worker as resolved — if the first line we hit (scanning
      // backward) was NOT a thinking line, the timer should not tick.
      seenWorkers.add(worker);
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', flex: 1, minHeight: 0 }}>
      {/* Panel header */}
      <div style={{
        padding: '4px 12px', borderBottom: '1px solid #1E293B',
        fontSize: '0.6rem', fontWeight: 700, color: labelColor,
        letterSpacing: '0.5px', background: labelColor + '08',
        display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0,
      }}>
        <span style={{
          width: '6px', height: '6px', borderRadius: '50%',
          background: labelColor, display: 'inline-block',
        }} />
        {label}
        <span style={{ color: '#475569', fontSize: '0.55rem', fontWeight: 400 }}>
          ({panelLogs.length})
        </span>
      </div>
      {/* Scroll container */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        style={{
          flex: 1, overflowY: 'auto', padding: '8px 12px',
          fontFamily: '"Cascadia Code", "Fira Code", "JetBrains Mono", monospace',
          fontSize: '0.75rem', lineHeight: 1.7,
        }}
      >
        {panelLogs.length === 0 ? (
          <div style={{ color: '#475569', padding: '12px 0', fontSize: '0.7rem' }}>
            {emptyText}
          </div>
        ) : panelLogs.map((log, i) => {
          const isBox = /^[\u2554\u2557\u255A\u255D\u2551\u2550]/.test(log.message) || /[\u2554\u2557\u255A\u255D\u2551\u2550]{2,}/.test(log.message);
          const color = isBox ? '#FBBF24' : (LEVEL_COLORS[log.level] ?? LEVEL_COLORS.info);
          const icon = LEVEL_ICONS[log.level] ?? '';
          const isActive = activeIndices.has(i);

          if (isActive) {
            return <ActiveLogLine key={i} log={log} color={color} icon={icon} accentColor={labelColor} />;
          }

          const ts = log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : '';
          const isThinkingLevel = log.level === 'thinking';
          const isScratchpad = !!log.scratchpad;
          const isLLMThinking = !!log.thinking;
          const isTier = !!log.tier;
          const isExpandable = isScratchpad || isLLMThinking;
          const isExpanded = isScratchpad
            ? expandedScratchpads.has(i)
            : isLLMThinking ? expandedThinking.has(i) : false;

          const thinkingColor = isLLMThinking
            ? (log.worker === 'opus' ? '#D946EF' : '#38BDF8')
            : undefined;

          return (
            <div key={i}>
              <div
                style={{
                  color: isLLMThinking ? thinkingColor : color,
                  display: 'flex', gap: '8px',
                  padding: isThinkingLevel ? '1px 0 1px 12px'
                    : isLLMThinking ? '3px 0 3px 12px' : '1px 0',
                  borderLeft: isLLMThinking ? `2px solid ${thinkingColor}66`
                    : isThinkingLevel ? '2px solid #7C3AED33'
                    : isScratchpad ? '2px solid #F59E0B44'
                    : isTier ? '2px solid #3B82F644'
                    : 'none',
                  opacity: log.level === 'debug' ? 0.5 : 1,
                  cursor: isExpandable ? 'pointer' : undefined,
                  background: isLLMThinking ? `${thinkingColor}0A`
                    : isScratchpad ? '#F59E0B08'
                    : isTier ? '#3B82F608' : undefined,
                  borderRadius: isExpandable || isTier ? '2px' : undefined,
                }}
                onClick={isExpandable ? () => {
                  if (isScratchpad) {
                    setExpandedScratchpads((prev) => {
                      const next = new Set(prev);
                      if (next.has(i)) next.delete(i); else next.add(i);
                      return next;
                    });
                  } else if (isLLMThinking) {
                    setExpandedThinking((prev) => {
                      const next = new Set(prev);
                      if (next.has(i)) next.delete(i); else next.add(i);
                      return next;
                    });
                  }
                } : undefined}
                title={isExpandable ? 'Click to expand' : undefined}
              >
                <span style={{ color: '#334155', flexShrink: 0, width: '70px', fontSize: '0.65rem', paddingTop: '2px' }}>
                  {ts}
                </span>
                {icon && !isLLMThinking && (
                  <span style={{ flexShrink: 0, width: '14px', textAlign: 'center', fontSize: '0.7rem' }}>
                    {icon}
                  </span>
                )}
                <span style={{ wordBreak: 'break-word' }}>
                  {log.message}
                  {isScratchpad && (
                    <span style={{ color: '#F59E0B', fontSize: '0.6rem', marginLeft: '6px' }}>
                      {isExpanded ? '▼' : '▶'} {log.scratchpad!.fullLength} chars
                    </span>
                  )}
                  {isLLMThinking && (
                    <span style={{ color: thinkingColor, fontSize: '0.6rem', marginLeft: '6px', opacity: 0.7 }}>
                      {isExpanded ? '▼' : '▶'} {(log.thinking!.userMessageLength / 1000).toFixed(1)}k chars
                    </span>
                  )}
                </span>
              </div>
              {/* Expanded scratchpad content */}
              {isExpanded && log.scratchpad && (
                <pre style={{
                  margin: '2px 0 4px 86px',
                  padding: '6px 10px',
                  background: '#0F172A',
                  border: '1px solid #F59E0B33',
                  borderRadius: '4px',
                  color: '#CBD5E1',
                  fontSize: '0.7rem',
                  lineHeight: 1.5,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  maxHeight: '300px',
                  overflowY: 'auto',
                }}>
                  <code>{log.scratchpad.content}</code>
                  {log.scratchpad.fullLength > log.scratchpad.content.length && (
                    <div style={{ color: '#64748B', fontStyle: 'italic', marginTop: '4px' }}>
                      ... truncated ({log.scratchpad.fullLength - log.scratchpad.content.length} more chars)
                    </div>
                  )}
                </pre>
              )}
              {/* Expanded LLM thinking content */}
              {isExpanded && log.thinking && (
                <div style={{
                  margin: '2px 0 6px 86px',
                  background: '#0F172A',
                  border: `1px solid ${thinkingColor}33`,
                  borderRadius: '4px',
                  fontSize: '0.7rem',
                  lineHeight: 1.5,
                  overflow: 'hidden',
                }}>
                  {/* System prompt section */}
                  <div style={{ padding: '6px 10px', borderBottom: `1px solid ${thinkingColor}22` }}>
                    <div style={{ color: thinkingColor, fontSize: '0.6rem', fontWeight: 700, marginBottom: '3px', letterSpacing: '0.5px' }}>
                      SYSTEM PROMPT
                    </div>
                    <pre style={{
                      margin: 0, color: '#94A3B8', whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word', maxHeight: '120px', overflowY: 'auto',
                    }}>
                      <code>{log.thinking.systemPrompt}</code>
                    </pre>
                  </div>
                  {/* User message preview section */}
                  <div style={{ padding: '6px 10px', borderBottom: `1px solid ${thinkingColor}22` }}>
                    <div style={{ color: thinkingColor, fontSize: '0.6rem', fontWeight: 700, marginBottom: '3px', letterSpacing: '0.5px' }}>
                      USER MESSAGE ({(log.thinking.userMessageLength / 1000).toFixed(1)}k chars)
                    </div>
                    <pre style={{
                      margin: 0, color: '#CBD5E1', whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word', maxHeight: '200px', overflowY: 'auto',
                    }}>
                      <code>{log.thinking.userMessagePreview}</code>
                      {log.thinking.userMessageLength > log.thinking.userMessagePreview.length && (
                        <span style={{ color: '#64748B', fontStyle: 'italic' }}>
                          {'\n'}... truncated ({log.thinking.userMessageLength - log.thinking.userMessagePreview.length} more chars)
                        </span>
                      )}
                    </pre>
                  </div>
                  {/* Metadata bar */}
                  <div style={{
                    padding: '4px 10px', display: 'flex', gap: '12px', flexWrap: 'wrap',
                    color: '#64748B', fontSize: '0.6rem',
                  }}>
                    {log.thinking.contractsIncluded && log.thinking.contractsIncluded.length > 0 && (
                      <span>Contracts: {log.thinking.contractsIncluded.join(', ')}</span>
                    )}
                    {log.thinking.contextFiles && log.thinking.contextFiles.length > 0 && (
                      <span>Context: {log.thinking.contextFiles.length} files</span>
                    )}
                    {log.thinking.files && log.thinking.files.length > 0 && (
                      <span>Files: {log.thinking.files.join(', ')}</span>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
});

/* ---------- Component ---------- */

export default function ForgeIDEModal({ runId, projectId, repoName, onClose, mode = 'upgrade' }: ForgeIDEModalProps) {
  const { token } = useAuth();
  const isBuild = mode === 'build';
  const entityId = isBuild ? projectId! : runId!;

  /* State */
  const [tasks, setTasks] = useState<UpgradeTask[]>([]);
  /* Build mode state */
  const [buildId, setBuildId] = useState('');
  const [costData, setCostData] = useState<CostData | null>(null);
  const [phases, setPhases] = useState<BuildPhase[]>([]);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [fileDiffs, setFileDiffs] = useState<FileDiff[]>([]);
  const [status, setStatus] = useState<'preparing' | 'ready' | 'running' | 'paused' | 'stopping' | 'stopped' | 'completed' | 'error'>('preparing');
  const [totalTasks, setTotalTasks] = useState(0);
  const [completedTasks, setCompletedTasks] = useState(0);
  const [expandedDiff, setExpandedDiff] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<'activity' | 'changes' | 'errors'>('activity');
  const [buildErrors, setBuildErrors] = useState<BuildError[]>([]);
  const [tokenUsage, setTokenUsage] = useState<TokenUsage>({ ...EMPTY_TOKENS });
  const [chatMessages, setChatMessages] = useState<{ role: 'user' | 'assistant'; text: string; timestamp: string }[]>([]);
  const [leftTab, setLeftTab] = useState<'tasks' | 'chat'>('tasks');
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [chatLoading, setChatLoading] = useState(false);
  const [cmdInput, setCmdInput] = useState('');
  const [cmdSuggestions, setCmdSuggestions] = useState<string[]>([]);
  const [cmdHistoryArr, setCmdHistoryArr] = useState<string[]>([]);
  const [historyIdx, setHistoryIdx] = useState(-1);
  const [pendingPrompt, setPendingPrompt] = useState(false);  // Y/N prompt active
  const [pendingClarification, setPendingClarification] = useState<{
    questionId: string;
    question: string;
    context?: string;
    options?: string[];
  } | null>(null);
  const [fixProgress, setFixProgress] = useState<{ tier: number; attempt: number; max: number } | null>(null);
  const [fileChecklist, setFileChecklist] = useState<ChecklistItem[]>([]);
  const [opusAgents, setOpusAgents] = useState<AgentInfo[]>([]);
  const [opusPct, setOpusPct] = useState(50);  // Opus takes top N%, Sonnet gets rest
  /* Per-phase file tracking for expandable sidebar */
  const [phaseFiles, setPhaseFiles] = useState<Record<number, { path: string; size_bytes?: number; language?: string; committed: boolean }[]>>({});
  const [expandedPhase, setExpandedPhase] = useState<number | null>(null);
  const [setupEndIndex, setSetupEndIndex] = useState(-1);
  const [setupCollapsed, setSetupCollapsed] = useState(true);
  const autoCommenceRef = useRef(false);
  const cmdInputRef = useRef<HTMLInputElement>(null);
  const rightColRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);

  const SLASH_COMMANDS: Record<string, string> = isBuild ? {
    '/start':   'Start or resume the build',
    '/pause':   'Pause after the current file finishes',
    '/resume':  'Resume a paused build',
    '/stop':    'Cancel the build immediately',
    '/nuke':    'Nuke build — delete branch + record permanently',
    '/push':    'Commit and push to GitHub',
    '/compact': 'Compact context before next file',
    '/commit':  'Git add, commit, and push',
    '/status':  'Print current progress summary',
    '/clear':   'Clear the activity log',
    '/verify':  'Run verification (syntax + tests)',
  } : {
    '/start':  'Begin the upgrade execution',
    '/pause':  'Pause execution after current task finishes',
    '/resume': 'Resume a paused execution',
    '/stop':   'Abort execution (current task finishes first)',
    '/retry':  'Re-run failed/skipped tasks',
    '/push':   'Apply changes, commit, and push to GitHub',
    '/status': 'Print current progress summary',
    '/help':   'Show available slash commands',
    '/clear':  'Clear the activity log',
  };

  /* Drag-to-resize Opus/Sonnet split */
  const handleDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    draggingRef.current = true;
    const onMove = (ev: MouseEvent) => {
      if (!draggingRef.current || !rightColRef.current) return;
      const rect = rightColRef.current.getBoundingClientRect();
      const y = ev.clientY - rect.top;
      const pct = Math.min(85, Math.max(15, (y / rect.height) * 100));
      setOpusPct(pct);
    };
    const onUp = () => {
      draggingRef.current = false;
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';
  }, []);

  /* Refs */
  const startedRef = useRef(false);
  const chatScrollRef = useRef<HTMLDivElement>(null);
  const logsLenRef = useRef(0);

  /* Keep logsLenRef in sync so polling closures see the latest count */
  useEffect(() => { logsLenRef.current = logs.length; }, [logs.length]);

  /* Derived: split logs by worker for three-panel display */
  const _rawSystemLogs = useMemo(
    () => logs.filter(l => l.worker !== 'sonnet' && l.worker !== 'opus'),
    [logs],
  );

  /* Collapse setup logs (everything before forge_ide_ready) into a
     single toggleable summary line when setupCollapsed is true. */
  const systemLogs = useMemo(() => {
    if (setupEndIndex < 0 || !setupCollapsed) return _rawSystemLogs;
    // Count how many raw-system entries fall within the setup range
    const setupSysLogs = logs.slice(0, setupEndIndex).filter(
      l => l.worker !== 'sonnet' && l.worker !== 'opus',
    );
    if (setupSysLogs.length === 0) return _rawSystemLogs;
    return _rawSystemLogs.slice(setupSysLogs.length);
  }, [_rawSystemLogs, logs, setupEndIndex, setupCollapsed]);

  const sonnetLogs = useMemo(
    () => logs.filter(l => l.worker === 'sonnet'),
    [logs],
  );
  const opusLogs = useMemo(
    () => logs.filter(l => l.worker === 'opus'),
    [logs],
  );

  /* Prepare workspace & show preview (soft-landing — no auto-start) */
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    if (isBuild) {
      /* ── Build mode init ── */
      const prepareBuild = async () => {
        const hdr = { Authorization: `Bearer ${token}` };
        // 1. Fetch phases → map to tasks
        try {
          const phasesRes = await fetch(`${API_BASE}/projects/${projectId}/build/phases`, { headers: hdr });
          if (phasesRes.ok) {
            const phasesData: BuildPhase[] = await phasesRes.json();
            setPhases(phasesData);
            setTotalTasks(phasesData.length);
            setTasks(phasesData.map((ph) => ({
              id: `phase_${ph.number}`,
              name: `Phase ${ph.number}: ${ph.name}`,
              priority: 'high',
              effort: 'large',
              forge_automatable: true,
              category: ph.objective?.substring(0, 50) || 'Build phase',
              status: 'pending' as const,
            })));
          }
        } catch { /* silent */ }

        // 2. Fetch current build status (may resume existing build)
        try {
          const statusRes = await fetch(`${API_BASE}/projects/${projectId}/build/status`, { headers: hdr });
          if (statusRes.ok) {
            const sd = await statusRes.json();
            setBuildId(sd.id || '');
            const currentPhaseNum = (() => { const m = (sd.phase || '').match(/\d+/); return m ? parseInt(m[0], 10) : 0; })();
            // Map build status to IDE status
            if (sd.status === 'running') {
              setStatus('running');
              // Mark phases that are already done
              setTasks((prev) => prev.map((t) => {
                const phNum = parseInt(t.id.replace('phase_', ''), 10);
                if (phNum < currentPhaseNum) return { ...t, status: 'proposed' as const };
                if (phNum === currentPhaseNum) return { ...t, status: 'running' as const };
                return t;
              }));
              setCompletedTasks(currentPhaseNum);
            } else if (sd.status === 'pending') {
              // Build exists but hasn't been commenced — still in setup/ready gate
              setStatus('preparing');
            } else if (sd.status === 'paused') setStatus('paused');
            else if (sd.status === 'completed') { setStatus('completed'); setCompletedTasks(phases.length); }
            else if (sd.status === 'failed') setStatus('error');
            else setStatus('ready');
          } else {
            setStatus('ready');
          }
        } catch {
          setStatus('ready');
        }

        // 3. Fetch per-phase file lists for resumed builds
        try {
          const pfRes = await fetch(`${API_BASE}/projects/${projectId}/build/phase-files`, { headers: hdr });
          if (pfRes.ok) {
            const pfData = await pfRes.json();
            if (pfData.phases) {
              const mapped: Record<number, { path: string; size_bytes?: number; language?: string; committed: boolean }[]> = {};
              for (const [k, v] of Object.entries(pfData.phases)) {
                mapped[parseInt(k, 10)] = v as any;
              }
              setPhaseFiles(mapped);
            }
          }
        } catch { /* silent */ }

        // 4. Fetch live cost
        try {
          const costRes = await fetch(`${API_BASE}/projects/${projectId}/build/live-cost`, { headers: hdr });
          if (costRes.ok) {
            const cd = await costRes.json();
            setCostData(cd);
            // Map per-model tokens if available, otherwise fall back to aggregate
            const mt = cd.model_tokens as Record<string, { input: number; output: number; total: number }> | undefined;
            if (mt) {
              const opus = mt.opus || { input: 0, output: 0, total: 0 };
              const sonnet = mt.sonnet || { input: 0, output: 0, total: 0 };
              const haiku = mt.haiku || { input: 0, output: 0, total: 0 };
              setTokenUsage({
                opus: { input: opus.input, output: opus.output, total: opus.total },
                sonnet: { input: sonnet.input, output: sonnet.output, total: sonnet.total },
                haiku: { input: haiku.input, output: haiku.output, total: haiku.total },
                total: opus.total + sonnet.total + haiku.total,
              });
            } else if (cd.tokens_in || cd.tokens_out) {
              setTokenUsage((prev) => ({
                ...prev,
                opus: { input: cd.tokens_in || 0, output: cd.tokens_out || 0, total: (cd.tokens_in || 0) + (cd.tokens_out || 0) },
                total: (cd.tokens_in || 0) + (cd.tokens_out || 0),
              }));
            }
          }
        } catch { /* silent */ }

        // 4. Seed logs from history
        try {
          const logsRes = await fetch(`${API_BASE}/projects/${projectId}/build/logs?limit=200`, { headers: hdr });
          if (logsRes.ok) {
            const logData = await logsRes.json();
            const items = (logData.items ?? []) as { timestamp: string; message: string; level: string; source?: string }[];
            setLogs(items.map((l) => ({
              timestamp: l.timestamp,
              source: l.source || 'system',
              level: l.level || 'info',
              message: l.message,
              worker: classifyWorker(l.message || ''),
            })));
          }
        } catch { /* silent */ }

        // 5. Load persisted build errors
        try {
          const errRes = await fetch(`${API_BASE}/projects/${projectId}/build/errors`, { headers: hdr });
          if (errRes.ok) {
            const errData: BuildError[] = await errRes.json();
            if (errData.length > 0) setBuildErrors(errData);
          }
        } catch { /* silent */ }

        setCmdInput('/start');
      };
      prepareBuild();
    } else {
      /* ── Upgrade mode init (existing) ── */
      const prepare = async () => {
        // 1. Fetch upgrade preview (task list, repo info)
        try {
          const previewRes = await fetch(`${API_BASE}/scout/runs/${runId}/upgrade-preview`, {
            headers: { Authorization: `Bearer ${token}` },
          });
          if (previewRes.ok) {
            const preview = await previewRes.json();
            setTotalTasks(preview.total_tasks || 0);
            if (Array.isArray(preview.tasks)) {
              setTasks(preview.tasks.map((t: any) => ({ ...t, status: 'pending' })));
            }
          }
        } catch { /* silent */ }

        // 2. Clone repository in background (WS events show progress)
        try {
          const prepRes = await fetch(`${API_BASE}/scout/runs/${runId}/prepare-upgrade`, {
            method: 'POST',
            headers: { Authorization: `Bearer ${token}` },
          });
          if (prepRes.ok) {
            const prepData = await prepRes.json();
            setStatus('ready');
            // If backend recovered a stash from a previous failed push,
            // pre-fill /push instead of /start so the user can retry.
            if (prepData.stash_recovered) {
              setCmdInput('/push');
              return;
            }
          } else {
            // Clone failed but still allow IDE usage (analysis works)
            setStatus('ready');
          }
        } catch {
          setStatus('ready');
        }
        setCmdInput('/start');
      };
      prepare();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entityId, token]);

  /* WS event handler */
  useWebSocket(
    useCallback(
      (data: { type: string; payload: any }) => {
        const p = data.payload;
        if (!p) return;

        /* ────── Build mode WS events ────── */
        if (isBuild) {
          switch (data.type) {
            case 'build_started':
              setBuildId(p.id || '');
              setStatus('preparing');
              break;

            case 'build_overview': {
              const phList: BuildPhase[] = (p.phases || []).map((ph: any) => ({
                number: ph.number, name: ph.name, objective: ph.objective || '',
                deliverables: ph.deliverables || [],
              }));
              setPhases(phList);
              setTotalTasks(phList.length);
              setTasks(phList.map((ph) => ({
                id: `phase_${ph.number}`,
                name: `Phase ${ph.number}: ${ph.name}`,
                priority: 'high', effort: 'large', forge_automatable: true,
                category: ph.objective?.substring(0, 50) || '',
                status: 'pending' as const,
              })));
              break;
            }

            case 'build_log': {
              const msg = (p.message ?? p.msg ?? '') as string;
              if (!msg) break;
              const logLevel = (p.level || 'info') as string;
              setLogs((prev) => [...prev, {
                timestamp: p.timestamp || new Date().toISOString(),
                source: p.source || 'system',
                level: logLevel,
                message: msg,
                worker: classifyWorker(msg),
              }]);
              // Track errors in the errors panel
              if (logLevel === 'error') {
                const fp = errorFingerprint(p.source || 'system', 'error', msg);
                setBuildErrors((prev) => {
                  const existing = prev.find((e) => e.fingerprint === fp && !e.resolved);
                  if (existing) {
                    return prev.map((e) =>
                      e.id === existing.id
                        ? { ...e, occurrence_count: e.occurrence_count + 1, last_seen: new Date().toISOString() }
                        : e,
                    );
                  }
                  const newErr: BuildError = {
                    id: `fe-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                    fingerprint: fp,
                    first_seen: p.timestamp || new Date().toISOString(),
                    last_seen: p.timestamp || new Date().toISOString(),
                    occurrence_count: 1,
                    phase: undefined,
                    file_path: undefined,
                    source: p.source || 'system',
                    severity: 'error',
                    message: msg,
                    resolved: false,
                  };
                  return [...prev, newErr];
                });
              }
              break;
            }

            case 'build_turn':
              // Informational — token/context metric
              break;

            case 'token_update':
              setTokenUsage((prev) => ({
                ...prev,
                opus: {
                  input: p.input_tokens || 0,
                  output: p.output_tokens || 0,
                  total: (p.input_tokens || 0) + (p.output_tokens || 0),
                },
                total: p.total_tokens || (p.input_tokens || 0) + (p.output_tokens || 0),
              }));
              break;

            case 'cost_ticker':
              setCostData({
                total_cost_usd: p.total_cost_usd || 0,
                api_calls: p.api_calls || 0,
                tokens_in: p.tokens_in || 0,
                tokens_out: p.tokens_out || 0,
                spend_cap: p.spend_cap ?? null,
                pct_used: p.pct_used || 0,
              });
              // Update per-model token buckets from model_tokens
              if (p.model_tokens) {
                const mt = p.model_tokens as Record<string, { input: number; output: number; total: number }>;
                setTokenUsage((prev) => {
                  const opus = mt.opus || { input: 0, output: 0, total: 0 };
                  const sonnet = mt.sonnet || { input: 0, output: 0, total: 0 };
                  const haiku = mt.haiku || { input: 0, output: 0, total: 0 };
                  return {
                    opus: { input: opus.input, output: opus.output, total: opus.total },
                    sonnet: { input: sonnet.input, output: sonnet.output, total: sonnet.total },
                    haiku: { input: haiku.input, output: haiku.output, total: haiku.total },
                    total: opus.total + sonnet.total + haiku.total,
                  };
                });
              }
              break;

            case 'phase_plan':
              // Could map plan tasks under current phase — for now log it
              break;

            case 'phase_complete': {
              const phaseStr = (p.phase || '') as string;
              const phNum = parseInt(phaseStr.match(/\d+/)?.[0] || '0', 10);
              setTasks((prev) => prev.map((t) =>
                t.id === `phase_${phNum}` ? { ...t, status: 'proposed' as const } : t
              ));
              setCompletedTasks((prev) => Math.max(prev, phNum + 1));
              // Store per-phase file list for expandable sidebar
              if (Array.isArray(p.files) && p.files.length > 0) {
                setPhaseFiles((prev) => ({
                  ...prev,
                  [phNum]: (p.files as { path: string; size_bytes?: number; language?: string; committed: boolean }[]),
                }));
              }
              if (p.input_tokens && p.output_tokens) {
                setTokenUsage((prev) => ({
                  ...prev,
                  opus: {
                    input: (prev.opus.input || 0) + (p.input_tokens || 0),
                    output: (prev.opus.output || 0) + (p.output_tokens || 0),
                    total: (prev.opus.total || 0) + (p.input_tokens || 0) + (p.output_tokens || 0),
                  },
                  total: (prev.total || 0) + (p.input_tokens || 0) + (p.output_tokens || 0),
                }));
              }
              break;
            }

            case 'phase_transition': {
              const msg = (p.message ?? '') as string;
              if (msg) {
                setLogs((prev) => [...prev, {
                  timestamp: new Date().toISOString(),
                  source: 'system', level: 'system', message: msg, worker: 'system',
                }]);
              }
              // Mark next phase as running
              const nextPhStr = msg.match(/Phase\s+(\d+)/g);
              if (nextPhStr && nextPhStr.length >= 2) {
                const nextNum = parseInt(nextPhStr[1].match(/\d+/)?.[0] || '0', 10);
                setTasks((prev) => prev.map((t) =>
                  t.id === `phase_${nextNum}` ? { ...t, status: 'running' as const } : t
                ));
              }
              break;
            }

            case 'file_manifest':
              setFileChecklist(
                (p.files || []).map((f: any) => ({
                  file: f.path || f.file,
                  action: 'modify',
                  description: f.purpose || '',
                  status: f.status === 'done' ? 'passed' as const : 'pending' as const,
                })),
              );
              break;

            /* file_generating handled below with agent tracking */

            case 'file_generated':
              setFileChecklist((prev) => prev.map((c) =>
                c.file === p.path ? { ...c, status: p.skipped ? 'pending' as const : 'written' as const } : c
              ));
              break;

            case 'file_audited':
              setFileChecklist((prev) => prev.map((c) =>
                c.file === p.path ? { ...c, status: p.verdict === 'PASS' ? 'passed' as const : 'failed' as const } : c
              ));
              break;

            case 'file_created':
              setFileDiffs((prev) => {
                // If the file already exists (e.g. fix/modify), update it
                const existing = prev.find((d) => d.file === p.path);
                if (existing) {
                  return prev.map((d) =>
                    d.file === p.path
                      ? {
                          ...d,
                          action: p.action || 'modify',
                          description: `${p.size_bytes || 0} bytes · ${p.language || ''}`,
                          before_snippet: p.before_snippet || d.after_snippet,
                          after_snippet: p.after_snippet || d.after_snippet,
                        }
                      : d,
                  );
                }
                return [...prev, {
                  task_id: 'build',
                  file: p.path,
                  action: p.action || 'create',
                  description: `${p.size_bytes || 0} bytes · ${p.language || ''}`,
                  after_snippet: p.after_snippet,
                }];
              });
              break;

            case 'tool_use':
              setLogs((prev) => [...prev, {
                timestamp: new Date().toISOString(),
                source: 'tool', level: 'info',
                message: `🔧 ${p.tool_name}: ${p.result_summary || p.input_summary || ''}`,
                worker: 'opus',
              }]);
              break;

            case 'build_complete':
              setStatus('completed');
              if (p.total_input_tokens || p.total_output_tokens) {
                setTokenUsage((prev) => ({
                  ...prev,
                  opus: {
                    input: p.total_input_tokens || prev.opus.input,
                    output: p.total_output_tokens || prev.opus.output,
                    total: (p.total_input_tokens || 0) + (p.total_output_tokens || 0) || prev.opus.total,
                  },
                  total: (p.total_input_tokens || 0) + (p.total_output_tokens || 0) || prev.total,
                }));
              }
              if (p.total_cost_usd != null) {
                setCostData((prev) => ({ ...(prev || { api_calls: 0, tokens_in: 0, tokens_out: 0, spend_cap: null, pct_used: 0 }),
                  total_cost_usd: p.total_cost_usd }));
              }
              break;

            case 'build_error':
            case 'build_failed':
              setStatus('error');
              if (p.error_detail || p.error) {
                const errMsg = p.error_detail || p.error || 'Build failed';
                setLogs((prev) => [...prev, {
                  timestamp: new Date().toISOString(),
                  source: 'system', level: 'error',
                  message: errMsg,
                  worker: 'system',
                }]);
                // Track as fatal error
                const fp = errorFingerprint('system', 'fatal', errMsg);
                setBuildErrors((prev) => {
                  const existing = prev.find((e) => e.fingerprint === fp && !e.resolved);
                  if (existing) {
                    return prev.map((e) =>
                      e.id === existing.id
                        ? { ...e, occurrence_count: e.occurrence_count + 1, last_seen: new Date().toISOString() }
                        : e,
                    );
                  }
                  return [...prev, {
                    id: `fe-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                    fingerprint: fp,
                    first_seen: new Date().toISOString(),
                    last_seen: new Date().toISOString(),
                    occurrence_count: 1,
                    source: 'system',
                    severity: 'fatal' as const,
                    message: errMsg,
                    resolved: false,
                  }];
                });
              }
              break;

            case 'build_error_resolved': {
              // Backend resolved an error (phase-complete or auto-fix)
              const resolvedId = p.error_id as string;
              const resolvedFp = p.fingerprint as string | undefined;
              setBuildErrors((prev) => prev.map((e) => {
                if (e.id === resolvedId || (resolvedFp && e.fingerprint === resolvedFp && !e.resolved)) {
                  return {
                    ...e,
                    resolved: true,
                    resolved_at: new Date().toISOString(),
                    resolution_method: (p.method || 'auto-fix') as BuildError['resolution_method'],
                    resolution_summary: (p.summary || '') as string,
                  };
                }
                return e;
              }));
              break;
            }

            case 'build_cancelled':
              setStatus('stopped');
              break;

            case 'build_nuked':
              setStatus('stopped');
              setLogs((prev) => [...prev, {
                timestamp: new Date().toISOString(),
                source: 'system', level: 'warn',
                message: `☢ Build nuked — ${p.message || 'branch deleted, build record finalized'}`,
                worker: 'system',
              }]);
              break;

            case 'forge_ide_ready':
              setSetupEndIndex(logs.length);
              if (autoCommenceRef.current) {
                // Auto-commence: user already pressed /start, skip ready state
                autoCommenceRef.current = false;
                setLogs((prev) => [...prev, {
                  timestamp: new Date().toISOString(),
                  source: 'system', level: 'info',
                  message: '✔ IDE ready — commencing build…',
                  worker: 'system',
                }]);
                // Fire commence in background
                fetch(`${API_BASE}/projects/${projectId}/build/commence`, {
                  method: 'POST',
                  headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
                  body: JSON.stringify({ action: 'commence' }),
                }).catch(() => { /* WS build_commenced will confirm */ });
              } else {
                setStatus('ready');
                setLogs((prev) => [...prev, {
                  timestamp: new Date().toISOString(),
                  source: 'system', level: 'info',
                  message: '✔ IDE ready — type /start to begin the build',
                  worker: 'system',
                }]);
              }
              break;

            case 'build_commenced':
              setStatus('running');
              break;

            case 'build_paused':
              setStatus('paused');
              setPendingPrompt(true);
              setLogs((prev) => [...prev, {
                timestamp: new Date().toISOString(),
                source: 'system', level: 'warn',
                message: `⏸ Build paused — ${p.audit_findings || 'awaiting user action'}. Options: ${(p.options || []).join(', ')}`,
                worker: 'system',
              }]);
              setTimeout(() => cmdInputRef.current?.focus(), 100);
              break;

            case 'build_resumed':
              setStatus('running');
              setPendingPrompt(false);
              break;

            case 'build_clarification_request': {
              const { question_id, question, context, options } = p;
              setPendingClarification({ questionId: question_id, question, context, options });
              setStatus('awaiting_input' as any);
              setTimeout(() => cmdInputRef.current?.focus(), 100);
              break;
            }

            case 'build_clarification_resolved': {
              setPendingClarification(null);
              setStatus('running');
              if (p.answer) {
                setLogs((prev) => [...prev, {
                  timestamp: new Date().toISOString(),
                  source: 'user',
                  level: 'info',
                  message: `\u21B3 You answered: ${p.answer}`,
                  worker: 'system' as const,
                }]);
              }
              break;
            }

            case 'audit_pass':
            case 'audit_fail': {
              const verdict = data.type === 'audit_pass' ? '✅ PASS' : '❌ FAIL';
              setLogs((prev) => [...prev, {
                timestamp: new Date().toISOString(),
                source: 'audit', level: data.type === 'audit_pass' ? 'info' : 'warn',
                message: `Audit ${verdict} — ${p.phase || ''}`,
                worker: 'system',
              }]);
              break;
            }

            case 'build_activity_status':
              setLogs((prev) => [...prev, {
                timestamp: new Date().toISOString(),
                source: 'system', level: 'thinking',
                message: `${p.status}…`,
                worker: (p.model === 'sonnet' ? 'sonnet' : p.model === 'opus' ? 'opus' : 'system') as 'sonnet' | 'opus' | 'system',
              }]);
              break;

            case 'context_reset':
              setLogs((prev) => [...prev, {
                timestamp: new Date().toISOString(),
                source: 'system', level: 'system',
                message: `♻ Context reset for ${p.phase || 'next phase'} (dropped ${p.dropped || 0} messages)`,
                worker: 'system',
              }]);
              // Clear agent tracker for new phase
              setOpusAgents([]);
              break;

            case 'verification_result':
              setLogs((prev) => [...prev, {
                timestamp: new Date().toISOString(),
                source: 'verify', level: (p.tests_failed || 0) > 0 ? 'warn' : 'info',
                message: `Verification: ${p.tests_passed || 0} passed, ${p.tests_failed || 0} failed, ${p.syntax_errors || 0} syntax errors`,
                worker: 'system',
              }]);
              break;

            case 'governance_pass':
            case 'governance_fail':
              setLogs((prev) => [...prev, {
                timestamp: new Date().toISOString(),
                source: 'governance', level: data.type === 'governance_pass' ? 'info' : 'warn',
                message: `Governance ${data.type === 'governance_pass' ? '✅ PASS' : '❌ FAIL'}`,
                worker: 'system',
              }]);
              break;

            /* ---- Scratchpad writes (from any agent) ---- */
            case 'scratchpad_write': {
              const spWorker: 'sonnet' | 'opus' = p.source === 'sonnet' ? 'sonnet' : 'opus';
              const preview = (p.summary as string) || (p.content_preview as string) || '';
              setLogs((prev) => [...prev, {
                timestamp: new Date().toISOString(),
                source: p.role || 'agent',
                level: 'info',
                message: `📝 Scratchpad [${p.key}]: ${preview.slice(0, 120)}${preview.length > 120 ? '…' : ''}`,
                worker: spWorker,
                scratchpad: {
                  key: p.key as string,
                  content: (p.content_preview as string) || '',
                  fullLength: (p.full_length as number) || 0,
                  role: (p.role as string) || 'agent',
                },
              }]);
              break;
            }

            /* ---- Sonnet live review results ---- */
            case 'sonnet_review': {
              const icon = p.verdict === 'ok' ? '✅' : '⚠️';
              const note = (p.note as string) || '';
              setLogs((prev) => [...prev, {
                timestamp: new Date().toISOString(),
                source: 'review', level: p.verdict === 'ok' ? 'info' : 'warn',
                message: `${icon} Reviewed ${p.path}${note ? ` — ${note}` : ''}`,
                worker: 'sonnet',
              }]);
              break;
            }

            /* ---- LLM thinking / prompt visibility ---- */
            case 'llm_thinking': {
              const model = (p.model as string) || 'sonnet';
              const purpose = (p.purpose as string) || 'Processing...';
              const sysPrompt = (p.system_prompt as string) || '';
              const msgPreview = (p.user_message_preview as string) || '';
              const msgLen = (p.user_message_length as number) || 0;
              const thinkWorker: 'sonnet' | 'opus' = model === 'opus' ? 'opus' : 'sonnet';
              const icon = thinkWorker === 'sonnet' ? '🧠' : '📋';
              setLogs((prev) => [...prev, {
                timestamp: new Date().toISOString(),
                source: 'thinking', level: 'thinking',
                message: `${icon} ${purpose}`,
                worker: thinkWorker,
                thinking: {
                  purpose,
                  systemPrompt: sysPrompt,
                  userMessagePreview: msgPreview,
                  userMessageLength: msgLen,
                  file: (p.file as string) || undefined,
                  contractsIncluded: p.contracts_included as string[] || undefined,
                  contextFiles: (p.context_files as string[]) || undefined,
                  files: (p.files as string[]) || undefined,
                },
              }]);
              break;
            }

            /* ---- Tier progress events ---- */
            case 'tiers_computed': {
              setLogs((prev) => [...prev, {
                timestamp: new Date().toISOString(),
                source: 'planner', level: 'info',
                message: `🏗️ ${p.tier_count} dependency tiers computed for ${p.phase || 'this phase'}`,
                worker: 'sonnet',
                tier: {
                  index: -1, action: 'start',
                  fileCount: (p.tiers as { files: string[] }[])?.reduce((n: number, t: { files: string[] }) => n + t.files.length, 0) ?? 0,
                },
              }]);
              // Assign tier indices to existing checklist items
              const tierMap = new Map<string, number>();
              for (const t of (p.tiers as { tier: number; files: string[] }[]) || []) {
                for (const f of t.files) {
                  tierMap.set(f, t.tier);
                }
              }
              setFileChecklist((prev) => prev.map((c) => ({
                ...c,
                tierIndex: tierMap.get(c.file) ?? c.tierIndex,
              })));
              break;
            }

            case 'tier_start': {
              const tierFiles = (p.files as string[]) || [];
              const prefix = (p.common_prefix as string) || '';
              setLogs((prev) => [...prev, {
                timestamp: new Date().toISOString(),
                source: 'planner', level: 'info',
                message: `⚡ Tier ${p.tier}: Building ${p.file_count || tierFiles.length} files in ${p.batch_count || 1} parallel agents`,
                worker: 'opus',
                tier: {
                  index: p.tier as number,
                  action: 'start',
                  fileCount: tierFiles.length,
                  files: tierFiles,
                },
              }]);
              break;
            }

            case 'tier_complete': {
              const written = (p.files_written as string[]) || [];
              setLogs((prev) => [...prev, {
                timestamp: new Date().toISOString(),
                source: 'planner', level: 'info',
                message: `✅ Tier ${p.tier} complete: ${p.file_count || written.length} files written`,
                worker: 'opus',
                tier: {
                  index: p.tier as number,
                  action: 'complete',
                  fileCount: written.length,
                  files: written,
                },
              }]);
              // Mark all agents in this tier as done
              setOpusAgents((prev) => prev.map(a =>
                a.tier === (p.tier as number)
                  ? { ...a, status: 'done' as const, files: a.files.map(f => ({ ...f, status: 'done' as const })) }
                  : a
              ));
              break;
            }

            /* ---- Agent tracking events ---- */
            case 'agent_start': {
              const agentId = p.agent_id as string;
              const tier = p.tier as number;
              const files = (p.files as string[]) || [];
              const prefix = (p.common_prefix as string) || '';
              setOpusAgents((prev) => {
                // Don't add duplicate
                if (prev.some(a => a.agentId === agentId)) return prev;
                return [...prev, {
                  agentId,
                  tier,
                  status: 'running' as const,
                  startedAt: new Date().toISOString(),
                  files: files.map(f => ({
                    path: f,
                    displayPath: prefix && f.startsWith(prefix) ? f.slice(prefix.length) : f,
                    status: 'pending' as const,
                  })),
                }];
              });
              break;
            }

            case 'file_generating': {
              // Mark file as building in agent tracker
              const agentId = p.agent_id as string;
              if (agentId) {
                setOpusAgents((prev) => prev.map(a =>
                  a.agentId === agentId
                    ? { ...a, files: a.files.map(f => f.path === p.path ? { ...f, status: 'building' as const } : f) }
                    : a
                ));
              }
              setFileChecklist((prev) => prev.map((c) =>
                c.file === p.path ? { ...c, status: 'written' as const } : c
              ));
              break;
            }

            case 'agent_file_done': {
              const agentId = p.agent_id as string;
              setOpusAgents((prev) => prev.map(a =>
                a.agentId === agentId
                  ? { ...a, files: a.files.map(f => f.path === p.path ? { ...f, status: 'done' as const } : f) }
                  : a
              ));
              break;
            }

            case 'agent_done': {
              const agentId = p.agent_id as string;
              setOpusAgents((prev) => prev.map(a =>
                a.agentId === agentId
                  ? {
                      ...a,
                      status: 'done' as const,
                      files: a.files.map(f => ({ ...f, status: 'done' as const })),
                    }
                  : a
              ));
              break;
            }

            /* ---- Sub-agent events (legacy flat log) ---- */
            case 'subagent_start':
              setLogs((prev) => [...prev, {
                timestamp: new Date().toISOString(),
                source: p.role || 'agent',
                level: 'info',
                message: `🤖 Sub-agent [${p.role}] started — ${p.file_count || 0} files`,
                worker: 'opus',
              }]);
              break;

            case 'subagent_done':
              setLogs((prev) => [...prev, {
                timestamp: new Date().toISOString(),
                source: p.role || 'agent',
                level: 'info',
                message: `✔ Sub-agent [${p.role}] done — ${(p.files_written as string[])?.length || 0} files written`,
                worker: 'opus',
              }]);
              break;
          }
          return;
        }

        /* ────── Upgrade mode WS events (existing) ────── */
        if (p.run_id !== runId) return;

        switch (data.type) {
          case 'upgrade_started':
            setStatus('running');
            setTotalTasks(p.total_tasks || 0);
            // narrator_enabled no longer used — chat replaces narrator
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
              worker: classifyWorker(p.message),
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
            setFileChecklist([]);  // Clear checklist between tasks
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
              audit_status: 'pending',
            }]);
            break;

          case 'file_audit_start':
            setFileDiffs((prev) => prev.map((d) =>
              d.file === p.file && d.task_id === p.task_id
                ? { ...d, audit_status: 'auditing' as const }
                : d
            ));
            setFileChecklist((prev) => prev.map((c) =>
              c.file === p.file ? { ...c, status: 'auditing' as const } : c
            ));
            break;

          case 'file_audit_pass':
            setFileDiffs((prev) => prev.map((d) =>
              d.file === p.file && d.task_id === p.task_id
                ? { ...d, audit_status: 'passed' as const }
                : d
            ));
            setFileChecklist((prev) => prev.map((c) =>
              c.file === p.file ? { ...c, status: 'passed' as const } : c
            ));
            break;

          case 'file_audit_fail':
            setFileDiffs((prev) => prev.map((d) =>
              d.file === p.file && d.task_id === p.task_id
                ? { ...d, audit_status: 'failed' as const, findings: p.findings || [] }
                : d
            ));
            setFileChecklist((prev) => prev.map((c) =>
              c.file === p.file ? { ...c, status: 'failed' as const } : c
            ));
            break;

          case 'upgrade_file_checklist':
            setFileChecklist(
              (p.files || []).map((f: any) => ({
                file: f.file, action: f.action,
                description: f.description, status: f.status || 'pending',
              })),
            );
            break;

          case 'upgrade_file_progress':
            setFileChecklist((prev) => prev.map((c) =>
              c.file === p.file ? { ...c, status: (p.status || 'written') as ChecklistItem['status'] } : c
            ));
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

          case 'upgrade_pushed':
            setLogs((prev) => [...prev, {
              timestamp: new Date().toISOString(),
              source: 'system',
              level: 'system',
              message: `🎉 Pushed to ${p.repo_name} branch ${p.branch || 'main'} (${p.commit_sha?.slice(0, 8) || ''})`,
            }]);
            break;

          case 'upgrade_clear_logs':
            setLogs([]);
            break;

          case 'upgrade_narration':
            // Legacy — narrations now handled via chat
            break;

          case 'upgrade_prompt':
            setPendingPrompt(true);
            setFixProgress(null);  // clear fix indicator when prompt appears
            // Auto-focus the command input
            setTimeout(() => cmdInputRef.current?.focus(), 100);
            break;

          case 'fix_attempt_start':
            setFixProgress({ tier: p.tier, attempt: p.attempt, max: p.max_attempts });
            break;

          case 'fix_attempt_result':
            if (p.passed) setFixProgress(null);
            break;
        }
      },
      [isBuild, runId, entityId],
    ),
  );

  /* Polling fallback */
  useEffect(() => {
    if (status !== 'running' && status !== 'paused') return;
    let consecutiveFailures = 0;
    const pollUrl = isBuild
      ? `${API_BASE}/projects/${projectId}/build/status`
      : `${API_BASE}/scout/runs/${runId}/upgrade-status`;
    const interval = setInterval(async () => {
      try {
        const res = await fetch(pollUrl, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) {
          consecutiveFailures++;
          if (res.status === 404 && consecutiveFailures >= 3) {
            setStatus((prev) =>
              prev === 'running' || prev === 'paused' ? 'completed' : prev,
            );
            clearInterval(interval);
          }
          return;
        }
        consecutiveFailures = 0;
        const data = await res.json();

        if (isBuild) {
          // Build mode polling
          if (data.status === 'running') setStatus('running');
          else if (data.status === 'paused') setStatus('paused');
          else if (data.status === 'completed') { setStatus('completed'); clearInterval(interval); }
          else if (data.status === 'failed') { setStatus('error'); clearInterval(interval); }
          // Also poll live cost
          try {
            const costRes = await fetch(`${API_BASE}/projects/${projectId}/build/live-cost`, {
              headers: { Authorization: `Bearer ${token}` },
            });
            if (costRes.ok) {
              const cd = await costRes.json();
              setCostData(cd);
            }
          } catch { /* silent */ }
        } else {
          // Upgrade mode polling (existing)
          setCompletedTasks(data.completed_tasks || 0);
          if (data.tokens) {
            setTokenUsage({
              opus: data.tokens.opus || { ...EMPTY_BUCKET },
              sonnet: data.tokens.sonnet || { ...EMPTY_BUCKET },
              haiku: data.tokens.haiku || { ...EMPTY_BUCKET },
              total: data.tokens.total || 0,
            });
          }
          // narrator_enabled no longer used
          if (data.status === 'paused') setStatus('paused');
          else if (data.status === 'completed' || data.status === 'error' || data.status === 'stopped') {
            setStatus(data.status as any);
            clearInterval(interval);
          }
          // Backfill logs if WS missed them (use ref to avoid stale closure)
          if (Array.isArray(data.logs) && data.logs.length > logsLenRef.current) {
            setLogs(data.logs.map((l: any) => ({ ...l, worker: classifyWorker(l.message || '') })));
          }
        }
      } catch { /* silent */ }
    }, 4000);
    return () => clearInterval(interval);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, runId, token]);

  /* Submit a clarification answer */
  const submitClarification = async (answer: string) => {
    if (!pendingClarification) return;
    try {
      await fetch(`${API_BASE}/projects/${projectId}/build/clarify`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ question_id: pendingClarification.questionId, answer }),
      });
    } catch { /* WS event confirms success */ }
    // Optimistically clear — WS build_clarification_resolved will confirm
    setPendingClarification(null);
  };

  /* Send a slash command */
  const sendCmd = useCallback(async (cmd: string) => {
    const trimmed = cmd.trim();
    if (!trimmed) return;

    // Intercept /start — trigger execution directly
    if (trimmed.toLowerCase() === '/start') {
      setCmdInput('');
      setCmdSuggestions([]);
      setLogs((prev) => [...prev, {
        timestamp: new Date().toISOString(),
        source: 'user',
        level: 'system',
        message: '> /start',
      }]);

      if (isBuild) {
        // Build mode: start build
        try {
          const res = await fetch(`${API_BASE}/projects/${projectId}/build`, {
            method: 'POST',
            headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
          });
          if (res.ok) {
            const data = await res.json();
            setBuildId(data.id || '');
            setStatus('preparing');
            autoCommenceRef.current = true;
          } else {
            const err = await res.json().catch(() => ({ detail: 'Failed to start build' }));
            const detail = err.detail || 'Failed to start build';
            if (typeof detail === 'string' && (detail.toLowerCase().includes('already') || detail.toLowerCase().includes('running'))) {
              // Build exists — if it's waiting at the ready gate, commence it
              autoCommenceRef.current = true;
              try {
                const commRes = await fetch(`${API_BASE}/projects/${projectId}/build/commence`, {
                  method: 'POST',
                  headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
                  body: JSON.stringify({ action: 'commence' }),
                });
                if (commRes.ok) {
                  setStatus('running');
                } else {
                  // Already commenced / already running — just reflect status
                  setStatus('running');
                }
              } catch {
                setStatus('running');
              }
              return;
            }
            setLogs((prev) => [...prev, {
              timestamp: new Date().toISOString(), source: 'system', level: 'error', message: typeof detail === 'string' ? detail : JSON.stringify(detail),
            }]);
          }
        } catch {
          setLogs((prev) => [...prev, {
            timestamp: new Date().toISOString(), source: 'system', level: 'error', message: 'Network error starting build',
          }]);
          setStatus('error');
        }
      } else {
        // Upgrade mode: start upgrade
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
            if (typeof detail === 'string' && detail.toLowerCase().includes('already in progress')) {
              setStatus('running');
              return;
            }
            setLogs((prev) => [...prev, {
              timestamp: new Date().toISOString(), source: 'system', level: 'error', message: detail,
            }]);
            setStatus('error');
          }
        } catch {
          setLogs((prev) => [...prev, {
            timestamp: new Date().toISOString(), source: 'system', level: 'error', message: 'Network error starting upgrade',
          }]);
          setStatus('error');
        }
      }
      return;
    }

    setCmdHistoryArr((prev) => [trimmed, ...prev.filter((c) => c !== trimmed)].slice(0, 50));
    setHistoryIdx(-1);
    setCmdInput('');
    setCmdSuggestions([]);
    setPendingPrompt(false);

    // Render user input as a log line locally
    setLogs((prev) => [...prev, {
      timestamp: new Date().toISOString(),
      source: 'user',
      level: 'system',
      message: `> ${trimmed}`,
    }]);

    // Non-slash input → route to build chat (Haiku Q&A)
    if (!trimmed.startsWith('/')) {
      const ts = new Date().toISOString();
      setChatMessages((prev) => [...prev, { role: 'user', text: trimmed, timestamp: ts }]);
      setLeftTab('chat');
      setChatLoading(true);
      try {
        const chatRes = await fetch(`${API_BASE}/projects/${projectId}/build/chat`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: trimmed }),
        });
        if (chatRes.ok) {
          const chatData = await chatRes.json();
          setChatMessages((prev) => [...prev, {
            role: 'assistant', text: chatData.reply || '(no response)',
            timestamp: new Date().toISOString(),
          }]);
        } else {
          const errBody = await chatRes.json().catch(() => ({ detail: 'Chat request failed' }));
          setChatMessages((prev) => [...prev, {
            role: 'assistant',
            text: `Error: ${errBody.detail || errBody.message || 'Request failed'}`,
            timestamp: new Date().toISOString(),
          }]);
        }
      } catch {
        setChatMessages((prev) => [...prev, {
          role: 'assistant', text: 'Network error — could not reach the server.',
          timestamp: new Date().toISOString(),
        }]);
      } finally {
        setChatLoading(false);
      }
      return;
    }

    if (isBuild) {
      // Build mode: route commands to build endpoints
      const lower = trimmed.toLowerCase();
      try {
        let res: Response;
        if (lower === '/nuke') {
          if (!confirm('☢ NUKE BUILD — this will delete the branch, mark the build as nuked, and cannot be undone. Proceed?')) return;
          res = await fetch(`${API_BASE}/projects/${projectId}/build/nuke`, {
            method: 'POST',
            headers: { Authorization: `Bearer ${token}` },
          });
          if (res.ok) {
            setLogs((prev) => [...prev, {
              timestamp: new Date().toISOString(), source: 'system', level: 'warn',
              message: '☢ Build nuked — branch deleted, record marked permanently.',
            }]);
            setStatus('stopped');
            setTimeout(() => onClose(), 1500);
            return;
          }
        } else if (lower === '/stop') {
          res = await fetch(`${API_BASE}/projects/${projectId}/build/cancel`, {
            method: 'POST',
            headers: { Authorization: `Bearer ${token}` },
          });
        } else if (lower === '/resume' || lower.startsWith('/resume ')) {
          // Extract action from /resume or default to 'retry'
          const action = trimmed.split(/\s+/)[1] || 'retry';
          res = await fetch(`${API_BASE}/projects/${projectId}/build/resume`, {
            method: 'POST',
            headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action }),
          });
        } else if (lower === '/status') {
          res = await fetch(`${API_BASE}/projects/${projectId}/build/status`, {
            headers: { Authorization: `Bearer ${token}` },
          });
          try {
            const body = await res.json();
            setLogs((prev) => [...prev, {
              timestamp: new Date().toISOString(), source: 'command-ack', level: 'system',
              message: `Status: ${body.status || 'unknown'} · Phase: ${body.phase || '?'} · Loop: ${body.loop_count || 0}`,
            }]);
          } catch { /* silent */ }
          return;
        } else if (lower === '/clear') {
          setLogs([]);
          return;
        } else {
          // All other commands → interject
          res = await fetch(`${API_BASE}/projects/${projectId}/build/interject`, {
            method: 'POST',
            headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: trimmed }),
          });
        }
        if (!res.ok) {
          // Server returned an error — show it
          try {
            const errBody = await res.json();
            setLogs((prev) => [...prev, {
              timestamp: new Date().toISOString(), source: 'command-ack',
              level: 'error',
              message: errBody.detail || errBody.message || errBody.error || `Command failed (${res.status})`,
            }]);
          } catch {
            setLogs((prev) => [...prev, {
              timestamp: new Date().toISOString(), source: 'command-ack',
              level: 'error',
              message: `Command failed (${res.status})`,
            }]);
          }
        } else {
          try {
            const body = await res.json();
            if (body.message || body.status) {
              setLogs((prev) => [...prev, {
                timestamp: new Date().toISOString(), source: 'command-ack',
                level: body.ok === false ? 'error' : 'system',
                message: body.message || `Status: ${body.status}`,
              }]);
            }
          } catch { /* silent */ }
        }
      } catch {
        setLogs((prev) => [...prev, {
          timestamp: new Date().toISOString(), source: 'system', level: 'error',
          message: 'Failed to send command (network error)',
        }]);
      }
    } else {
      // Upgrade mode: existing command handling
      try {
        const res = await fetch(`${API_BASE}/scout/runs/${runId}/command`, {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ command: trimmed }),
        });
        try {
          const body = await res.json();
          if (body.message) {
            setLogs((prev) => [...prev, {
              timestamp: new Date().toISOString(),
              source: 'command-ack',
              level: body.ok === false ? 'error' : 'system',
              message: body.message,
            }]);
          }
        } catch { /* response may not be JSON — ignore */ }
      } catch {
        setLogs((prev) => [...prev, {
          timestamp: new Date().toISOString(),
          source: 'system',
          level: 'error',
          message: 'Failed to send command (network error)',
        }]);
      }
    }
  }, [isBuild, runId, projectId, token]);

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

  /* Auto-scroll chat when new messages arrive */
  useEffect(() => {
    if (chatScrollRef.current) {
      chatScrollRef.current.scrollTop = chatScrollRef.current.scrollHeight;
    }
  }, [chatMessages.length]);

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
          background: status === 'ready' ? '#22C55E' : status === 'running' ? '#22C55E' : status === 'completed' ? '#3B82F6' : status === 'error' ? '#EF4444' : status === 'paused' ? '#F59E0B' : status === 'stopping' || status === 'stopped' ? '#F97316' : status === 'preparing' ? '#38BDF8' : '#64748B',
          animation: status === 'running' ? 'pulse 1.5s ease-in-out infinite' : status === 'ready' ? 'pulse 2s ease-in-out infinite' : status === 'paused' ? 'pulse 2.5s ease-in-out infinite' : status === 'preparing' ? 'pulse 1s ease-in-out infinite' : 'none',
        }} />
        <span style={{ fontFamily: 'monospace', fontSize: '0.85rem', fontWeight: 600, color: '#F1F5F9' }}>
          {isBuild ? 'FORGE BUILD' : 'FORGE IDE'}
        </span>
        <span style={{ color: '#64748B', fontSize: '0.75rem' }}>—</span>
        <span style={{ color: '#94A3B8', fontSize: '0.75rem', fontFamily: 'monospace' }}>
          {repoName}
        </span>

        {/* Status badge */}
        <span style={{
          padding: '2px 10px', borderRadius: '10px', fontSize: '0.65rem',
          fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px',
          background: status === 'ready' ? '#14532D' : status === 'running' ? '#14532D' : status === 'completed' ? '#1E3A5F' : status === 'error' ? '#7F1D1D' : status === 'paused' ? '#78350F' : status === 'stopping' || status === 'stopped' ? '#7C2D12' : status === 'preparing' ? '#1E3A5F' : '#1E293B',
          color: status === 'ready' ? '#22C55E' : status === 'running' ? '#22C55E' : status === 'completed' ? '#3B82F6' : status === 'error' ? '#EF4444' : status === 'paused' ? '#F59E0B' : status === 'stopping' || status === 'stopped' ? '#F97316' : status === 'preparing' ? '#38BDF8' : '#64748B',
        }}>
          {status === 'preparing' ? 'Preparing…' : status === 'ready' ? 'Ready' : status}
        </span>

        {/* Auto-fix progress indicator */}
        {fixProgress && (
          <span style={{
            padding: '2px 10px', borderRadius: '10px', fontSize: '0.65rem',
            fontWeight: 600, letterSpacing: '0.5px',
            background: fixProgress.tier === 1 ? '#1E3A5F' : '#3B1F6E',
            color: fixProgress.tier === 1 ? '#60A5FA' : '#A78BFA',
            animation: 'pulse 1.5s ease-in-out infinite',
          }}>
            {fixProgress.tier === 1 ? '🔧' : '🧠'} FIX {fixProgress.attempt}/{fixProgress.max}
          </span>
        )}

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
              {completedTasks}/{totalTasks} {isBuild ? 'phases' : 'tasks'} — {pct}%
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
            {/* Haiku counter (chat) */}
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
            {/* Total */}
            <div style={{ borderLeft: '1px solid #334155', paddingLeft: '8px' }}>
              <span style={{
                fontSize: '0.65rem', fontFamily: 'monospace', color: '#94A3B8',
              }}>
                Σ {fmtTokens(tokenUsage.total)}
              </span>
            </div>
            {/* Cost estimate */}
            <div style={{ borderLeft: '1px solid #334155', paddingLeft: '8px' }}>
              <span style={{
                fontSize: '0.65rem', fontFamily: 'monospace', color: '#22C55E',
                fontWeight: 600,
              }}>
                {costData ? fmtCost(costData.total_cost_usd) : fmtCost(estimateCost(tokenUsage))}
              </span>
              {costData?.spend_cap != null && (
                <span style={{
                  fontSize: '0.5rem', color: (costData.pct_used || 0) > 80 ? '#EF4444' : '#64748B',
                  marginLeft: '4px',
                }}>
                  / ${costData.spend_cap.toFixed(0)}
                </span>
              )}
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
        {leftCollapsed ? (
          <div style={{
            width: '24px', flexShrink: 0,
            background: '#0F172A', borderRight: '1px solid #1E293B',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <button
              onClick={() => setLeftCollapsed(false)}
              title="Expand sidebar"
              style={{
                background: 'transparent', border: 'none', cursor: 'pointer',
                color: '#475569', fontSize: '0.7rem', padding: '6px 2px',
                lineHeight: 1, transition: 'color 0.15s',
                writingMode: 'vertical-rl',
              }}
              onMouseEnter={e => (e.currentTarget.style.color = '#F1F5F9')}
              onMouseLeave={e => (e.currentTarget.style.color = '#475569')}
            >
              ▸
            </button>
          </div>
        ) : (
        <div style={{
          width: '280px', flexShrink: 0,
          background: '#0F172A', borderRight: '1px solid #1E293B',
          display: 'flex', flexDirection: 'column',
        }}>
          {/* Left tab switcher */}
          <div style={{
            display: 'flex', borderBottom: '1px solid #1E293B', flexShrink: 0,
          }}>
            {(['tasks', 'chat'] as const).map((tab) => (
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
                {tab === 'tasks' ? (isBuild ? `Phases (${tasks.length})` : `Tasks (${tasks.length})`) : `💬 Chat${chatMessages.length > 0 ? ` (${chatMessages.length})` : ''}`}
              </button>
            ))}
            <button
              onClick={() => setLeftCollapsed(true)}
              title="Collapse sidebar"
              style={{
                background: 'transparent', border: 'none', cursor: 'pointer',
                color: '#475569', fontSize: '0.75rem', padding: '4px 8px',
                flexShrink: 0, lineHeight: 1,
                transition: 'color 0.15s',
              }}
              onMouseEnter={e => (e.currentTarget.style.color = '#F1F5F9')}
              onMouseLeave={e => (e.currentTarget.style.color = '#475569')}
            >
              ◂
            </button>
          </div>

          {/* Tasks tab */}
          {leftTab === 'tasks' && (
            <div style={{ flex: 1, overflowY: 'auto', padding: '12px' }}>
              {tasks.length === 0 && status === 'preparing' && (
                <div style={{ color: '#475569', fontSize: '0.75rem', padding: '8px' }}>
                  Initializing…
                </div>
              )}

              {tasks.map((task, i) => {
                const phNum = parseInt(task.id.replace('phase_', ''), 10);
                const hasFiles = (task.status === 'proposed' || task.status === 'skipped') && phaseFiles[phNum]?.length > 0;
                const isExpanded = expandedPhase === phNum && hasFiles;
                return (
                <div
                  key={task.id}
                  style={{
                    display: 'flex', flexDirection: 'column', gap: '4px',
                    padding: '8px 10px', marginBottom: '4px',
                    background: task.status === 'running' ? '#1E293B' : isExpanded ? '#0F172A' : 'transparent',
                    borderRadius: '6px',
                    borderLeft: `3px solid ${
                      task.status === 'running' ? '#3B82F6' :
                      task.status === 'proposed' ? '#22C55E' :
                      task.status === 'skipped' ? '#F59E0B' :
                      task.status === 'error' ? '#EF4444' : '#334155'
                    }`,
                    transition: 'all 0.3s ease',
                    cursor: hasFiles ? 'pointer' : 'default',
                  }}
                  onClick={() => { if (hasFiles) setExpandedPhase(prev => prev === phNum ? null : phNum); }}
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
                    {hasFiles && (
                      <span style={{
                        fontSize: '0.6rem', color: '#64748B', fontFamily: 'monospace',
                        marginRight: '2px', flexShrink: 0,
                      }}>
                        {phaseFiles[phNum].length} file{phaseFiles[phNum].length !== 1 ? 's' : ''} {isExpanded ? '▾' : '▸'}
                      </span>
                    )}
                    {task.changes_count != null && task.changes_count > 0 && !hasFiles && (
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

                  {/* Expanded file list for completed/skipped phases */}
                  {isExpanded && (
                    <div style={{
                      marginTop: '6px', paddingTop: '6px',
                      borderTop: '1px solid #1E293B',
                    }}>
                      {phaseFiles[phNum].map((f, fi) => {
                        const fname = f.path.split('/').pop() || f.path;
                        const dir = f.path.includes('/') ? f.path.substring(0, f.path.lastIndexOf('/')) : '';
                        return (
                          <div
                            key={fi}
                            style={{
                              display: 'flex', alignItems: 'center', gap: '6px',
                              padding: '3px 4px', borderRadius: '3px',
                              fontSize: '0.65rem', fontFamily: 'monospace',
                              color: '#CBD5E1',
                            }}
                            title={`${f.path}${f.size_bytes ? ` (${(f.size_bytes / 1024).toFixed(1)} KB)` : ''}${f.committed ? ' — committed' : ' — on disk'}`}
                          >
                            <span style={{
                              flexShrink: 0, fontSize: '0.6rem',
                              color: f.committed ? '#22C55E' : '#F59E0B',
                            }}>
                              {f.committed ? '●' : '○'}
                            </span>
                            <span style={{ color: '#64748B', flexShrink: 0 }}>
                              {f.language === 'python' ? '🐍' :
                               f.language === 'typescript' || f.language === 'javascript' ? '📜' :
                               f.language === 'json' ? '📋' :
                               f.language === 'css' || f.language === 'scss' ? '🎨' :
                               f.language === 'html' ? '🌐' :
                               f.language === 'markdown' ? '📝' : '📄'}
                            </span>
                            <span style={{
                              overflow: 'hidden', textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap', flex: 1,
                            }}>
                              {fname}
                            </span>
                            {dir && (
                              <span style={{
                                fontSize: '0.55rem', color: '#475569',
                                overflow: 'hidden', textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap', maxWidth: '90px',
                                direction: 'rtl', textAlign: 'left',
                              }}>
                                {dir}
                              </span>
                            )}
                          </div>
                        );
                      })}
                      <div style={{
                        display: 'flex', gap: '8px', marginTop: '4px',
                        fontSize: '0.55rem', color: '#64748B', padding: '2px 4px',
                      }}>
                        <span style={{ color: '#22C55E' }}>● committed</span>
                        <span style={{ color: '#F59E0B' }}>○ on disk</span>
                      </div>
                    </div>
                  )}
                </div>
                );
              })}
            </div>
          )}

          {/* Chat tab */}
          {leftTab === 'chat' && (
            <div ref={chatScrollRef} style={{ flex: 1, overflowY: 'auto', padding: '8px' }}>
              {chatMessages.length === 0 ? (
                <div style={{ color: '#475569', fontSize: '0.7rem', padding: '8px', lineHeight: 1.6 }}>
                  <div style={{ color: '#38BDF8', fontSize: '0.65rem', fontWeight: 700, marginBottom: '6px' }}>💬 BUILD CHAT</div>
                  <div style={{ marginBottom: '6px' }}>Ask anything about your build — type in the command line below (no / prefix).</div>
                  <div style={{ fontSize: '0.6rem', color: '#64748B' }}>
                    Try: "What files are in phase 1?" · "What errors do we have?" · "What's the build status?"
                  </div>
                </div>
              ) : (
                chatMessages.map((m, i) => (
                  <div
                    key={i}
                    style={{
                      display: 'flex',
                      justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
                      marginBottom: '6px',
                    }}
                  >
                    <div style={{
                      maxWidth: '90%',
                      padding: '6px 10px',
                      borderRadius: m.role === 'user' ? '8px 8px 2px 8px' : '8px 8px 8px 2px',
                      background: m.role === 'user' ? '#1E3A5F' : '#1E293B',
                      borderLeft: m.role === 'assistant' ? '2px solid #38BDF8' : 'none',
                      borderRight: m.role === 'user' ? '2px solid #3B82F6' : 'none',
                    }}>
                      <div style={{
                        fontSize: '0.5rem', color: '#64748B', marginBottom: '2px',
                        display: 'flex', alignItems: 'center', gap: '4px',
                      }}>
                        {m.role === 'assistant' && (
                          <span style={{
                            fontSize: '0.45rem', fontWeight: 700, padding: '0 4px',
                            borderRadius: '2px', background: '#38BDF822', color: '#38BDF8',
                          }}>HAIKU</span>
                        )}
                        <span>{new Date(m.timestamp).toLocaleTimeString()}</span>
                      </div>
                      <div style={{
                        fontSize: '0.7rem', color: m.role === 'user' ? '#93C5FD' : '#CBD5E1',
                        lineHeight: 1.45, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                      }}>
                        {m.text}
                      </div>
                    </div>
                  </div>
                ))
              )}
              {chatLoading && (
                <div style={{
                  display: 'flex', justifyContent: 'flex-start', marginBottom: '6px',
                }}>
                  <div style={{
                    padding: '6px 10px', borderRadius: '8px 8px 8px 2px',
                    background: '#1E293B', borderLeft: '2px solid #38BDF8',
                    color: '#38BDF8', fontSize: '0.65rem', fontFamily: 'monospace',
                  }}>
                    <span className="forge-ide-dots">Thinking</span>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
        )}

        {/* ── Right Panel: Activity Log + Changes ── */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: '#0B1120' }}>

          {/* Tab bar */}
          <div style={{
            display: 'flex', alignItems: 'center', borderBottom: '1px solid #1E293B', flexShrink: 0,
          }}>
            {/* Copy log button */}
            <button
              title="Copy activity log to clipboard"
              onClick={() => {
                const text = logs.map(l => {
                  const ts = l.timestamp ? new Date(l.timestamp).toLocaleTimeString() : '';
                  return `${ts}  ${l.message}`;
                }).join('\n');
                navigator.clipboard.writeText(text).then(() => {
                  const btn = document.getElementById('forge-copy-log-btn');
                  if (btn) { btn.textContent = '✓'; setTimeout(() => { btn.textContent = '⧉'; }, 1200); }
                });
              }}
              id="forge-copy-log-btn"
              style={{
                background: 'transparent', border: 'none', cursor: 'pointer',
                color: '#64748B', fontSize: '0.85rem', padding: '6px 10px',
                lineHeight: 1, flexShrink: 0, transition: 'color 0.2s',
              }}
              onMouseEnter={e => (e.currentTarget.style.color = '#F1F5F9')}
              onMouseLeave={e => (e.currentTarget.style.color = '#64748B')}
            >
              ⧉
            </button>
            {(['activity', 'changes', 'errors'] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                style={{
                  padding: '8px 20px', fontSize: '0.75rem', fontWeight: 500,
                  background: 'transparent', border: 'none', cursor: 'pointer',
                  color: activeTab === tab ? '#F1F5F9' : '#64748B',
                  borderBottom: activeTab === tab ? '2px solid #3B82F6' : '2px solid transparent',
                  textTransform: 'uppercase', letterSpacing: '0.5px',
                  position: 'relative',
                }}
              >
                {tab === 'activity'
                  ? `Activity (${logs.length})`
                  : tab === 'changes'
                    ? `Changes (${fileDiffs.length})`
                    : `Errors (${buildErrors.filter(e => !e.resolved).length})`}
                {/* Red badge for unresolved errors */}
                {tab === 'errors' && buildErrors.filter(e => !e.resolved).length > 0 && activeTab !== 'errors' && (
                  <span style={{
                    position: 'absolute', top: '2px', right: '2px',
                    width: '7px', height: '7px', borderRadius: '50%',
                    background: '#EF4444',
                  }} />
                )}
              </button>
            ))}
          </div>

          {/* Activity log — unified left (40%) + stacked Opus/Sonnet right (60%) */}
          {activeTab === 'activity' && (
            <div style={{ flex: 1, display: 'flex', flexDirection: 'row', overflow: 'hidden' }}>
              {/* System/command log — 40% */}
              <div style={{ width: '40%', flexShrink: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                {setupEndIndex >= 0 && (
                  <button
                    onClick={() => setSetupCollapsed(c => !c)}
                    style={{
                      background: '#1E293B', border: 'none', borderBottom: '1px solid #334155',
                      color: '#94A3B8', fontSize: '0.7rem', padding: '4px 12px',
                      cursor: 'pointer', textAlign: 'left', flexShrink: 0,
                    }}
                  >
                    {setupCollapsed ? '▸ Setup logs collapsed — click to expand' : '▾ Setup logs expanded — click to collapse'}
                  </button>
                )}
                <LogPane
                  logs={systemLogs}
                  status={status}
                  label="LOG"
                  labelColor="#94A3B8"
                  emptyText={status === 'preparing' ? 'Preparing workspace…' : status === 'ready' ? 'Ready — type /start and press Enter to begin' : 'Waiting for output…'}
                />
                {fileChecklist.length > 0 && <FileChecklist items={fileChecklist} />}
              </div>
              {/* Divider */}
              <div style={{ width: '1px', background: '#1E293B', flexShrink: 0 }} />
              {/* Right column — stacked Opus (top) + Sonnet (bottom) — 60% */}
              <div ref={rightColRef} style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                {/* Opus — top (agent grouped view) */}
                <div style={{ height: `${opusPct}%`, flexShrink: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                  <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                    <AgentPanel
                      agents={opusAgents}
                      status={status}
                      opusLogs={opusLogs}
                      label="OPUS"
                      labelColor="#D946EF"
                      emptyText={status === 'preparing' ? 'Preparing…' : status === 'ready' ? 'Opus builder will appear here' : 'Waiting for builder…'}
                    />
                  </div>
                </div>
                {/* Draggable resize handle */}
                <div
                  onMouseDown={handleDragStart}
                  style={{
                    height: '5px', flexShrink: 0, cursor: 'row-resize',
                    background: '#1E293B', position: 'relative',
                    transition: draggingRef.current ? 'none' : 'background 0.2s',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.background = '#334155'; }}
                  onMouseLeave={e => { if (!draggingRef.current) e.currentTarget.style.background = '#1E293B'; }}
                >
                  {/* Grip dots */}
                  <div style={{
                    position: 'absolute', left: '50%', top: '50%',
                    transform: 'translate(-50%, -50%)',
                    display: 'flex', gap: '3px',
                  }}>
                    {[0,1,2].map(k => (
                      <div key={k} style={{ width: '3px', height: '3px', borderRadius: '50%', background: '#475569' }} />
                    ))}
                  </div>
                </div>
                {/* Sonnet — bottom */}
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                  <LogPane
                    logs={sonnetLogs}
                    status={status}
                    label="SONNET"
                    labelColor="#38BDF8"
                    emptyText={status === 'preparing' ? 'Preparing…' : status === 'ready' ? 'Sonnet planner will appear here' : 'Waiting for planner…'}
                  />
                </div>
              </div>
            </div>
          )}

          {/* Command input bar — always visible on activity tab */}
          {activeTab === 'activity' && (
            <div style={{
              flexShrink: 0,
              borderTop: pendingClarification ? '1px solid #FBBF2466' : pendingPrompt ? '1px solid #FBBF2466' : status === 'ready' ? '1px solid #22C55E33' : status === 'preparing' ? '1px solid #38BDF833' : '1px solid #1E293B',
              background: pendingClarification ? '#1C1A10' : pendingPrompt ? '#1C1510' : '#0F172A', padding: '0', position: 'relative',
              boxShadow: pendingClarification ? '0 -2px 20px rgba(251,191,36,0.15)' : pendingPrompt ? '0 -2px 20px rgba(251, 191, 36, 0.15)' : status === 'ready' ? '0 -2px 20px rgba(34, 197, 94, 0.12)' : status === 'preparing' ? '0 -2px 20px rgba(56, 189, 248, 0.12)' : 'none',
              transition: 'box-shadow 0.3s ease, border-color 0.3s ease, background 0.3s ease',
            }}>
              {/* Clarification card — shown when builder asks a question */}
              {pendingClarification && (
                <div style={{
                  margin: '8px 12px',
                  padding: '10px 14px',
                  background: '#1C1A10',
                  border: '1px solid #FBBF2466',
                  borderRadius: '6px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '8px',
                }}>
                  <div style={{ color: '#FBBF24', fontSize: '0.8rem', fontWeight: 700 }}>
                    {pendingClarification.question}
                  </div>
                  {pendingClarification.context && (
                    <div style={{ color: '#94A3B8', fontSize: '0.72rem' }}>
                      {pendingClarification.context}
                    </div>
                  )}
                  {pendingClarification.options && pendingClarification.options.length > 0 && (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                      {pendingClarification.options.map((opt) => (
                        <button
                          key={opt}
                          onClick={() => { submitClarification(opt); setCmdInput(''); }}
                          style={{
                            background: '#292114',
                            border: '1px solid #FBBF2466',
                            borderRadius: '4px',
                            color: '#FBBF24',
                            fontSize: '0.72rem',
                            padding: '4px 10px',
                            cursor: 'pointer',
                            fontWeight: 500,
                          }}
                        >
                          {opt}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {/* Autocomplete dropdown */}
              {cmdSuggestions.length > 0 && status !== 'ready' && (
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
                  color: status === 'preparing' ? '#38BDF8' : '#22C55E',
                  fontFamily: 'monospace', fontSize: '0.8rem',
                  fontWeight: 700, flexShrink: 0,
                  animation: status === 'ready' ? 'pulseGreen 2s ease-in-out infinite' : status === 'preparing' ? 'pulseBlue 1.5s ease-in-out infinite' : 'none',
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
                      // If a clarification is pending, treat input as the answer
                      if (pendingClarification && cmdInput.trim()) {
                        submitClarification(cmdInput.trim());
                        setCmdInput('');
                        return;
                      }
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
                  placeholder={pendingClarification ? 'Type your answer or choose an option above…' : pendingPrompt ? (isBuild ? 'Type retry, skip, abort, or edit…' : 'Type Y or N…') : status === 'ready' ? (isBuild ? 'Press Enter to start build…' : 'Press Enter to start upgrade…') : 'Ask a question or type / for commands…'}
                  style={{
                    flex: 1, background: 'transparent', border: 'none', outline: 'none',
                    color: pendingPrompt ? '#FBBF24'
                      : status === 'ready' && cmdInput.trim().toLowerCase() === '/start' ? '#22C55E' : '#E2E8F0',
                    fontFamily: '"Cascadia Code", "Fira Code", "JetBrains Mono", monospace',
                    fontSize: '0.8rem', caretColor: pendingPrompt ? '#FBBF24' : '#22C55E',
                    fontWeight: pendingPrompt ? 700
                      : status === 'ready' && cmdInput.trim().toLowerCase() === '/start' ? 700 : 400,
                  }}
                  autoComplete="off"
                  spellCheck={false}
                  autoFocus={status === 'ready'}
                />
                {status === 'ready' && (
                  <button
                    onClick={() => sendCmd('/start')}
                    style={{
                      background: '#14532D', color: '#22C55E',
                      border: '1px solid #22C55E44', borderRadius: '4px',
                      padding: '3px 12px', cursor: 'pointer',
                      fontSize: '0.7rem', fontWeight: 700,
                      animation: 'pulseGreen 2s ease-in-out infinite',
                    }}
                  >
                    ▶ START
                  </button>
                )}
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
                        {/* Audit badge */}
                        {diff.audit_status === 'auditing' && (
                          <span style={{
                            fontSize: '0.6rem', fontWeight: 600, padding: '1px 6px',
                            borderRadius: '4px', background: '#F9731622', color: '#F97316',
                            animation: 'pulse 1.5s ease-in-out infinite',
                          }}>
                            ● AUDITING
                          </span>
                        )}
                        {diff.audit_status === 'passed' && (
                          <span style={{
                            fontSize: '0.6rem', fontWeight: 600, padding: '1px 6px',
                            borderRadius: '4px', background: '#22C55E22', color: '#22C55E',
                          }}>
                            ✓✓ PASS
                          </span>
                        )}
                        {diff.audit_status === 'failed' && (
                          <span style={{
                            fontSize: '0.6rem', fontWeight: 600, padding: '1px 6px',
                            borderRadius: '4px', background: '#EF444422', color: '#EF4444',
                          }}>
                            ✗ FAIL
                          </span>
                        )}
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
                          {diff.audit_status === 'failed' && diff.findings && diff.findings.length > 0 && (
                            <div style={{
                              marginBottom: '8px', padding: '6px 10px',
                              background: '#1A0000', border: '1px solid #7F1D1D',
                              borderRadius: '4px', fontSize: '0.7rem',
                            }}>
                              <div style={{ color: '#EF4444', fontWeight: 600, marginBottom: '4px', fontSize: '0.65rem', textTransform: 'uppercase' }}>
                                Audit Findings
                              </div>
                              {diff.findings.map((finding, fi) => (
                                <div key={fi} style={{ color: '#FCA5A5', padding: '1px 0' }}>
                                  • {finding}
                                </div>
                              ))}
                            </div>
                          )}
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

          {/* Errors tab */}
          {activeTab === 'errors' && (
            <ErrorsPanel
              errors={buildErrors}
              onDismiss={async (errorId) => {
                // Optimistic UI update
                setBuildErrors((prev) => prev.map((e) =>
                  e.id === errorId ? { ...e, resolved: true, resolved_at: new Date().toISOString(), resolution_method: 'dismissed' } : e,
                ));
                // Persist to backend
                try {
                  await fetch(`${API_BASE}/projects/${projectId}/build/errors/dismiss`, {
                    method: 'POST',
                    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ error_id: errorId }),
                  });
                } catch {
                  // Revert on failure
                  setBuildErrors((prev) => prev.map((e) =>
                    e.id === errorId ? { ...e, resolved: false, resolved_at: undefined, resolution_method: undefined } : e,
                  ));
                }
              }}
            />
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
            {status === 'stopped' ? '🛑 Execution stopped by user' : isBuild ? '✅ Build complete' : '✅ Upgrade analysis complete'}
          </span>
          <span style={{ color: '#64748B', fontSize: '0.75rem' }}>
            {isBuild
              ? `${completedTasks} phase(s) completed`
              : `${fileDiffs.length} file change(s) proposed across ${completedTasks} task(s)`}
            {tokenUsage.total > 0 && ` · ${fmtTokens(tokenUsage.total)} tokens`}
            {(costData?.total_cost_usd || estimateCost(tokenUsage) > 0) && ` · ${costData ? fmtCost(costData.total_cost_usd) : fmtCost(estimateCost(tokenUsage))}`}
          </span>
          <div style={{ flex: 1 }} />
          {fileDiffs.length > 0 && (
            <button
              onClick={() => sendCmd('/push')}
              style={{
                background: 'linear-gradient(135deg, #14532D, #166534)', color: '#22C55E',
                border: '1px solid #22C55E44', borderRadius: '6px',
                padding: '6px 16px', cursor: 'pointer',
                fontSize: '0.75rem', fontWeight: 600,
              }}
            >
              🚀 Push to GitHub
            </button>
          )}
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

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
        @keyframes pulseGreen {
          0%, 100% { opacity: 1; text-shadow: 0 0 8px rgba(34, 197, 94, 0.8); }
          50% { opacity: 0.6; text-shadow: 0 0 2px rgba(34, 197, 94, 0.3); }
        }
        @keyframes pulseBlue {
          0%, 100% { opacity: 1; text-shadow: 0 0 8px rgba(56, 189, 248, 0.8); }
          50% { opacity: 0.4; text-shadow: 0 0 2px rgba(56, 189, 248, 0.3); }
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
