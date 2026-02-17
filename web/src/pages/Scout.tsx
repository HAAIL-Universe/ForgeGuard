import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import { useWebSocket } from '../hooks/useWebSocket';
import AppShell from '../components/AppShell';
import HealthBadge from '../components/HealthBadge';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

/* ---------- Types ---------- */

interface Repo {
  id: string;
  full_name: string;
  health_score: string;
}

interface ScoutCheck {
  code: string;
  name: string;
  result: 'PASS' | 'FAIL' | 'WARN';
  detail: string;
}

interface ScoutRun {
  id: string;
  repo_id: string;
  repo_name: string;
  status: string;
  scan_type?: 'quick' | 'deep';
  hypothesis: string | null;
  checks_passed: number;
  checks_failed: number;
  checks_warned: number;
  started_at: string;
  completed_at: string | null;
  checks?: ScoutCheck[];
  warnings?: ScoutCheck[];
}

interface StackProfile {
  languages: Record<string, number>;
  primary_language: string | null;
  backend: { framework: string | null; runtime: string; orm: string | null; db: string | null } | null;
  frontend: { framework: string | null; bundler: string | null; language: string; ui_library: string | null } | null;
  infrastructure: { containerized: boolean; ci_cd: string | null; hosting: string | null };
  testing: { backend_framework: string | null; frontend_framework: string | null; has_tests: boolean };
  project_type: string;
  manifest_files: string[];
}

interface Dossier {
  executive_summary?: string;
  intent?: string;
  quality_assessment?: { score: number; strengths: string[]; weaknesses: string[] };
  risk_areas?: { area: string; severity: string; detail: string }[];
  recommendations?: { priority: string; suggestion: string }[];
}

interface DeepScanResult {
  metadata?: Record<string, any>;
  stack_profile?: StackProfile | null;
  architecture?: Record<string, any> | null;
  dossier?: Dossier | null;
  checks?: ScoutCheck[];
  warnings?: ScoutCheck[];
  files_analysed?: number;
  tree_size?: number;
  head_sha?: string;
}

/* All governance checks in display order */
const ALL_CHECKS = [
  { code: 'A1', name: 'Scope compliance' },
  { code: 'A2', name: 'Minimal diff' },
  { code: 'A3', name: 'Evidence completeness' },
  { code: 'A4', name: 'Boundary compliance' },
  { code: 'A5', name: 'Diff log gate' },
  { code: 'A6', name: 'Authorization gate' },
  { code: 'A7', name: 'Verification order' },
  { code: 'A8', name: 'Test gate' },
  { code: 'A9', name: 'Dependency gate' },
  { code: 'W1', name: 'Secrets in diff' },
  { code: 'W2', name: 'Audit ledger integrity' },
  { code: 'W3', name: 'Physics route coverage' },
];

type View = 'repos' | 'running' | 'results' | 'deep_running' | 'dossier' | 'upgrade_plan';

function Scout() {
  const { token } = useAuth();
  const { addToast } = useToast();

  /* State */
  const [repos, setRepos] = useState<Repo[]>([]);
  const [reposLoading, setReposLoading] = useState(true);
  const [history, setHistory] = useState<ScoutRun[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [filter, setFilter] = useState('');
  const [view, setView] = useState<View>('repos');
  const [activeRepo, setActiveRepo] = useState<Repo | null>(null);
  const [activeRun, setActiveRun] = useState<ScoutRun | null>(null);
  const [hypothesis, setHypothesis] = useState('');
  const [showHypothesis, setShowHypothesis] = useState<string | null>(null);

  /* Live check results during a run */
  const [liveChecks, setLiveChecks] = useState<Record<string, 'PASS' | 'FAIL' | 'WARN' | 'running' | 'pending'>>({});
  const [liveDetails, setLiveDetails] = useState<Record<string, string>>({});
  const [expandedCheck, setExpandedCheck] = useState<string | null>(null);
  const [expandedHistoryRun, setExpandedHistoryRun] = useState<string | null>(null);

  /* Deep scan state */
  const [deepSteps, setDeepSteps] = useState<Record<string, string>>({});
  const [deepScanResult, setDeepScanResult] = useState<DeepScanResult | null>(null);
  const [deepScanning, setDeepScanning] = useState(false);

  /* Upgrade plan state */
  const [upgradePlan, setUpgradePlan] = useState<Record<string, any> | null>(null);
  const [upgradePlanLoading, setUpgradePlanLoading] = useState(false);

  /* Fetch repos */
  const fetchRepos = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/repos`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setRepos(data.items);
      }
    } catch { /* best effort */ }
    finally { setReposLoading(false); }
  }, [token]);

  /* Fetch scout history */
  const fetchHistory = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/scout/history`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setHistory(data.runs ?? []);
      }
    } catch { /* best effort */ }
    finally { setHistoryLoading(false); }
  }, [token]);

  useEffect(() => {
    fetchRepos();
    fetchHistory();
  }, [fetchRepos, fetchHistory]);

  /* WS handler for scout events */
  useWebSocket(
    useCallback(
      (data: { type: string; payload: any }) => {
        if (data.type === 'scout_progress') {
          const p = data.payload;
          if (p.step) {
            // Deep scan step-by-step progress
            setDeepSteps((prev) => ({ ...prev, [p.step]: p.detail || 'Done' }));
          } else {
            // Quick scan check-by-check
            setLiveChecks((prev) => ({ ...prev, [p.check_code]: p.result }));
            if (p.detail) {
              setLiveDetails((prev) => ({ ...prev, [p.check_code]: p.detail }));
            }
          }
        } else if (data.type === 'scout_complete') {
          const p = data.payload;
          setActiveRun(p);
          if (p.scan_type === 'deep') {
            setDeepScanning(false);
            // Fetch dossier
            fetch(`${API_BASE}/scout/runs/${p.id}/dossier`, {
              headers: { Authorization: `Bearer ${token}` },
            })
              .then((res) => (res.ok ? res.json() : null))
              .then((data) => {
                if (data) setDeepScanResult(data);
                setView('dossier');
              })
              .catch(() => setView('dossier'));
          } else {
            setView('results');
          }
          fetchHistory();
        }
      },
      [fetchHistory, token],
    ),
  );

  /* Trigger a scout run */
  const handleScout = async (repo: Repo) => {
    setActiveRepo(repo);
    setView('running');
    setExpandedCheck(null);

    /* Initialise all checks to pending */
    const initial: Record<string, 'pending'> = {};
    ALL_CHECKS.forEach((c) => { initial[c.code] = 'pending'; });
    setLiveChecks(initial);
    setLiveDetails({});

    try {
      const res = await fetch(`${API_BASE}/scout/${repo.id}/run`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ hypothesis: hypothesis.trim() || null }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Scout run failed' }));
        addToast(err.detail ?? 'Scout run failed');
        setView('repos');
      }
    } catch {
      addToast('Network error starting Scout run');
      setView('repos');
    }
  };

  /* Trigger a deep scan */
  const handleDeepScan = async (repo: Repo) => {
    setActiveRepo(repo);
    setView('deep_running');
    setDeepSteps({});
    setDeepScanResult(null);
    setDeepScanning(true);

    try {
      const res = await fetch(`${API_BASE}/scout/${repo.id}/deep-scan`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          hypothesis: hypothesis.trim() || null,
          include_llm: true,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Deep scan failed' }));
        addToast(err.detail ?? 'Deep scan failed');
        setView('repos');
        setDeepScanning(false);
      }
    } catch {
      addToast('Network error starting deep scan');
      setView('repos');
      setDeepScanning(false);
    }
  };

  /* View a past deep scan dossier */
  const handleViewDossier = async (run: ScoutRun) => {
    setActiveRun(run);
    const repo = repos.find((r) => r.id === run.repo_id) ?? null;
    setActiveRepo(repo);
    try {
      const res = await fetch(`${API_BASE}/scout/runs/${run.id}/dossier`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setDeepScanResult(data);
        setView('dossier');
      } else {
        addToast('Could not load dossier');
      }
    } catch {
      addToast('Network error loading dossier');
    }
  };

  /* Generate upgrade plan for a deep scan run */
  const handleGenerateUpgradePlan = async (runId: string) => {
    setUpgradePlanLoading(true);
    try {
      const res = await fetch(`${API_BASE}/scout/runs/${runId}/upgrade-plan`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ include_llm: true }),
      });
      if (res.ok) {
        const data = await res.json();
        setUpgradePlan(data);
        setView('upgrade_plan');
      } else {
        const err = await res.json().catch(() => ({ detail: 'Failed to generate upgrade plan' }));
        addToast(err.detail ?? 'Failed to generate upgrade plan');
      }
    } catch {
      addToast('Network error generating upgrade plan');
    } finally {
      setUpgradePlanLoading(false);
    }
  };

  /* View a previously generated upgrade plan */
  const handleViewUpgradePlan = async (runId: string) => {
    setUpgradePlanLoading(true);
    try {
      const res = await fetch(`${API_BASE}/scout/runs/${runId}/upgrade-plan`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setUpgradePlan(data);
        setView('upgrade_plan');
      } else {
        addToast('No upgrade plan found. Generate one first.');
      }
    } catch {
      addToast('Network error loading upgrade plan');
    } finally {
      setUpgradePlanLoading(false);
    }
  };

  /* Load a past run's details */
  const handleExpandHistory = async (run: ScoutRun) => {
    if (expandedHistoryRun === run.id) {
      setExpandedHistoryRun(null);
      return;
    }
    setExpandedHistoryRun(run.id);

    /* Already has checks? */
    if (run.checks) return;

    try {
      const res = await fetch(`${API_BASE}/scout/runs/${run.id}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const detail = await res.json();
        setHistory((prev) =>
          prev.map((r) => (r.id === run.id ? { ...r, checks: detail.checks ?? [], warnings: detail.warnings ?? [] } : r)),
        );
      } else {
        /* Request failed — clear loading state with empty checks */
        setHistory((prev) =>
          prev.map((r) => (r.id === run.id ? { ...r, checks: [], warnings: [] } : r)),
        );
      }
    } catch {
      setHistory((prev) =>
        prev.map((r) => (r.id === run.id ? { ...r, checks: [], warnings: [] } : r)),
      );
    }
  };

  /* View past run as results view */
  const handleViewRun = (run: ScoutRun) => {
    setActiveRun(run);
    const repo = repos.find((r) => r.id === run.repo_id) ?? null;
    setActiveRepo(repo);
    setView('results');

    /* Populate live checks from stored results */
    const checks: Record<string, 'PASS' | 'FAIL' | 'WARN'> = {};
    const details: Record<string, string> = {};
    for (const c of (run.checks ?? [])) {
      checks[c.code] = c.result;
      if (c.detail) details[c.code] = c.detail;
    }
    for (const w of (run.warnings ?? [])) {
      checks[w.code] = w.result;
      if (w.detail) details[w.code] = w.detail;
    }
    setLiveChecks(checks);
    setLiveDetails(details);
  };

  /* Filtered repos */
  const filteredRepos = filter
    ? repos.filter((r) => r.full_name.toLowerCase().includes(filter.toLowerCase()))
    : repos;

  /* Result counts from active run */
  const passCount = activeRun?.checks_passed ?? Object.values(liveChecks).filter((v) => v === 'PASS').length;
  const failCount = activeRun?.checks_failed ?? Object.values(liveChecks).filter((v) => v === 'FAIL').length;
  const warnCount = activeRun?.checks_warned ?? Object.values(liveChecks).filter((v) => v === 'WARN').length;

  /* Status icon */
  const checkIcon = (status: string) => {
    switch (status) {
      case 'PASS': return '✅';
      case 'FAIL': return '❌';
      case 'WARN': return '⚠️';
      case 'running': return '⏳';
      default: return '⬜';
    }
  };

  const relativeTime = (iso: string) => {
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    return `${days}d ago`;
  };

  return (
    <AppShell>
      <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '24px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            {view !== 'repos' && (
              <button
                onClick={() => { setView('repos'); setActiveRepo(null); setActiveRun(null); setHypothesis(''); setShowHypothesis(null); }}
                style={{
                  background: 'transparent',
                  border: '1px solid #334155',
                  color: '#94A3B8',
                  borderRadius: '6px',
                  padding: '4px 10px',
                  cursor: 'pointer',
                  fontSize: '0.8rem',
                }}
              >
                ← Back
              </button>
            )}
            <h2 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 700 }}>
              {view === 'repos' && 'Scout'}
              {view === 'running' && `Scout → ${activeRepo?.full_name ?? ''}`}
              {view === 'results' && `Scout → ${activeRepo?.full_name ?? ''}`}
              {view === 'dossier' && `Dossier → ${activeRepo?.full_name ?? ''}`}
              {view === 'upgrade_plan' && `Upgrade Plan → ${activeRepo?.full_name ?? ''}`}
            </h2>
            {view === 'results' && (
              <div style={{ display: 'flex', gap: '8px', fontSize: '0.75rem' }}>
                <span style={{ color: '#22C55E' }}>{passCount} pass</span>
                <span style={{ color: '#EF4444' }}>{failCount} fail</span>
                {warnCount > 0 && <span style={{ color: '#F59E0B' }}>{warnCount} warn</span>}
              </div>
            )}
          </div>
        </div>

        {/* ─── REPOS VIEW ─── */}
        {view === 'repos' && (
          <>
            {/* Filter */}
            <input
              type="text"
              placeholder="Filter repos…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              data-testid="scout-repo-filter"
              style={{
                width: '100%',
                padding: '8px 12px',
                marginBottom: '16px',
                background: '#1E293B',
                border: '1px solid #334155',
                borderRadius: '6px',
                color: '#F8FAFC',
                fontSize: '0.85rem',
                outline: 'none',
                boxSizing: 'border-box',
              }}
            />

            {/* Repo list */}
            <h3 style={{ fontSize: '0.75rem', color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '8px' }}>
              Connected Repos
            </h3>
            {reposLoading ? (
              <div style={{ color: '#64748B', fontSize: '0.8rem', padding: '12px 0' }}>Loading…</div>
            ) : filteredRepos.length === 0 ? (
              <div style={{ color: '#64748B', fontSize: '0.8rem', padding: '12px 0' }}>
                {repos.length === 0 ? 'No repos connected. Connect a repo from the dashboard.' : 'No repos match filter.'}
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', marginBottom: '32px' }}>
                {filteredRepos.map((repo) => (
                  <div
                    key={repo.id}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '10px 14px',
                      background: '#1E293B',
                      borderRadius: '8px',
                      border: '1px solid #334155',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0, flex: 1 }}>
                      <HealthBadge score={repo.health_score} size={10} />
                      <span style={{ fontSize: '0.85rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {repo.full_name}
                      </span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
                      {/* Hypothesis toggle */}
                      <button
                        onClick={() => setShowHypothesis(showHypothesis === repo.id ? null : repo.id)}
                        title="Add hypothesis"
                        style={{
                          background: 'transparent',
                          border: '1px solid #334155',
                          color: showHypothesis === repo.id ? '#2563EB' : '#64748B',
                          borderRadius: '6px',
                          padding: '4px 8px',
                          cursor: 'pointer',
                          fontSize: '0.7rem',
                        }}
                      >
                        💡
                      </button>
                      <button
                        onClick={() => handleScout(repo)}
                        data-testid={`scout-repo-${repo.id}`}
                        style={{
                          background: '#2563EB',
                          color: '#F8FAFC',
                          border: 'none',
                          borderRadius: '6px',
                          padding: '6px 14px',
                          cursor: 'pointer',
                          fontSize: '0.75rem',
                          fontWeight: 600,
                          whiteSpace: 'nowrap',
                        }}
                      >
                        Quick Scan
                      </button>
                      <button
                        onClick={() => handleDeepScan(repo)}
                        data-testid={`deep-scan-${repo.id}`}
                        style={{
                          background: '#7C3AED',
                          color: '#F8FAFC',
                          border: 'none',
                          borderRadius: '6px',
                          padding: '6px 14px',
                          cursor: 'pointer',
                          fontSize: '0.75rem',
                          fontWeight: 600,
                          whiteSpace: 'nowrap',
                        }}
                      >
                        Deep Scan 🔬
                      </button>
                    </div>
                    {/* Hypothesis input row */}
                    {showHypothesis === repo.id && (
                      <div style={{ width: '100%', marginTop: '8px' }}>
                        <input
                          type="text"
                          placeholder="I think the auth middleware might be leaking tokens…"
                          value={hypothesis}
                          onChange={(e) => setHypothesis(e.target.value)}
                          data-testid="scout-hypothesis"
                          style={{
                            width: '100%',
                            padding: '6px 10px',
                            background: '#0F172A',
                            border: '1px solid #334155',
                            borderRadius: '6px',
                            color: '#F8FAFC',
                            fontSize: '0.8rem',
                            outline: 'none',
                            boxSizing: 'border-box',
                          }}
                        />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* Recent scout runs */}
            <h3 style={{ fontSize: '0.75rem', color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '8px' }}>
              Recent Scout Runs
            </h3>
            {historyLoading ? (
              <div style={{ color: '#64748B', fontSize: '0.8rem', padding: '12px 0' }}>Loading…</div>
            ) : history.length === 0 ? (
              <div style={{ color: '#64748B', fontSize: '0.8rem', padding: '12px 0' }}>No previous Scout runs.</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                {history.map((run) => (
                  <div key={run.id}>
                    <div
                      onClick={() => handleExpandHistory(run)}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: '8px 14px',
                        background: '#1E293B',
                        borderRadius: expandedHistoryRun === run.id ? '8px 8px 0 0' : '8px',
                        border: '1px solid #334155',
                        cursor: 'pointer',
                        fontSize: '0.8rem',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0, flex: 1 }}>
                        <span style={{
                          width: '8px', height: '8px', borderRadius: '50%', flexShrink: 0,
                          background: run.checks_failed > 0 ? '#EF4444' : run.checks_warned > 0 ? '#F59E0B' : '#22C55E',
                        }} />
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {run.repo_name}
                        </span>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexShrink: 0, color: '#64748B', fontSize: '0.7rem' }}>
                        {run.scan_type === 'deep' && (
                          <span style={{ color: '#7C3AED', fontWeight: 600 }}>DEEP</span>
                        )}
                        <span>{relativeTime(run.started_at)}</span>
                        <span style={{ color: '#22C55E' }}>{run.checks_passed}✓</span>
                        {run.checks_failed > 0 && <span style={{ color: '#EF4444' }}>{run.checks_failed}✗</span>}
                        {run.checks_warned > 0 && <span style={{ color: '#F59E0B' }}>{run.checks_warned}!</span>}
                        <span>{expandedHistoryRun === run.id ? '▾' : '▸'}</span>
                      </div>
                    </div>
                    {/* Expanded detail */}
                    {expandedHistoryRun === run.id && (
                      <div
                        style={{
                          padding: '10px 14px',
                          background: '#0F172A',
                          borderRadius: '0 0 8px 8px',
                          border: '1px solid #334155',
                          borderTop: 'none',
                        }}
                      >
                        {run.checks !== undefined ? (
                          <>
                            {[...(run.checks ?? []), ...(run.warnings ?? [])].length > 0 ? (
                              [...(run.checks ?? []), ...(run.warnings ?? [])].map((c) => (
                                <div key={c.code} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '3px 0', fontSize: '0.75rem' }}>
                                  <span>{checkIcon(c.result)}</span>
                                  <span style={{ color: '#94A3B8' }}>{c.code}</span>
                                  <span>{c.name}</span>
                                  <span style={{ color: '#64748B', marginLeft: 'auto', maxWidth: '40%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {c.detail}
                                  </span>
                                </div>
                              ))
                            ) : (
                              <div style={{ color: '#64748B', fontSize: '0.75rem' }}>
                                {run.status === 'running' ? 'Run still in progress…' : 'No results available.'}
                              </div>
                            )}
                            {[...(run.checks ?? []), ...(run.warnings ?? [])].length > 0 && (
                              <div style={{ display: 'flex', gap: '8px', marginTop: '8px' }}>
                                <button
                                  onClick={() => handleViewRun(run)}
                                  style={{
                                    background: '#334155',
                                    color: '#F8FAFC',
                                    border: 'none',
                                    borderRadius: '6px',
                                    padding: '6px 14px',
                                    cursor: 'pointer',
                                    fontSize: '0.7rem',
                                    fontWeight: 500,
                                  }}
                                >
                                  View Full Results
                                </button>
                                {run.scan_type === 'deep' && (
                                  <button
                                    onClick={() => handleViewDossier(run)}
                                    style={{
                                      background: '#7C3AED',
                                      color: '#F8FAFC',
                                      border: 'none',
                                      borderRadius: '6px',
                                      padding: '6px 14px',
                                      cursor: 'pointer',
                                      fontSize: '0.7rem',
                                      fontWeight: 500,
                                    }}
                                  >
                                    View Dossier 🔬
                                  </button>
                                )}
                                {run.scan_type === 'deep' && run.status === 'completed' && (
                                  <button
                                    onClick={() => {
                                      setActiveRun(run);
                                      const repo = repos.find((r) => r.id === run.repo_id) ?? null;
                                      setActiveRepo(repo);
                                      handleViewUpgradePlan(run.id);
                                    }}
                                    style={{
                                      background: '#059669',
                                      color: '#F8FAFC',
                                      border: 'none',
                                      borderRadius: '6px',
                                      padding: '6px 14px',
                                      cursor: 'pointer',
                                      fontSize: '0.7rem',
                                      fontWeight: 500,
                                    }}
                                  >
                                    📋 Upgrade Plan
                                  </button>
                                )}
                              </div>
                            )}
                          </>
                        ) : (
                          <div style={{ color: '#64748B', fontSize: '0.75rem' }}>Loading…</div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {/* ─── RUNNING VIEW ─── */}
        {(view === 'running' || view === 'deep_running') && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
              <span style={{ fontSize: '0.8rem', color: '#94A3B8' }}>
                {view === 'deep_running' ? 'Deep scan in progress…' : 'Running checks…'}
              </span>
            </div>
            {view === 'deep_running' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '12px' }}>
                {['metadata', 'tree', 'stack', 'fetching', 'architecture', 'audit', 'dossier'].map((step) => {
                  const label: Record<string, string> = {
                    metadata: 'Fetching repo metadata',
                    tree: 'Fetching file tree',
                    stack: 'Detecting technology stack',
                    fetching: 'Fetching key files',
                    architecture: 'Mapping architecture',
                    audit: 'Running compliance checks',
                    dossier: 'Generating project dossier',
                  };
                  const done = step in deepSteps;
                  return (
                    <div
                      key={step}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '10px',
                        padding: '8px 14px',
                        background: '#1E293B',
                        borderRadius: '6px',
                        border: '1px solid #334155',
                        fontSize: '0.85rem',
                      }}
                    >
                      <span>{done ? '✅' : deepScanning ? '⏳' : '⬜'}</span>
                      <span>{label[step]}</span>
                      {done && deepSteps[step] && (
                        <span style={{ marginLeft: 'auto', color: '#64748B', fontSize: '0.7rem' }}>
                          {deepSteps[step]}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
            {view === 'running' && ALL_CHECKS.map((check) => {
              const status = liveChecks[check.code] ?? 'pending';
              return (
                <div
                  key={check.code}
                  onClick={() => liveDetails[check.code] && setExpandedCheck(expandedCheck === check.code ? null : check.code)}
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    padding: '8px 14px',
                    background: '#1E293B',
                    borderRadius: '6px',
                    border: '1px solid #334155',
                    cursor: liveDetails[check.code] ? 'pointer' : 'default',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '0.85rem' }}>
                    <span>{checkIcon(status)}</span>
                    <span style={{ color: '#94A3B8', fontSize: '0.7rem', fontFamily: 'monospace', width: '24px' }}>{check.code}</span>
                    <span>{check.name}</span>
                    <span style={{
                      marginLeft: 'auto', fontSize: '0.7rem', fontWeight: 500,
                      color: status === 'PASS' ? '#22C55E' : status === 'FAIL' ? '#EF4444' : status === 'WARN' ? '#F59E0B' : '#64748B',
                    }}>
                      {status === 'pending' ? 'Pending' : status === 'running' ? 'Running…' : status}
                    </span>
                  </div>
                  {expandedCheck === check.code && liveDetails[check.code] && (
                    <div style={{ marginTop: '6px', paddingLeft: '42px', fontSize: '0.75rem', color: '#94A3B8', whiteSpace: 'pre-wrap' }}>
                      {liveDetails[check.code]}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* ─── DOSSIER VIEW ─── */}
        {view === 'dossier' && deepScanResult && (
          <div>
            {/* Stack Profile */}
            {deepScanResult.stack_profile && (
              <div style={{ marginBottom: '24px' }}>
                <h3 style={{ fontSize: '0.85rem', color: '#94A3B8', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Technology Stack
                </h3>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '10px' }}>
                  {/* Languages */}
                  <div style={{ background: '#1E293B', borderRadius: '8px', padding: '12px', border: '1px solid #334155' }}>
                    <div style={{ fontSize: '0.7rem', color: '#64748B', marginBottom: '6px', textTransform: 'uppercase' }}>Languages</div>
                    {Object.entries(deepScanResult.stack_profile.languages).map(([lang, pct]) => (
                      <div key={lang} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', padding: '2px 0' }}>
                        <span>{lang}</span>
                        <span style={{ color: '#64748B' }}>{pct}%</span>
                      </div>
                    ))}
                  </div>
                  {/* Backend */}
                  {deepScanResult.stack_profile.backend && (
                    <div style={{ background: '#1E293B', borderRadius: '8px', padding: '12px', border: '1px solid #334155' }}>
                      <div style={{ fontSize: '0.7rem', color: '#64748B', marginBottom: '6px', textTransform: 'uppercase' }}>Backend</div>
                      <div style={{ fontSize: '0.8rem' }}>{deepScanResult.stack_profile.backend.framework ?? 'No framework'}</div>
                      <div style={{ fontSize: '0.7rem', color: '#94A3B8' }}>{deepScanResult.stack_profile.backend.runtime}</div>
                      {deepScanResult.stack_profile.backend.db && (
                        <div style={{ fontSize: '0.7rem', color: '#94A3B8' }}>DB: {deepScanResult.stack_profile.backend.db}</div>
                      )}
                      {deepScanResult.stack_profile.backend.orm && (
                        <div style={{ fontSize: '0.7rem', color: '#94A3B8' }}>ORM: {deepScanResult.stack_profile.backend.orm}</div>
                      )}
                    </div>
                  )}
                  {/* Frontend */}
                  {deepScanResult.stack_profile.frontend && (
                    <div style={{ background: '#1E293B', borderRadius: '8px', padding: '12px', border: '1px solid #334155' }}>
                      <div style={{ fontSize: '0.7rem', color: '#64748B', marginBottom: '6px', textTransform: 'uppercase' }}>Frontend</div>
                      <div style={{ fontSize: '0.8rem' }}>{deepScanResult.stack_profile.frontend.framework ?? 'Unknown'}</div>
                      <div style={{ fontSize: '0.7rem', color: '#94A3B8' }}>{deepScanResult.stack_profile.frontend.language}</div>
                      {deepScanResult.stack_profile.frontend.bundler && (
                        <div style={{ fontSize: '0.7rem', color: '#94A3B8' }}>Bundler: {deepScanResult.stack_profile.frontend.bundler}</div>
                      )}
                      {deepScanResult.stack_profile.frontend.ui_library && (
                        <div style={{ fontSize: '0.7rem', color: '#94A3B8' }}>UI: {deepScanResult.stack_profile.frontend.ui_library}</div>
                      )}
                    </div>
                  )}
                  {/* Infrastructure */}
                  <div style={{ background: '#1E293B', borderRadius: '8px', padding: '12px', border: '1px solid #334155' }}>
                    <div style={{ fontSize: '0.7rem', color: '#64748B', marginBottom: '6px', textTransform: 'uppercase' }}>Infrastructure</div>
                    <div style={{ fontSize: '0.8rem' }}>
                      {deepScanResult.stack_profile.infrastructure.containerized ? '🐳 Containerized' : 'No container'}
                    </div>
                    {deepScanResult.stack_profile.infrastructure.ci_cd && (
                      <div style={{ fontSize: '0.7rem', color: '#94A3B8' }}>CI/CD: {deepScanResult.stack_profile.infrastructure.ci_cd}</div>
                    )}
                    {deepScanResult.stack_profile.infrastructure.hosting && (
                      <div style={{ fontSize: '0.7rem', color: '#94A3B8' }}>Hosting: {deepScanResult.stack_profile.infrastructure.hosting}</div>
                    )}
                  </div>
                  {/* Testing */}
                  <div style={{ background: '#1E293B', borderRadius: '8px', padding: '12px', border: '1px solid #334155' }}>
                    <div style={{ fontSize: '0.7rem', color: '#64748B', marginBottom: '6px', textTransform: 'uppercase' }}>Testing</div>
                    <div style={{ fontSize: '0.8rem' }}>
                      {deepScanResult.stack_profile.testing.has_tests ? '✅ Tests detected' : '❌ No tests'}
                    </div>
                    {deepScanResult.stack_profile.testing.backend_framework && (
                      <div style={{ fontSize: '0.7rem', color: '#94A3B8' }}>Backend: {deepScanResult.stack_profile.testing.backend_framework}</div>
                    )}
                    {deepScanResult.stack_profile.testing.frontend_framework && (
                      <div style={{ fontSize: '0.7rem', color: '#94A3B8' }}>Frontend: {deepScanResult.stack_profile.testing.frontend_framework}</div>
                    )}
                  </div>
                  {/* Project Type */}
                  <div style={{ background: '#1E293B', borderRadius: '8px', padding: '12px', border: '1px solid #334155' }}>
                    <div style={{ fontSize: '0.7rem', color: '#64748B', marginBottom: '6px', textTransform: 'uppercase' }}>Project Type</div>
                    <div style={{ fontSize: '0.8rem', textTransform: 'capitalize' }}>{deepScanResult.stack_profile.project_type.replace('_', ' ')}</div>
                    <div style={{ fontSize: '0.7rem', color: '#94A3B8', marginTop: '4px' }}>
                      {deepScanResult.tree_size} files · {deepScanResult.files_analysed} analysed
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Architecture */}
            {deepScanResult.architecture && (
              <div style={{ marginBottom: '24px' }}>
                <h3 style={{ fontSize: '0.85rem', color: '#94A3B8', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Architecture
                </h3>
                <div style={{ background: '#1E293B', borderRadius: '8px', padding: '14px', border: '1px solid #334155' }}>
                  <div style={{ fontSize: '0.8rem', marginBottom: '8px' }}>
                    Structure: <span style={{ color: '#2563EB', textTransform: 'capitalize' }}>{deepScanResult.architecture.structure_type}</span>
                  </div>
                  {deepScanResult.architecture.entry_points?.length > 0 && (
                    <div style={{ marginBottom: '8px' }}>
                      <span style={{ fontSize: '0.7rem', color: '#64748B' }}>Entry points: </span>
                      <span style={{ fontSize: '0.75rem', fontFamily: 'monospace' }}>
                        {deepScanResult.architecture.entry_points.join(', ')}
                      </span>
                    </div>
                  )}
                  {deepScanResult.architecture.data_models?.length > 0 && (
                    <div style={{ marginBottom: '8px' }}>
                      <span style={{ fontSize: '0.7rem', color: '#64748B' }}>Data models: </span>
                      <span style={{ fontSize: '0.75rem' }}>{deepScanResult.architecture.data_models.join(', ')}</span>
                    </div>
                  )}
                  {deepScanResult.architecture.external_integrations?.length > 0 && (
                    <div>
                      <span style={{ fontSize: '0.7rem', color: '#64748B' }}>Integrations: </span>
                      <span style={{ fontSize: '0.75rem' }}>{deepScanResult.architecture.external_integrations.join(', ')}</span>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* LLM Dossier */}
            {deepScanResult.dossier && (
              <div style={{ marginBottom: '24px' }}>
                <h3 style={{ fontSize: '0.85rem', color: '#94A3B8', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Project Dossier
                </h3>
                {/* Executive Summary */}
                {deepScanResult.dossier.executive_summary && (
                  <div style={{ background: '#1E293B', borderRadius: '8px', padding: '14px', border: '1px solid #334155', marginBottom: '10px' }}>
                    <div style={{ fontSize: '0.7rem', color: '#64748B', marginBottom: '6px', textTransform: 'uppercase' }}>Executive Summary</div>
                    <div style={{ fontSize: '0.8rem', lineHeight: 1.5 }}>{deepScanResult.dossier.executive_summary}</div>
                  </div>
                )}
                {/* Quality Score */}
                {deepScanResult.dossier.quality_assessment && (
                  <div style={{ background: '#1E293B', borderRadius: '8px', padding: '14px', border: '1px solid #334155', marginBottom: '10px' }}>
                    <div style={{ fontSize: '0.7rem', color: '#64748B', marginBottom: '6px', textTransform: 'uppercase' }}>Quality Assessment</div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '8px' }}>
                      <div style={{
                        width: '48px', height: '48px', borderRadius: '50%', display: 'flex',
                        alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: '1rem',
                        background: deepScanResult.dossier.quality_assessment.score >= 80 ? '#14532D' :
                          deepScanResult.dossier.quality_assessment.score >= 60 ? '#78350F' : '#7F1D1D',
                        color: deepScanResult.dossier.quality_assessment.score >= 80 ? '#22C55E' :
                          deepScanResult.dossier.quality_assessment.score >= 60 ? '#F59E0B' : '#EF4444',
                      }}>
                        {deepScanResult.dossier.quality_assessment.score}
                      </div>
                      <div style={{ fontSize: '0.8rem', color: '#94A3B8' }}>
                        {deepScanResult.dossier.quality_assessment.score >= 80 ? 'Good quality' :
                          deepScanResult.dossier.quality_assessment.score >= 60 ? 'Needs improvement' : 'Significant issues'}
                      </div>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                      <div>
                        <div style={{ fontSize: '0.7rem', color: '#22C55E', marginBottom: '4px' }}>Strengths</div>
                        {deepScanResult.dossier.quality_assessment.strengths.map((s, i) => (
                          <div key={i} style={{ fontSize: '0.75rem', padding: '2px 0' }}>✅ {s}</div>
                        ))}
                      </div>
                      <div>
                        <div style={{ fontSize: '0.7rem', color: '#EF4444', marginBottom: '4px' }}>Weaknesses</div>
                        {deepScanResult.dossier.quality_assessment.weaknesses.map((w, i) => (
                          <div key={i} style={{ fontSize: '0.75rem', padding: '2px 0' }}>⚠️ {w}</div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
                {/* Risk Areas */}
                {deepScanResult.dossier.risk_areas && deepScanResult.dossier.risk_areas.length > 0 && (
                  <div style={{ background: '#1E293B', borderRadius: '8px', padding: '14px', border: '1px solid #334155', marginBottom: '10px' }}>
                    <div style={{ fontSize: '0.7rem', color: '#64748B', marginBottom: '6px', textTransform: 'uppercase' }}>Risk Areas</div>
                    {deepScanResult.dossier.risk_areas.map((r, i) => (
                      <div key={i} style={{ display: 'flex', gap: '8px', padding: '4px 0', fontSize: '0.75rem' }}>
                        <span style={{
                          padding: '1px 6px', borderRadius: '4px', fontSize: '0.65rem', fontWeight: 600,
                          background: r.severity === 'high' ? '#7F1D1D' : r.severity === 'medium' ? '#78350F' : '#1E3A5F',
                          color: r.severity === 'high' ? '#EF4444' : r.severity === 'medium' ? '#F59E0B' : '#3B82F6',
                        }}>
                          {r.severity.toUpperCase()}
                        </span>
                        <span style={{ color: '#94A3B8' }}>{r.area}:</span>
                        <span>{r.detail}</span>
                      </div>
                    ))}
                  </div>
                )}
                {/* Recommendations */}
                {deepScanResult.dossier.recommendations && deepScanResult.dossier.recommendations.length > 0 && (
                  <div style={{ background: '#1E293B', borderRadius: '8px', padding: '14px', border: '1px solid #334155' }}>
                    <div style={{ fontSize: '0.7rem', color: '#64748B', marginBottom: '6px', textTransform: 'uppercase' }}>Recommendations</div>
                    {deepScanResult.dossier.recommendations.map((r, i) => (
                      <div key={i} style={{ display: 'flex', gap: '8px', padding: '4px 0', fontSize: '0.75rem' }}>
                        <span style={{
                          padding: '1px 6px', borderRadius: '4px', fontSize: '0.65rem', fontWeight: 600,
                          background: r.priority === 'high' ? '#14532D' : r.priority === 'medium' ? '#1E3A5F' : '#1E293B',
                          color: r.priority === 'high' ? '#22C55E' : r.priority === 'medium' ? '#3B82F6' : '#94A3B8',
                        }}>
                          {r.priority.toUpperCase()}
                        </span>
                        <span>{r.suggestion}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Action buttons */}
            <div style={{ display: 'flex', gap: '10px', justifyContent: 'center', flexWrap: 'wrap' }}>
              <button
                onClick={() => { setView('repos'); setActiveRepo(null); setActiveRun(null); setDeepScanResult(null); }}
                style={{
                  background: '#334155',
                  color: '#F8FAFC',
                  border: 'none',
                  borderRadius: '6px',
                  padding: '8px 18px',
                  cursor: 'pointer',
                  fontSize: '0.8rem',
                  fontWeight: 500,
                }}
              >
                ← Back to Repos
              </button>
              {activeRun && (
                <button
                  disabled={upgradePlanLoading}
                  onClick={() => handleGenerateUpgradePlan(activeRun.id)}
                  style={{
                    background: '#059669',
                    color: '#F8FAFC',
                    border: 'none',
                    borderRadius: '6px',
                    padding: '8px 18px',
                    cursor: upgradePlanLoading ? 'wait' : 'pointer',
                    fontSize: '0.8rem',
                    fontWeight: 500,
                    opacity: upgradePlanLoading ? 0.6 : 1,
                  }}
                >
                  {upgradePlanLoading ? '⏳ Generating…' : '📋 Generate Upgrade Plan'}
                </button>
              )}
              {activeRepo && (
                <button
                  onClick={() => handleDeepScan(activeRepo)}
                  style={{
                    background: '#7C3AED',
                    color: '#F8FAFC',
                    border: 'none',
                    borderRadius: '6px',
                    padding: '8px 18px',
                    cursor: 'pointer',
                    fontSize: '0.8rem',
                    fontWeight: 500,
                  }}
                >
                  🔬 Re-scan
                </button>
              )}
            </div>
          </div>
        )}

        {/* ─── UPGRADE PLAN VIEW ─── */}
        {view === 'upgrade_plan' && upgradePlan && (
          <div>
            {/* Executive Brief */}
            {upgradePlan.executive_brief && (
              <div style={{ marginBottom: '24px' }}>
                <h3 style={{ fontSize: '0.85rem', color: '#94A3B8', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Executive Brief
                </h3>
                <div style={{ background: '#1E293B', borderRadius: '8px', padding: '14px', border: '1px solid #334155' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '12px' }}>
                    <div style={{
                      width: '52px', height: '52px', borderRadius: '50%', display: 'flex',
                      alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: '1.2rem',
                      background: ['A', 'B'].includes(upgradePlan.executive_brief.health_grade) ? '#14532D' :
                        upgradePlan.executive_brief.health_grade === 'C' ? '#78350F' : '#7F1D1D',
                      color: ['A', 'B'].includes(upgradePlan.executive_brief.health_grade) ? '#22C55E' :
                        upgradePlan.executive_brief.health_grade === 'C' ? '#F59E0B' : '#EF4444',
                    }}>
                      {upgradePlan.executive_brief.health_grade}
                    </div>
                    <div>
                      <div style={{ fontSize: '0.85rem', fontWeight: 600 }}>{upgradePlan.executive_brief.headline}</div>
                      <div style={{ fontSize: '0.7rem', color: '#94A3B8', marginTop: '2px' }}>
                        Estimated effort: <span style={{ textTransform: 'capitalize' }}>{upgradePlan.executive_brief.estimated_total_effort}</span>
                      </div>
                    </div>
                  </div>
                  {upgradePlan.executive_brief.top_priorities?.length > 0 && (
                    <div style={{ marginBottom: '8px' }}>
                      <div style={{ fontSize: '0.7rem', color: '#64748B', marginBottom: '4px', textTransform: 'uppercase' }}>Top Priorities</div>
                      {upgradePlan.executive_brief.top_priorities.map((p: string, i: number) => (
                        <div key={i} style={{ fontSize: '0.75rem', padding: '2px 0' }}>🎯 {p}</div>
                      ))}
                    </div>
                  )}
                  {upgradePlan.executive_brief.risk_summary && (
                    <div style={{ fontSize: '0.75rem', color: '#F59E0B', marginTop: '4px' }}>
                      ⚠️ {upgradePlan.executive_brief.risk_summary}
                    </div>
                  )}
                  {upgradePlan.executive_brief.forge_automation_note && (
                    <div style={{ fontSize: '0.75rem', color: '#22C55E', marginTop: '4px' }}>
                      🤖 {upgradePlan.executive_brief.forge_automation_note}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Summary Stats Bar */}
            {upgradePlan.summary_stats && (
              <div style={{ marginBottom: '24px' }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: '8px' }}>
                  {[
                    { label: 'Deps checked', value: upgradePlan.summary_stats.dependencies_checked, color: '#94A3B8' },
                    { label: 'Current', value: upgradePlan.summary_stats.current_count, color: '#22C55E' },
                    { label: 'Outdated', value: upgradePlan.summary_stats.outdated_count, color: '#F59E0B' },
                    { label: 'EOL', value: upgradePlan.summary_stats.eol_count, color: '#EF4444' },
                    { label: 'Patterns', value: upgradePlan.summary_stats.patterns_detected, color: '#3B82F6' },
                    { label: 'Migrations', value: upgradePlan.summary_stats.migrations_recommended, color: '#7C3AED' },
                    { label: 'High priority', value: upgradePlan.summary_stats.high_priority_migrations, color: '#EF4444' },
                    { label: 'Automatable', value: upgradePlan.summary_stats.forge_automatable, color: '#22C55E' },
                  ].map((stat, i) => (
                    <div key={i} style={{
                      background: '#1E293B', borderRadius: '8px', padding: '10px', border: '1px solid #334155', textAlign: 'center',
                    }}>
                      <div style={{ fontSize: '1.1rem', fontWeight: 700, color: stat.color }}>{stat.value}</div>
                      <div style={{ fontSize: '0.65rem', color: '#64748B', textTransform: 'uppercase' }}>{stat.label}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Version Report */}
            {upgradePlan.version_report?.length > 0 && (
              <div style={{ marginBottom: '24px' }}>
                <h3 style={{ fontSize: '0.85rem', color: '#94A3B8', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Version Report
                </h3>
                <div style={{ background: '#1E293B', borderRadius: '8px', border: '1px solid #334155', overflow: 'hidden' }}>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 80px 80px 60px', gap: '0', fontSize: '0.7rem',
                    color: '#64748B', padding: '8px 14px', borderBottom: '1px solid #334155', textTransform: 'uppercase' }}>
                    <span>Package</span><span>Current</span><span>Latest</span><span>Status</span>
                  </div>
                  {(upgradePlan.version_report as any[]).map((v: any, i: number) => (
                    <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr 80px 80px 60px', gap: '0',
                      padding: '6px 14px', fontSize: '0.8rem', borderBottom: i < upgradePlan.version_report.length - 1 ? '1px solid #1E293B' : 'none',
                      background: v.status === 'eol' ? 'rgba(239,68,68,0.05)' : v.status === 'outdated' ? 'rgba(245,158,11,0.05)' : 'transparent',
                    }}>
                      <span style={{ fontFamily: 'monospace' }}>{v.package}</span>
                      <span style={{ color: '#94A3B8', fontFamily: 'monospace' }}>{v.current}</span>
                      <span style={{ color: '#94A3B8', fontFamily: 'monospace' }}>{v.latest}</span>
                      <span style={{
                        fontSize: '0.65rem', fontWeight: 600, padding: '1px 6px', borderRadius: '4px',
                        background: v.status === 'current' ? '#14532D' : v.status === 'outdated' ? '#78350F' : v.status === 'eol' ? '#7F1D1D' : '#1E3A5F',
                        color: v.status === 'current' ? '#22C55E' : v.status === 'outdated' ? '#F59E0B' : v.status === 'eol' ? '#EF4444' : '#3B82F6',
                      }}>
                        {v.status === 'current' ? '🟢' : v.status === 'outdated' ? '🟡' : v.status === 'eol' ? '🔴' : '❓'} {v.status}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Pattern Findings */}
            {upgradePlan.pattern_findings?.length > 0 && (
              <div style={{ marginBottom: '24px' }}>
                <h3 style={{ fontSize: '0.85rem', color: '#94A3B8', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Detected Patterns
                </h3>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  {(upgradePlan.pattern_findings as any[]).map((p: any, i: number) => (
                    <div key={i} style={{
                      background: '#1E293B', borderRadius: '8px', padding: '10px 14px', border: '1px solid #334155',
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                        <span style={{
                          padding: '1px 6px', borderRadius: '4px', fontSize: '0.65rem', fontWeight: 600,
                          background: p.severity === 'high' ? '#7F1D1D' : p.severity === 'medium' ? '#78350F' : '#1E3A5F',
                          color: p.severity === 'high' ? '#EF4444' : p.severity === 'medium' ? '#F59E0B' : '#3B82F6',
                        }}>
                          {p.severity.toUpperCase()}
                        </span>
                        <span style={{ fontSize: '0.7rem', color: '#64748B', fontFamily: 'monospace' }}>{p.id}</span>
                        <span style={{ fontSize: '0.8rem', fontWeight: 500 }}>{p.name}</span>
                        <span style={{
                          marginLeft: 'auto', fontSize: '0.65rem', color: '#64748B', textTransform: 'uppercase',
                        }}>{p.category}</span>
                      </div>
                      <div style={{ fontSize: '0.75rem', color: '#94A3B8' }}>{p.detail}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Migration Recommendations */}
            {upgradePlan.migration_recommendations?.length > 0 && (
              <div style={{ marginBottom: '24px' }}>
                <h3 style={{ fontSize: '0.85rem', color: '#94A3B8', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Migration Recommendations
                </h3>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {(upgradePlan.migration_recommendations as any[]).map((m: any, i: number) => (
                    <div key={i} style={{
                      background: '#1E293B', borderRadius: '8px', padding: '12px 14px', border: '1px solid #334155',
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px', flexWrap: 'wrap' }}>
                        <span style={{
                          padding: '1px 6px', borderRadius: '4px', fontSize: '0.65rem', fontWeight: 600,
                          background: m.priority === 'high' ? '#7F1D1D' : m.priority === 'medium' ? '#78350F' : '#1E3A5F',
                          color: m.priority === 'high' ? '#EF4444' : m.priority === 'medium' ? '#F59E0B' : '#3B82F6',
                        }}>
                          {m.priority.toUpperCase()}
                        </span>
                        <span style={{ fontSize: '0.7rem', color: '#64748B', fontFamily: 'monospace' }}>{m.id}</span>
                        <span style={{ fontSize: '0.8rem', fontWeight: 500 }}>{m.from_state} → {m.to_state}</span>
                        {m.forge_automatable && (
                          <span style={{
                            fontSize: '0.6rem', padding: '1px 6px', borderRadius: '4px',
                            background: '#14532D', color: '#22C55E', fontWeight: 600,
                          }}>
                            🤖 AUTOMATABLE
                          </span>
                        )}
                      </div>
                      <div style={{ fontSize: '0.75rem', color: '#94A3B8', marginBottom: '6px' }}>{m.rationale}</div>
                      <div style={{ display: 'flex', gap: '12px', fontSize: '0.7rem', color: '#64748B', marginBottom: '6px' }}>
                        <span>Effort: <span style={{ textTransform: 'capitalize', color: m.effort === 'high' ? '#EF4444' : m.effort === 'medium' ? '#F59E0B' : '#22C55E' }}>{m.effort}</span></span>
                        <span>Risk: <span style={{ textTransform: 'capitalize', color: m.risk === 'high' ? '#EF4444' : m.risk === 'medium' ? '#F59E0B' : '#22C55E' }}>{m.risk}</span></span>
                        <span style={{ textTransform: 'capitalize' }}>{m.category}</span>
                      </div>
                      {m.steps?.length > 0 && (
                        <div style={{ paddingLeft: '8px', borderLeft: '2px solid #334155' }}>
                          {m.steps.map((step: string, si: number) => (
                            <div key={si} style={{ fontSize: '0.7rem', color: '#94A3B8', padding: '2px 0' }}>
                              {si + 1}. {step}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Forge Spec */}
            {upgradePlan.forge_spec && (
              <div style={{ marginBottom: '24px' }}>
                <h3 style={{ fontSize: '0.85rem', color: '#94A3B8', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Forge Automation Spec
                </h3>
                <div style={{ background: '#1E293B', borderRadius: '8px', padding: '14px', border: '1px solid #334155' }}>
                  <div style={{ fontSize: '0.75rem', color: '#22C55E', marginBottom: '8px' }}>
                    🤖 {upgradePlan.forge_spec.total_automatable} task(s) can be automated by Forge
                  </div>
                  <pre style={{
                    background: '#0F172A', borderRadius: '6px', padding: '10px', fontSize: '0.7rem',
                    color: '#94A3B8', overflow: 'auto', maxHeight: '200px', margin: 0,
                  }}>
                    {JSON.stringify(upgradePlan.forge_spec, null, 2)}
                  </pre>
                </div>
              </div>
            )}

            {/* Action buttons */}
            <div style={{ display: 'flex', gap: '10px', justifyContent: 'center', flexWrap: 'wrap' }}>
              <button
                onClick={() => { setView('dossier'); }}
                style={{
                  background: '#334155',
                  color: '#F8FAFC',
                  border: 'none',
                  borderRadius: '6px',
                  padding: '8px 18px',
                  cursor: 'pointer',
                  fontSize: '0.8rem',
                  fontWeight: 500,
                }}
              >
                ← Back to Dossier
              </button>
              <button
                onClick={() => { setView('repos'); setActiveRepo(null); setActiveRun(null); setDeepScanResult(null); setUpgradePlan(null); }}
                style={{
                  background: '#334155',
                  color: '#F8FAFC',
                  border: 'none',
                  borderRadius: '6px',
                  padding: '8px 18px',
                  cursor: 'pointer',
                  fontSize: '0.8rem',
                  fontWeight: 500,
                }}
              >
                ← Back to Repos
              </button>
            </div>
          </div>
        )}

        {/* ─── RESULTS VIEW ─── */}
        {view === 'results' && (
          <div>
            {/* Check list */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '24px' }}>
              {ALL_CHECKS.map((check) => {
                const status = liveChecks[check.code] ?? 'pending';
                return (
                  <div
                    key={check.code}
                    onClick={() => liveDetails[check.code] && setExpandedCheck(expandedCheck === check.code ? null : check.code)}
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      padding: '8px 14px',
                      background: '#1E293B',
                      borderRadius: '6px',
                      border: `1px solid ${status === 'FAIL' ? '#7F1D1D' : '#334155'}`,
                      cursor: liveDetails[check.code] ? 'pointer' : 'default',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '0.85rem' }}>
                      <span>{checkIcon(status)}</span>
                      <span style={{ color: '#94A3B8', fontSize: '0.7rem', fontFamily: 'monospace', width: '24px' }}>{check.code}</span>
                      <span>{check.name}</span>
                      <span style={{
                        marginLeft: 'auto', fontSize: '0.7rem', fontWeight: 500,
                        color: status === 'PASS' ? '#22C55E' : status === 'FAIL' ? '#EF4444' : status === 'WARN' ? '#F59E0B' : '#64748B',
                      }}>
                        {status}
                      </span>
                    </div>
                    {expandedCheck === check.code && liveDetails[check.code] && (
                      <div style={{ marginTop: '6px', paddingLeft: '42px', fontSize: '0.75rem', color: '#94A3B8', whiteSpace: 'pre-wrap' }}>
                        {liveDetails[check.code]}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            {/* Action buttons */}
            <div style={{ display: 'flex', gap: '10px', justifyContent: 'center', flexWrap: 'wrap' }}>
              <button
                onClick={() => activeRepo && handleScout(activeRepo)}
                style={{
                  background: '#334155',
                  color: '#F8FAFC',
                  border: 'none',
                  borderRadius: '6px',
                  padding: '8px 18px',
                  cursor: 'pointer',
                  fontSize: '0.8rem',
                  fontWeight: 500,
                }}
              >
                🔄 Re-Scout
              </button>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}

export default Scout;
