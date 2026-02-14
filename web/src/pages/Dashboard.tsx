import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import RepoCard from '../components/RepoCard';
import type { Repo } from '../components/RepoCard';
import RepoPickerModal from '../components/RepoPickerModal';
import ConfirmDialog from '../components/ConfirmDialog';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

function Dashboard() {
  const { user, token, logout } = useAuth();
  const [repos, setRepos] = useState<Repo[]>([]);
  const [loading, setLoading] = useState(true);
  const [showPicker, setShowPicker] = useState(false);
  const [disconnectTarget, setDisconnectTarget] = useState<Repo | null>(null);

  const fetchRepos = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/repos`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setRepos(data.items);
      }
    } catch {
      // network error -- keep existing
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    fetchRepos();
  }, [fetchRepos]);

  const handleDisconnect = async () => {
    if (!disconnectTarget) return;
    try {
      await fetch(`${API_BASE}/repos/${disconnectTarget.id}/disconnect`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      setDisconnectTarget(null);
      fetchRepos();
    } catch {
      // best effort
      setDisconnectTarget(null);
    }
  };

  const handleRepoClick = (_repo: Repo) => {
    // Phase 3 will add navigation to /repos/:repoId
  };

  return (
    <div style={{ background: '#0F172A', color: '#F8FAFC', minHeight: '100vh' }}>
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '16px 24px',
          borderBottom: '1px solid #1E293B',
        }}
      >
        <h1 style={{ fontSize: '1.25rem', fontWeight: 700 }}>ForgeGuard</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {user?.avatar_url && (
            <img
              src={user.avatar_url}
              alt={user.github_login}
              style={{ width: 32, height: 32, borderRadius: '50%' }}
            />
          )}
          <span style={{ color: '#94A3B8' }}>{user?.github_login}</span>
          <button
            onClick={logout}
            style={{
              background: 'transparent',
              color: '#94A3B8',
              border: '1px solid #334155',
              borderRadius: '6px',
              padding: '6px 16px',
              cursor: 'pointer',
              fontSize: '0.875rem',
            }}
          >
            Logout
          </button>
        </div>
      </header>

      <main style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: '20px',
          }}
        >
          <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Connected Repos</h2>
          <button
            onClick={() => setShowPicker(true)}
            style={{
              background: '#2563EB',
              color: '#fff',
              border: 'none',
              borderRadius: '6px',
              padding: '8px 16px',
              cursor: 'pointer',
              fontSize: '0.875rem',
            }}
          >
            Connect a Repo
          </button>
        </div>

        {loading ? (
          <p style={{ color: '#94A3B8' }}>Loading repos...</p>
        ) : repos.length === 0 ? (
          <div
            style={{
              textAlign: 'center',
              padding: '64px 24px',
              color: '#94A3B8',
            }}
          >
            <p>No repos connected yet. Click &quot;Connect a Repo&quot; to get started.</p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {repos.map((repo) => (
              <RepoCard
                key={repo.id}
                repo={repo}
                onDisconnect={setDisconnectTarget}
                onClick={handleRepoClick}
              />
            ))}
          </div>
        )}
      </main>

      {showPicker && (
        <RepoPickerModal
          onClose={() => setShowPicker(false)}
          onConnected={fetchRepos}
        />
      )}

      {disconnectTarget && (
        <ConfirmDialog
          title="Disconnect Repo"
          message={`Remove ${disconnectTarget.full_name} from ForgeGuard? This will delete all audit history for this repo.`}
          confirmLabel="Disconnect"
          onConfirm={handleDisconnect}
          onCancel={() => setDisconnectTarget(null)}
        />
      )}
    </div>
  );
}

export default Dashboard;
