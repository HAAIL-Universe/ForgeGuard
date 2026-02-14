import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import CommitRow from '../components/CommitRow';
import type { AuditRun } from '../components/CommitRow';
import HealthBadge from '../components/HealthBadge';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

function CommitTimeline() {
  const { repoId } = useParams<{ repoId: string }>();
  const { token } = useAuth();
  const navigate = useNavigate();

  const [audits, setAudits] = useState<AuditRun[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
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
      }
    } catch {
      // network error
    } finally {
      setLoading(false);
    }
  }, [repoId, token, offset]);

  useEffect(() => {
    fetchAudits();
  }, [fetchAudits]);

  const handleAuditClick = (audit: AuditRun) => {
    navigate(`/repos/${repoId}/audits/${audit.id}`);
  };

  return (
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
        <HealthBadge score="pending" />
        <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Commit Timeline</h2>
        <span style={{ color: '#94A3B8', fontSize: '0.8rem' }}>({total} audits)</span>
      </div>

      {loading ? (
        <p style={{ color: '#94A3B8' }}>Loading audits...</p>
      ) : audits.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '64px 24px', color: '#94A3B8' }}>
          <p>No audit results yet. Push a commit to trigger the first audit.</p>
        </div>
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
  );
}

export default CommitTimeline;
