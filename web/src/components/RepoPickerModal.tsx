import { useState, useEffect, useMemo } from 'react';
import { useAuth } from '../context/AuthContext';

interface GitHubRepo {
  github_repo_id: number;
  full_name: string;
  default_branch: string;
  private: boolean;
}

interface RepoPickerModalProps {
  onClose: () => void;
  onConnected: () => void;
}

const API_BASE = import.meta.env.VITE_API_URL ?? '';

function RepoPickerModal({ onClose, onConnected }: RepoPickerModalProps) {
  const { token } = useAuth();
  const [repos, setRepos] = useState<GitHubRepo[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [connecting, setConnecting] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchAvailable = async () => {
      try {
        const res = await fetch(`${API_BASE}/repos/available`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) throw new Error('Failed to load repos');
        const data = await res.json();
        setRepos(data.items);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load repos');
      } finally {
        setLoading(false);
      }
    };
    fetchAvailable();
  }, [token]);

  const filtered = useMemo(() => {
    if (!search) return repos;
    const q = search.toLowerCase();
    return repos.filter((r) => r.full_name.toLowerCase().includes(q));
  }, [repos, search]);

  const handleConnect = async (repo: GitHubRepo) => {
    setConnecting(repo.github_repo_id);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/repos/connect`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          github_repo_id: repo.github_repo_id,
          full_name: repo.full_name,
          default_branch: repo.default_branch,
        }),
      });
      if (res.status === 409) {
        setError('Repo is already connected');
        return;
      }
      if (!res.ok) throw new Error('Failed to connect repo');
      onConnected();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to connect');
    } finally {
      setConnecting(null);
    }
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0,0,0,0.6)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 100,
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: '#1E293B',
          borderRadius: '8px',
          padding: '24px',
          maxWidth: '520px',
          width: '90%',
          maxHeight: '80vh',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <h3 style={{ margin: '0 0 16px 0', fontSize: '1rem' }}>Connect a Repo</h3>

        <input
          type="text"
          placeholder="Search repos..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{
            width: '100%',
            padding: '8px 12px',
            background: '#0F172A',
            border: '1px solid #334155',
            borderRadius: '6px',
            color: '#F8FAFC',
            fontSize: '0.875rem',
            marginBottom: '12px',
            boxSizing: 'border-box',
          }}
        />

        {error && (
          <div
            style={{
              color: '#EF4444',
              fontSize: '0.8rem',
              marginBottom: '8px',
            }}
          >
            {error}
          </div>
        )}

        <div style={{ overflowY: 'auto', flex: 1, minHeight: 0 }}>
          {loading ? (
            <p style={{ color: '#94A3B8', textAlign: 'center' }}>Loading repos...</p>
          ) : filtered.length === 0 ? (
            <p style={{ color: '#94A3B8', textAlign: 'center' }}>No repos found</p>
          ) : (
            filtered.map((repo) => (
              <div
                key={repo.github_repo_id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '10px 8px',
                  borderBottom: '1px solid #334155',
                }}
              >
                <div>
                  <div style={{ fontSize: '0.875rem' }}>{repo.full_name}</div>
                  <div style={{ color: '#94A3B8', fontSize: '0.75rem' }}>
                    {repo.private ? 'Private' : 'Public'} &middot; {repo.default_branch}
                  </div>
                </div>
                <button
                  onClick={() => handleConnect(repo)}
                  disabled={connecting === repo.github_repo_id}
                  style={{
                    background: '#2563EB',
                    color: '#fff',
                    border: 'none',
                    borderRadius: '6px',
                    padding: '6px 14px',
                    cursor:
                      connecting === repo.github_repo_id ? 'not-allowed' : 'pointer',
                    fontSize: '0.75rem',
                    opacity: connecting === repo.github_repo_id ? 0.6 : 1,
                  }}
                >
                  {connecting === repo.github_repo_id ? 'Connecting...' : 'Connect'}
                </button>
              </div>
            ))
          )}
        </div>

        <button
          onClick={onClose}
          style={{
            background: 'transparent',
            color: '#94A3B8',
            border: '1px solid #334155',
            borderRadius: '6px',
            padding: '8px 16px',
            cursor: 'pointer',
            fontSize: '0.875rem',
            marginTop: '16px',
            alignSelf: 'flex-end',
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

export default RepoPickerModal;
