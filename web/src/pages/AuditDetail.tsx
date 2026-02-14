import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import ResultBadge from '../components/ResultBadge';
import CheckResultCard from '../components/CheckResultCard';
import type { CheckResultData } from '../components/CheckResultCard';

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
        }
      } catch {
        // network error
      } finally {
        setLoading(false);
      }
    };
    fetchDetail();
  }, [repoId, auditId, token]);

  if (loading) {
    return (
      <div style={{ padding: '24px', color: '#94A3B8' }}>
        Loading audit detail...
      </div>
    );
  }

  if (!detail) {
    return (
      <div style={{ padding: '24px', color: '#94A3B8' }}>
        Audit not found.
      </div>
    );
  }

  const duration =
    detail.started_at && detail.completed_at
      ? `${((new Date(detail.completed_at).getTime() - new Date(detail.started_at).getTime()) / 1000).toFixed(1)}s`
      : null;

  return (
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
  );
}

export default AuditDetailPage;
