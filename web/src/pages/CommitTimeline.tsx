import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import { useWebSocket } from '../hooks/useWebSocket';
import AppShell from '../components/AppShell';
import CommitRow from '../components/CommitRow';
import type { AuditRun } from '../components/CommitRow';
import HealthBadge from '../components/HealthBadge';
import EmptyState from '../components/EmptyState';
import { SkeletonRow } from '../components/Skeleton';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

function CommitTimeline() {
  const { repoId } = useParams<{ repoId: string }>();
  const { token } = useAuth();
  const { addToast } = useToast();
  const navigate = useNavigate();

  const [audits, setAudits] = useState<AuditRun[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [scanningFile, setScanningFile] = useState<string | null>(null);
  const [filesDone, setFilesDone] = useState(0);
  const [filesTotal, setFilesTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const limit = 50;

  const fetchAudits = useCallback(async () => {
    try {
      const res = await fetch(
        `${API_BASE}/repos/${repoId}/audits?limit=${limit}&offset=${offset}`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      if (res.ok) {
        const data = await res.json();
        setAudits(data.items);
        setTotal(data.total);
      } else {
        addToast('Failed to load audits');
      }
    } catch {
      addToast('Network error loading audits');
    } finally {
      setLoading(false);
    }
  }, [repoId, token, offset, addToast]);

  useEffect(() => {
    fetchAudits();
  }, [fetchAudits]);

  // Real-time: refresh when audit for this repo completes; track sync progress
  useWebSocket(useCallback((data) => {
    if (data.type === 'audit_update') {
      const payload = data.payload as { repo_id?: string };
      if (payload.repo_id === repoId) {
        fetchAudits();
      }
    }
    if (data.type === 'sync_progress') {
      const p = data.payload as { repo_id?: string; status?: string };
      if (p.repo_id === repoId) {
        if (p.status === 'running') {
          setSyncing(true);
        } else {
          setSyncing(false);
          setScanningFile(null);
          fetchAudits();
        }
      }
    }
    if (data.type === 'audit_progress') {
      const p = data.payload as { repo_id?: string; file?: string; files_done?: number; files_total?: number };
      if (p.repo_id === repoId) {
        setScanningFile(p.file ?? null);
        setFilesDone(p.files_done ?? 0);
        setFilesTotal(p.files_total ?? 0);
      }
    }
  }, [fetchAudits, repoId]));

  const handleAuditClick = (audit: AuditRun) => {
    navigate(`/repos/${repoId}/audits/${audit.id}`);
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      const res = await fetch(`${API_BASE}/repos/${repoId}/sync`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        addToast(`Synced ${data.synced} commit(s), ${data.skipped} already tracked`, 'success');
        fetchAudits();
      } else {
        addToast('Failed to sync commits');
      }
    } catch {
      addToast('Network error syncing commits');
    } finally {
      setSyncing(false);
    }
  };

  // Compute health from loaded audits
  const computedHealth = (() => {
    const completed = audits.filter((a) => a.status === 'completed');
    if (completed.length === 0) return 'pending';
    const allPass = completed.every((a) => a.overall_result === 'PASS');
    const anyFail = completed.some((a) => a.overall_result === 'FAIL' || a.overall_result === 'ERROR');
    if (allPass) return 'green';
    if (anyFail) return 'red';
    return 'yellow';
  })();

  return (
    <AppShell>
      <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '20px' }}>
          <button
            onClick={() => navigate('/')}
            style={{
              background: 'transparent',
              color: '#94A3B8',
              border: '1px solid #334155',
              borderRadius: '6px',
              padding: '6px 12px',
              cursor: 'pointer',
              fontSize: '0.8rem',
            }}
          >
            Back
          </button>
          <HealthBadge score={computedHealth} />
          <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Commit Timeline</h2>
          <span style={{ color: '#94A3B8', fontSize: '0.8rem' }}>({total} audits)</span>
          <button
            onClick={handleSync}
            disabled={syncing}
            data-testid="sync-commits-btn"
            style={{
              marginLeft: 'auto',
              background: syncing ? '#1E293B' : '#2563EB',
              color: '#fff',
              border: 'none',
              borderRadius: '6px',
              padding: '6px 14px',
              cursor: syncing ? 'not-allowed' : 'pointer',
              fontSize: '0.8rem',
              opacity: syncing ? 0.6 : 1,
            }}
          >
            {syncing ? 'Syncing...' : 'Sync Commits'}
          </button>
        </div>

        {/* Live scanning indicator */}
        {syncing && scanningFile && (
          <div
            style={{
              marginBottom: '12px',
              padding: '8px 14px',
              background: '#1E293B',
              borderRadius: '6px',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              fontSize: '0.8rem',
              color: '#94A3B8',
            }}
          >
            <span style={{ display: 'inline-block', animation: 'spin 1s linear infinite' }}>&#9696;</span>
            <span>
              Scanning {filesDone}/{filesTotal} &mdash;{' '}
              <span style={{ color: '#CBD5E1', fontFamily: 'monospace' }}>{scanningFile}</span>
            </span>
          </div>
        )}

        {loading ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <SkeletonRow />
            <SkeletonRow />
            <SkeletonRow />
            <SkeletonRow />
            <SkeletonRow />
          </div>
        ) : audits.length === 0 ? (
          <EmptyState message="No audit results yet. Push a commit to trigger the first audit." />
        ) : (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              {audits.map((audit) => (
                <CommitRow key={audit.id} audit={audit} onClick={handleAuditClick} />
              ))}
            </div>
            {total > offset + limit && (
              <button
                onClick={() => setOffset((o) => o + limit)}
                style={{
                  display: 'block',
                  margin: '16px auto',
                  background: '#1E293B',
                  color: '#94A3B8',
                  border: '1px solid #334155',
                  borderRadius: '6px',
                  padding: '8px 24px',
                  cursor: 'pointer',
                  fontSize: '0.85rem',
                }}
              >
                Load More
              </button>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}

export default CommitTimeline;
