import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import AppShell from '../components/AppShell';
import ResultBadge from '../components/ResultBadge';
import CheckResultCard from '../components/CheckResultCard';
import type { CheckResultData } from '../components/CheckResultCard';
import Skeleton from '../components/Skeleton';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

interface AuditDetail {
  id: string;
  commit_sha: string;
  commit_message: string;
  commit_author: string;
  branch: string;
  status: string;
  overall_result: string | null;
  started_at: string | null;
  completed_at: string | null;
  files_checked: number;
  checks: CheckResultData[];
}

function AuditDetailPage() {
  const { repoId, auditId } = useParams<{ repoId: string; auditId: string }>();
  const { token } = useAuth();
  const { addToast } = useToast();
  const navigate = useNavigate();

  const [detail, setDetail] = useState<AuditDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchDetail = async () => {
      try {
        const res = await fetch(
          `${API_BASE}/repos/${repoId}/audits/${auditId}`,
          { headers: { Authorization: `Bearer ${token}` } },
        );
        if (res.ok) {
          setDetail(await res.json());
        } else {
          addToast('Failed to load audit detail');
        }
      } catch {
        addToast('Network error loading audit detail');
      } finally {
        setLoading(false);
      }
    };
    fetchDetail();
  }, [repoId, auditId, token, addToast]);

  if (loading) {
    return (
      <AppShell>
        <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
          <Skeleton width="80px" height="28px" style={{ marginBottom: '20px' }} />
          <div style={{ background: '#1E293B', borderRadius: '8px', padding: '20px', marginBottom: '16px' }}>
            <Skeleton width="30%" height="14px" style={{ marginBottom: '12px' }} />
            <Skeleton width="60%" height="12px" style={{ marginBottom: '12px' }} />
            <Skeleton width="100px" height="24px" />
          </div>
          <Skeleton width="120px" height="16px" style={{ marginBottom: '12px' }} />
          <div style={{ background: '#1E293B', borderRadius: '6px', padding: '14px 18px', marginBottom: '8px' }}>
            <Skeleton width="50%" height="14px" />
          </div>
          <div style={{ background: '#1E293B', borderRadius: '6px', padding: '14px 18px', marginBottom: '8px' }}>
            <Skeleton width="50%" height="14px" />
          </div>
        </div>
      </AppShell>
    );
  }

  if (!detail) {
    return (
      <AppShell>
        <div style={{ padding: '24px', color: '#94A3B8' }}>
          Audit not found.
        </div>
      </AppShell>
    );
  }

  const duration =
    detail.started_at && detail.completed_at
      ? `${((new Date(detail.completed_at).getTime() - new Date(detail.started_at).getTime()) / 1000).toFixed(1)}s`
      : null;

  return (
    <AppShell>
      <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
        <button
          onClick={() => navigate(`/repos/${repoId}`)}
          style={{
            background: 'transparent',
            color: '#94A3B8',
            border: '1px solid #334155',
            borderRadius: '6px',
            padding: '6px 12px',
            cursor: 'pointer',
            fontSize: '0.8rem',
            marginBottom: '20px',
          }}
        >
          Back to Timeline
        </button>

        {/* Commit Info */}
        <div
          style={{
            background: '#1E293B',
            borderRadius: '8px',
            padding: '20px',
            marginBottom: '16px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
            <a
              href={`https://github.com/commit/${detail.commit_sha}`}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: '#2563EB', fontFamily: 'monospace', fontSize: '0.85rem' }}
            >
              {detail.commit_sha.substring(0, 7)}
            </a>
            <span style={{ color: '#94A3B8', fontSize: '0.8rem' }}>
              {detail.branch} &middot; {detail.commit_author}
            </span>
          </div>
          <p style={{ margin: '0 0 12px 0', fontSize: '0.9rem' }}>
            {detail.commit_message}
          </p>
          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
            <ResultBadge result={detail.overall_result} size="large" />
            <span style={{ color: '#94A3B8', fontSize: '0.8rem' }}>
              {detail.files_checked} files checked
              {duration && <span> &middot; {duration}</span>}
            </span>
          </div>
        </div>

        {/* Check Results */}
        <h3 style={{ fontSize: '1rem', marginBottom: '12px' }}>Check Results</h3>
        {detail.checks.length === 0 ? (
          <p style={{ color: '#94A3B8' }}>No check results.</p>
        ) : (
          detail.checks.map((check) => (
            <CheckResultCard key={check.id} check={check} />
          ))
        )}
      </div>
    </AppShell>
  );
}

export default AuditDetailPage;
