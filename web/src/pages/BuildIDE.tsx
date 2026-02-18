/**
 * BuildIDE — unified Forge IDE interface for project builds.
 *
 * Replaces the legacy BuildProgress page with a full-screen IDE modal
 * that displays build phases as tasks, live logs in split panels,
 * token metrics with $ cost, and slash commands for build control.
 *
 * This is a thin wrapper that:
 *   1. Extracts projectId from the URL
 *   2. Fetches the project name for the header
 *   3. Renders ForgeIDEModal in 'build' mode
 */
import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import ForgeIDEModal from '../components/ForgeIDEModal';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

export default function BuildIDE() {
  const { projectId } = useParams<{ projectId: string }>();
  const { token } = useAuth();
  const navigate = useNavigate();
  const [repoName, setRepoName] = useState('');
  const [ready, setReady] = useState(false);

  /* Fetch project details to get the repo/project name */
  useEffect(() => {
    if (!projectId || !token) return;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/projects/${projectId}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const data = await res.json();
          setRepoName(
            data.repo_full_name || data.name || data.target_ref || `Project ${projectId.slice(0, 8)}`,
          );
        } else {
          setRepoName(`Project ${projectId?.slice(0, 8) || '?'}`);
        }
      } catch {
        setRepoName(`Project ${projectId?.slice(0, 8) || '?'}`);
      }
      setReady(true);
    })();
  }, [projectId, token]);

  if (!projectId) {
    return (
      <div style={{
        background: '#0F172A', color: '#EF4444', minHeight: '100vh',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: 'monospace', fontSize: '0.9rem',
      }}>
        Missing project ID
      </div>
    );
  }

  if (!ready) {
    return (
      <div style={{
        background: '#0F172A', color: '#94A3B8', minHeight: '100vh',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <div style={{
          width: '40px', height: '40px',
          border: '3px solid #1E293B', borderTop: '3px solid #2563EB',
          borderRadius: '50%', animation: 'spin 0.8s linear infinite',
        }} />
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    );
  }

  return (
    <ForgeIDEModal
      mode="build"
      projectId={projectId}
      repoName={repoName}
      onClose={() => navigate(`/projects/${projectId}`)}
    />
  );
}
