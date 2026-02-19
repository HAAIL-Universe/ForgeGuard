import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import { useWebSocket } from '../hooks/useWebSocket';
import AppShell from '../components/AppShell';
import RepoCard from '../components/RepoCard';
import type { Repo, SyncProgress } from '../components/RepoCard';
import RepoPickerModal from '../components/RepoPickerModal';
import ConfirmDialog from '../components/ConfirmDialog';
import ProjectCard from '../components/ProjectCard';
import type { Project } from '../components/ProjectCard';
import CreateProjectModal from '../components/CreateProjectModal';
import EmptyState from '../components/EmptyState';
import { SkeletonCard } from '../components/Skeleton';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

function Dashboard() {
  const { token } = useAuth();
  const { addToast } = useToast();
  const navigate = useNavigate();
  const [repos, setRepos] = useState<Repo[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [showPicker, setShowPicker] = useState(false);
  const [showCreateProject, setShowCreateProject] = useState(false);
  const [disconnectTarget, setDisconnectTarget] = useState<Repo | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [updatingRepoId, setUpdatingRepoId] = useState<string | null>(null);
  const [syncProgress, setSyncProgress] = useState<SyncProgress | null>(null);

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

  const fetchProjects = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/projects`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setProjects(data.items ?? data);
      }
    } catch { /* best effort */ }
    finally { setProjectsLoading(false); }
  }, [token]);

  useEffect(() => {
    fetchRepos();
    fetchProjects();
  }, [fetchRepos, fetchProjects]);

  // Real-time: refresh repos when an audit completes or health check finishes;
  // refresh projects on build events; track sync progress.
  useWebSocket(useCallback((data) => {
    if (data.type === 'audit_update') fetchRepos();
    if (data.type === 'repos_health_updated') {
      setIsRefreshing(false);
      fetchRepos();
    }
    if (data.type === 'sync_progress') {
      const p = data.payload as SyncProgress & { repo_id: string };
      setUpdatingRepoId(p.repo_id);
      setSyncProgress({ commits_done: p.commits_done, commits_total: p.commits_total, current_sha: p.current_sha, status: p.status });
      if (p.status === 'completed' || p.status === 'error') {
        // Clear after a brief delay so the user sees the final count
        setTimeout(() => { setUpdatingRepoId(null); setSyncProgress(null); }, 1200);
        fetchRepos();
      }
    }
    if (data.type === 'build_complete' || data.type === 'build_error' || data.type === 'build_started') fetchProjects();
  }, [fetchRepos, fetchProjects]));

  const triggerHealthCheck = async () => {
    setIsRefreshing(true);
    try {
      await fetch(`${API_BASE}/repos/health-check`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
    } catch {
      setIsRefreshing(false);
    }
    // isRefreshing cleared when repos_health_updated WS event arrives
  };

  const handleProjectClick = (project: Project) => {
    navigate(`/projects/${project.id}`);
  };

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

  const handleUpdate = async (repo: Repo) => {
    setUpdatingRepoId(repo.id);
    try {
      const res = await fetch(`${API_BASE}/repos/${repo.id}/sync`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        addToast(`Syncing ${repo.full_name}...`);
        fetchRepos();
      } else {
        const err = await res.json().catch(() => ({ detail: 'Sync failed' }));
        addToast(err.detail ?? 'Failed to sync repo');
      }
    } catch {
      addToast('Network error syncing repo');
    } finally {
      // Don't clear here — sync_progress WS events will manage the state
      // setUpdatingRepoId(null);
    }
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
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <button
              onClick={triggerHealthCheck}
              disabled={isRefreshing}
              title="Refresh repo health"
              style={{
                background: 'transparent',
                color: isRefreshing ? '#64748B' : '#94A3B8',
                border: '1px solid #334155',
                borderRadius: '6px',
                padding: '6px 10px',
                cursor: isRefreshing ? 'wait' : 'pointer',
                fontSize: '0.8rem',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
              }}
            >
              {isRefreshing ? (
                <span style={{ display: 'inline-block', animation: 'spin 1s linear infinite' }}>↻</span>
              ) : '↻'}
            </button>
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
                onUpdate={handleUpdate}
                onClick={handleRepoClick}
                updatingId={updatingRepoId}
                syncProgress={syncProgress}
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

      {/* Projects Section */}
      <div style={{ padding: '0 24px 24px', maxWidth: '960px', margin: '0 auto' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: '20px',
          }}
        >
          <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Projects</h2>
          <button
            onClick={() => setShowCreateProject(true)}
            data-testid="create-project-btn"
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
            Create Project
          </button>
        </div>

        {projectsLoading ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <SkeletonCard />
            <SkeletonCard />
          </div>
        ) : projects.length === 0 ? (
          <EmptyState
            message='No projects yet. Click "Create Project" to start building.'
            actionLabel="Create Project"
            onAction={() => setShowCreateProject(true)}
          />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {projects.map((project) => (
              <ProjectCard key={project.id} project={project} onClick={handleProjectClick} />
            ))}
          </div>
        )}
      </div>

      {showCreateProject && (
        <CreateProjectModal
          onClose={() => setShowCreateProject(false)}
          onCreated={(project) => {
            setShowCreateProject(false);
            navigate(`/projects/${project.id}`);
          }}
        />
      )}

      {disconnectTarget && (
        <ConfirmDialog
          title={disconnectTarget.repo_status === 'deleted' ? 'Clear Repo' : 'Disconnect Repo'}
          message={
            disconnectTarget.repo_status === 'deleted'
              ? `Clear ${disconnectTarget.full_name} from ForgeGuard? This deleted repo will be removed along with its audit history.`
              : `Remove ${disconnectTarget.full_name} from ForgeGuard? This will delete all audit history for this repo.`
          }
          confirmLabel={disconnectTarget.repo_status === 'deleted' ? 'Clear' : 'Disconnect'}
          onConfirm={handleDisconnect}
          onCancel={() => setDisconnectTarget(null)}
        />
      )}
    </AppShell>
  );
}

export default Dashboard;
