import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import { useWebSocket } from '../hooks/useWebSocket';
import AppShell from '../components/AppShell';
import RepoCard from '../components/RepoCard';
import type { Repo } from '../components/RepoCard';
import RepoPickerModal from '../components/RepoPickerModal';
import ConfirmDialog from '../components/ConfirmDialog';
import EmptyState from '../components/EmptyState';
import { SkeletonCard } from '../components/Skeleton';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

function Dashboard() {
  const { token } = useAuth();
  const { addToast } = useToast();
  const navigate = useNavigate();
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
      } else {
        addToast('Failed to load repos');
      }
    } catch {
      addToast('Network error loading repos');
    } finally {
      setLoading(false);
    }
  }, [token, addToast]);

  useEffect(() => {
    fetchRepos();
  }, [fetchRepos]);

  // Real-time: refresh repos when an audit completes
  useWebSocket(useCallback((data) => {
    if (data.type === 'audit_update') {
      fetchRepos();
    }
  }, [fetchRepos]));

  const handleDisconnect = async () => {
    if (!disconnectTarget) return;
    try {
      const res = await fetch(`${API_BASE}/repos/${disconnectTarget.id}/disconnect`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) addToast('Failed to disconnect repo');
      setDisconnectTarget(null);
      fetchRepos();
    } catch {
      addToast('Network error disconnecting repo');
      setDisconnectTarget(null);
    }
  };

  const handleRepoClick = (repo: Repo) => {
    navigate(`/repos/${repo.id}`);
  };

  return (
    <AppShell sidebarRepos={repos} onReposChange={fetchRepos}>
      <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
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
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <SkeletonCard />
            <SkeletonCard />
            <SkeletonCard />
          </div>
        ) : repos.length === 0 ? (
          <EmptyState
            message='No repos connected yet. Click "Connect a Repo" to get started.'
            actionLabel="Connect a Repo"
            onAction={() => setShowPicker(true)}
          />
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
      </div>

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
    </AppShell>
  );
}

export default Dashboard;
