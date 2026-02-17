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
  hypothesis: string | null;
  checks_passed: number;
  checks_failed: number;
  checks_warned: number;
  started_at: string;
  completed_at: string | null;
  checks?: ScoutCheck[];
  warnings?: ScoutCheck[];
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

type View = 'repos' | 'running' | 'results';

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
          setLiveChecks((prev) => ({ ...prev, [p.check_code]: p.result }));
          if (p.detail) {
            setLiveDetails((prev) => ({ ...prev, [p.check_code]: p.detail }));
          }
        } else if (data.type === 'scout_complete') {
          const p = data.payload;
          setActiveRun(p);
          setView('results');
          fetchHistory();
        }
      },
      [fetchHistory],
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
                        Scout Repo →
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
                              <button
                              onClick={() => handleViewRun(run)}
                              style={{
                                marginTop: '8px',
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
        {view === 'running' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
              <span style={{ fontSize: '0.8rem', color: '#94A3B8' }}>Running checks…</span>
            </div>
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
