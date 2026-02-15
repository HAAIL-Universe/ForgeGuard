/**
 * ContractProgress -- live step-by-step contract generation progress panel.
 *
 * Shows each contract being generated with status indicators, a running log,
 * context window meter, and cumulative token usage from the questionnaire.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { useWebSocket } from '../hooks/useWebSocket';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

/* ------------------------------------------------------------------ */
/*  Contract type labels                                              */
/* ------------------------------------------------------------------ */

const CONTRACT_LABELS: Record<string, string> = {
  blueprint: 'Blueprint',
  manifesto: 'Manifesto',
  stack: 'Stack',
  schema: 'Schema',
  physics: 'Physics',
  boundaries: 'Boundaries',
  phases: 'Phases',
  ui: 'UI',
  builder_directive: 'Builder Directive',
};

const ALL_CONTRACTS = Object.keys(CONTRACT_LABELS);

/* ------------------------------------------------------------------ */
/*  Context window constants                                          */
/* ------------------------------------------------------------------ */

const MODEL_CONTEXT_WINDOWS: Record<string, number> = {
  'claude-haiku-4-5': 200_000,
  'claude-sonnet-4-5': 200_000,
  'claude-opus-4-6': 200_000,
  'gpt-4o': 128_000,
};
const DEFAULT_CONTEXT_WINDOW = 200_000;

/* ------------------------------------------------------------------ */
/*  Types                                                             */
/* ------------------------------------------------------------------ */

interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
}

type ContractStatus = 'pending' | 'generating' | 'done';

interface LogEntry {
  time: string;
  message: string;
}

interface Props {
  projectId: string;
  tokenUsage: TokenUsage;
  model: string;
  onComplete: () => void;
}

/* ------------------------------------------------------------------ */
/*  Styles                                                            */
/* ------------------------------------------------------------------ */

const panelStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '12px',
  padding: '16px 20px',
  flex: 1,
  overflowY: 'auto',
};

const stepRowStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: '10px',
  fontSize: '0.82rem',
  padding: '6px 0',
  borderBottom: '1px solid #1E293B',
};

const logPanelStyle: React.CSSProperties = {
  background: '#0F172A',
  borderRadius: '6px',
  padding: '10px 12px',
  fontFamily: 'monospace',
  fontSize: '0.72rem',
  color: '#94A3B8',
  maxHeight: '120px',
  overflowY: 'auto',
  lineHeight: '1.6',
};

const meterBarOuter: React.CSSProperties = {
  flex: 1,
  height: '8px',
  background: '#1E293B',
  borderRadius: '4px',
  overflow: 'hidden',
};

/* ------------------------------------------------------------------ */
/*  Component                                                         */
/* ------------------------------------------------------------------ */

export default function ContractProgress({ projectId, tokenUsage: initialTokenUsage, model, onComplete }: Props) {
  const { token } = useAuth();
  const [statuses, setStatuses] = useState<Record<string, ContractStatus>>(() =>
    Object.fromEntries(ALL_CONTRACTS.map((c) => [c, 'pending' as const])),
  );
  const [log, setLog] = useState<LogEntry[]>([]);
  const [generating, setGenerating] = useState(false);
  const [allDone, setAllDone] = useState(false);
  const [cumulativeTokens, setCumulativeTokens] = useState<TokenUsage>(initialTokenUsage);
  const logEndRef = useRef<HTMLDivElement>(null);
  const startedRef = useRef(false);

  const addLog = useCallback((msg: string) => {
    const now = new Date();
    const time = now.toLocaleTimeString('en-GB', { hour12: false });
    setLog((prev) => [...prev, { time, message: msg }]);
  }, []);

  /* Auto-scroll log */
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [log]);

  /* Handle WS progress messages */
  useWebSocket(
    useCallback(
      (data: { type: string; payload: any }) => {
        if (data.type !== 'contract_progress') return;
        const p = data.payload;
        if (p.project_id !== projectId) return;

        const label = CONTRACT_LABELS[p.contract_type] ?? p.contract_type;
        if (p.status === 'generating') {
          setStatuses((prev) => ({ ...prev, [p.contract_type]: 'generating' }));
          addLog(`Generating ${label}...`);
        } else if (p.status === 'done') {
          setStatuses((prev) => ({ ...prev, [p.contract_type]: 'done' }));
          const inTok = p.input_tokens ?? 0;
          const outTok = p.output_tokens ?? 0;
          addLog(`✓ ${label} complete (${inTok.toLocaleString()} in / ${outTok.toLocaleString()} out)`);

          /* Accumulate token usage */
          if (inTok || outTok) {
            setCumulativeTokens((prev) => ({
              input_tokens: prev.input_tokens + inTok,
              output_tokens: prev.output_tokens + outTok,
            }));
          }

          /* Check if all done */
          setStatuses((prev) => {
            const values = Object.values(prev);
            if (values.every((s) => s === 'done')) {
              setAllDone(true);
              addLog('All contracts generated successfully.');
            }
            return prev;
          });
        }
      },
      [projectId, addLog],
    ),
  );

  /* Kick off generation on mount */
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    setGenerating(true);
    addLog('Starting contract generation...');

    fetch(`${API_BASE}/projects/${projectId}/contracts/generate`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((res) => {
        if (!res.ok) throw new Error('Generation failed');
        /* Mark any remaining as done (safety net) */
        setStatuses((prev) => {
          const updated = { ...prev };
          for (const key of ALL_CONTRACTS) {
            if (updated[key] !== 'done') updated[key] = 'done';
          }
          return updated;
        });
        setAllDone(true);
        setGenerating(false);
      })
      .catch(() => {
        addLog('✗ Contract generation failed');
        setGenerating(false);
      });
  }, [projectId, token, addLog]);

  /* Derived values */
  const contextWindow = MODEL_CONTEXT_WINDOWS[model] ?? DEFAULT_CONTEXT_WINDOW;
  const totalTokens = cumulativeTokens.input_tokens + cumulativeTokens.output_tokens;
  const ctxPercent = Math.min(100, (totalTokens / contextWindow) * 100);
  const doneCount = Object.values(statuses).filter((s) => s === 'done').length;

  /* Color for context bar */
  const ctxColor = ctxPercent > 80 ? '#EF4444' : ctxPercent > 50 ? '#F59E0B' : '#22C55E';

  return (
    <div style={panelStyle} data-testid="contract-progress">
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <h4 style={{ margin: 0, fontSize: '0.9rem', color: '#F8FAFC' }}>
          {allDone ? '✓ Contracts Ready' : `Generating Contracts… (${doneCount}/${ALL_CONTRACTS.length})`}
        </h4>
      </div>

      {/* Context window meter */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.7rem', color: '#94A3B8' }}>
          <span>Context Window ({model})</span>
          <span>
            {totalTokens.toLocaleString()} / {contextWindow.toLocaleString()} tokens ({ctxPercent.toFixed(1)}%)
          </span>
        </div>
        <div style={meterBarOuter}>
          <div
            style={{
              width: `${ctxPercent}%`,
              height: '100%',
              background: ctxColor,
              borderRadius: '4px',
              transition: 'width 0.4s ease',
            }}
          />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.65rem', color: '#64748B' }}>
          <span>Input: {cumulativeTokens.input_tokens.toLocaleString()}</span>
          <span>Output: {cumulativeTokens.output_tokens.toLocaleString()}</span>
        </div>
      </div>

      {/* Step list */}
      <div>
        {ALL_CONTRACTS.map((ct) => {
          const st = statuses[ct];
          const icon = st === 'done' ? '✅' : st === 'generating' ? '⏳' : '○';
          const color = st === 'done' ? '#22C55E' : st === 'generating' ? '#F59E0B' : '#475569';
          return (
            <div key={ct} style={stepRowStyle}>
              <span style={{ width: '20px', textAlign: 'center' }}>{icon}</span>
              <span style={{ flex: 1, color }}>{CONTRACT_LABELS[ct]}</span>
              <span style={{ fontSize: '0.7rem', color: '#64748B', textTransform: 'uppercase' }}>{st}</span>
            </div>
          );
        })}
      </div>

      {/* Log panel */}
      <div style={logPanelStyle} data-testid="contract-log">
        {log.map((entry, i) => (
          <div key={i}>
            <span style={{ color: '#475569' }}>{entry.time}</span>{' '}
            {entry.message}
          </div>
        ))}
        <div ref={logEndRef} />
      </div>

      {/* Done button */}
      {allDone && (
        <button
          onClick={onComplete}
          data-testid="contracts-done-btn"
          style={{
            background: '#16A34A',
            color: '#fff',
            border: 'none',
            borderRadius: '8px',
            padding: '10px 20px',
            cursor: 'pointer',
            fontSize: '0.8rem',
            fontWeight: 600,
            alignSelf: 'center',
          }}
        >
          Done — View Contracts
        </button>
      )}

      {generating && !allDone && (
        <p style={{ textAlign: 'center', color: '#64748B', fontSize: '0.75rem', margin: 0 }}>
          Generating…
        </p>
      )}
    </div>
  );
}
