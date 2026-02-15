import { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import Skeleton from '../components/Skeleton';

interface BuildCostEntry {
  phase: string;
  input_tokens: number;
  output_tokens: number;
  model: string;
  estimated_cost_usd: number;
}

interface BuildSummary {
  build: {
    id: string;
    project_id: string;
    phase: string;
    status: string;
    loop_count: number;
    started_at: string | null;
    completed_at: string | null;
    error_detail: string | null;
    created_at: string;
  };
  cost: {
    total_input_tokens: number;
    total_output_tokens: number;
    total_cost_usd: number;
    phases: BuildCostEntry[];
  };
  elapsed_seconds: number | null;
  loop_count: number;
}

interface DeployInstructions {
  project_name: string;
  instructions: string;
}

const API_BASE = import.meta.env.VITE_API_URL ?? '';

export default function BuildComplete() {
  const { projectId } = useParams<{ projectId: string }>();
  const { token } = useAuth();
  const navigate = useNavigate();

  const [summary, setSummary] = useState<BuildSummary | null>(null);
  const [instructions, setInstructions] = useState<DeployInstructions | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const headers = useCallback(() => ({
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  }), [token]);

  useEffect(() => {
    if (!token || !projectId) return;
    const load = async () => {
      try {
        const [sumRes, instrRes] = await Promise.all([
          fetch(`${API_BASE}/projects/${projectId}/build/summary`, { headers: headers() }),
          fetch(`${API_BASE}/projects/${projectId}/build/instructions`, { headers: headers() }),
        ]);
        if (sumRes.ok) setSummary(await sumRes.json());
        if (instrRes.ok) setInstructions(await instrRes.json());
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load');
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [token, projectId, headers]);

  const formatDuration = (seconds: number): string => {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) return `${h}h ${m}m ${s}s`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  };

  const formatTokens = (n: number): string => {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    return String(n);
  };

  if (loading) {
    return (
      <div style={{ padding: 32 }} data-testid="build-complete-skeleton">
        <Skeleton width="40%" height={32} />
        <Skeleton width="100%" height={120} />
        <Skeleton width="100%" height={200} />
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: 32, color: '#EF4444' }} data-testid="build-complete-error">
        <h2>Error</h2>
        <p>{error}</p>
      </div>
    );
  }

  const buildStatus = summary?.build.status ?? 'unknown';
  const isSuccess = buildStatus === 'completed';
  const isFailed = buildStatus === 'failed';

  return (
    <div style={{ padding: 32, maxWidth: 900, margin: '0 auto' }} data-testid="build-complete">
      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ color: '#F8FAFC', margin: 0, fontSize: 24 }}>
          {isSuccess ? 'Build Complete' : isFailed ? 'Build Failed' : 'Build Summary'}
        </h1>
        <p style={{ color: '#94A3B8', margin: '4px 0 0' }}>
          {instructions?.project_name ?? `Project ${projectId}`}
        </p>
      </div>

      {/* Status Banner */}
      <div
        data-testid="build-status-banner"
        style={{
          padding: 16,
          borderRadius: 8,
          marginBottom: 24,
          background: isSuccess ? '#166534' : isFailed ? '#7F1D1D' : '#1E3A5F',
          border: `1px solid ${isSuccess ? '#22C55E' : isFailed ? '#EF4444' : '#3B82F6'}`,
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ color: '#F8FAFC', fontWeight: 600 }}>
            {isSuccess ? 'All phases passed' : isFailed ? summary?.build.error_detail ?? 'Build failed' : `Status: ${buildStatus}`}
          </span>
          <span style={{
            padding: '2px 10px',
            borderRadius: 12,
            fontSize: 13,
            fontWeight: 600,
            background: isSuccess ? '#22C55E' : isFailed ? '#EF4444' : '#3B82F6',
            color: '#FFF',
          }}>
            {buildStatus.toUpperCase()}
          </span>
        </div>
      </div>

      {/* Build Summary Cards */}
      {summary && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 16, marginBottom: 24 }}>
          <div style={{ background: '#1E293B', borderRadius: 8, padding: 16 }} data-testid="summary-phase">
            <div style={{ color: '#94A3B8', fontSize: 13 }}>Final Phase</div>
            <div style={{ color: '#F8FAFC', fontSize: 20, fontWeight: 600 }}>{summary.build.phase}</div>
          </div>
          <div style={{ background: '#1E293B', borderRadius: 8, padding: 16 }} data-testid="summary-time">
            <div style={{ color: '#94A3B8', fontSize: 13 }}>Total Time</div>
            <div style={{ color: '#F8FAFC', fontSize: 20, fontWeight: 600 }}>
              {summary.elapsed_seconds != null ? formatDuration(summary.elapsed_seconds) : '—'}
            </div>
          </div>
          <div style={{ background: '#1E293B', borderRadius: 8, padding: 16 }} data-testid="summary-loops">
            <div style={{ color: '#94A3B8', fontSize: 13 }}>Loopbacks</div>
            <div style={{ color: '#F8FAFC', fontSize: 20, fontWeight: 600 }}>{summary.loop_count}</div>
          </div>
          <div style={{ background: '#1E293B', borderRadius: 8, padding: 16 }} data-testid="summary-cost">
            <div style={{ color: '#94A3B8', fontSize: 13 }}>Estimated Cost</div>
            <div style={{ color: '#F8FAFC', fontSize: 20, fontWeight: 600 }}>
              ${summary.cost.total_cost_usd.toFixed(2)}
            </div>
          </div>
        </div>
      )}

      {/* Token Usage */}
      {summary && summary.cost.phases.length > 0 && (
        <div style={{ background: '#1E293B', borderRadius: 8, padding: 20, marginBottom: 24 }} data-testid="cost-breakdown">
          <h3 style={{ color: '#F8FAFC', margin: '0 0 12px' }}>Token Usage by Phase</h3>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid #334155', color: '#94A3B8', fontSize: 13 }}>
            <span style={{ flex: 2 }}>Phase</span>
            <span style={{ flex: 1, textAlign: 'right' }}>Input</span>
            <span style={{ flex: 1, textAlign: 'right' }}>Output</span>
            <span style={{ flex: 1, textAlign: 'right' }}>Cost</span>
          </div>
          {summary.cost.phases.map((entry, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid #1E293B', color: '#F8FAFC', fontSize: 14 }}>
              <span style={{ flex: 2 }}>{entry.phase}</span>
              <span style={{ flex: 1, textAlign: 'right', color: '#94A3B8' }}>{formatTokens(entry.input_tokens)}</span>
              <span style={{ flex: 1, textAlign: 'right', color: '#94A3B8' }}>{formatTokens(entry.output_tokens)}</span>
              <span style={{ flex: 1, textAlign: 'right' }}>${entry.estimated_cost_usd.toFixed(4)}</span>
            </div>
          ))}
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 0 0', color: '#F8FAFC', fontWeight: 600, fontSize: 14 }}>
            <span style={{ flex: 2 }}>Total</span>
            <span style={{ flex: 1, textAlign: 'right' }}>{formatTokens(summary.cost.total_input_tokens)}</span>
            <span style={{ flex: 1, textAlign: 'right' }}>{formatTokens(summary.cost.total_output_tokens)}</span>
            <span style={{ flex: 1, textAlign: 'right' }}>${summary.cost.total_cost_usd.toFixed(4)}</span>
          </div>
        </div>
      )}

      {/* Deployment Instructions */}
      {instructions && (
        <div style={{ background: '#1E293B', borderRadius: 8, padding: 20, marginBottom: 24 }} data-testid="deploy-instructions">
          <h3 style={{ color: '#F8FAFC', margin: '0 0 12px' }}>Deployment Instructions</h3>
          <pre style={{
            background: '#0F172A',
            padding: 16,
            borderRadius: 6,
            color: '#E2E8F0',
            fontSize: 13,
            lineHeight: 1.6,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            maxHeight: 400,
            overflowY: 'auto',
            margin: 0,
          }}>
            {instructions.instructions}
          </pre>
        </div>
      )}

      {/* Actions */}
      <div style={{ display: 'flex', gap: 12 }}>
        <button
          onClick={() => navigate(`/projects/${projectId}`)}
          style={{
            padding: '10px 20px',
            borderRadius: 6,
            border: '1px solid #334155',
            background: '#1E293B',
            color: '#F8FAFC',
            cursor: 'pointer',
            fontWeight: 500,
          }}
        >
          Back to Project
        </button>
        <button
          onClick={() => navigate(`/projects/${projectId}/build`)}
          style={{
            padding: '10px 20px',
            borderRadius: 6,
            border: 'none',
            background: '#2563EB',
            color: '#FFF',
            cursor: 'pointer',
            fontWeight: 500,
          }}
        >
          View Build Logs
        </button>
      </div>
    </div>
  );
}
