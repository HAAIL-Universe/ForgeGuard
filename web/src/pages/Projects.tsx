import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useWebSocket } from '../hooks/useWebSocket';
import AppShell from '../components/AppShell';
import ProjectCard from '../components/ProjectCard';
import type { Project } from '../components/ProjectCard';
import CreateProjectModal from '../components/CreateProjectModal';
import EmptyState from '../components/EmptyState';
import { SkeletonCard } from '../components/Skeleton';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

function Projects() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);

  const fetchProjects = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/projects`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setProjects(data.items ?? data ?? []);
      }
    } catch {
      /* best effort */
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    fetchProjects();
  }, [fetchProjects]);

  // Refresh when contracts finish generating
  useWebSocket(
    useCallback(
      (data: { type: string; payload: unknown }) => {
        if (data.type === 'contract_progress') {
          const p = data.payload as { status?: string; index?: number; total?: number };
          if (p.status === 'done' && typeof p.index === 'number' && typeof p.total === 'number' && p.index === p.total - 1) {
            fetchProjects();
          }
        }
        if (data.type === 'build_complete' || data.type === 'build_error' || data.type === 'build_started') {
          fetchProjects();
        }
      },
      [fetchProjects],
    ),
  );

  const handleProjectClick = (project: Project) => {
    navigate(`/projects/${project.id}`);
  };

  return (
    <AppShell>
      <div
        style={{
          padding: '32px 24px',
          maxWidth: '720px',
          margin: '0 auto',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: '24px',
          }}
        >
          <h2 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 700 }}>Projects</h2>
          <button
            onClick={() => setShowCreate(true)}
            data-testid="create-project-btn"
            style={{
              background: '#2563EB',
              color: '#fff',
              border: 'none',
              borderRadius: '6px',
              padding: '8px 16px',
              cursor: 'pointer',
              fontSize: '0.875rem',
              fontWeight: 600,
            }}
          >
            Create Project
          </button>
        </div>

        {loading ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            <SkeletonCard />
            <SkeletonCard />
          </div>
        ) : projects.length === 0 ? (
          <EmptyState
            message='No projects yet. Click "Create Project" to start building.'
            actionLabel="Create Project"
            onAction={() => setShowCreate(true)}
          />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {projects.map((project) => (
              <ProjectCard key={project.id} project={project} onClick={handleProjectClick} />
            ))}
          </div>
        )}
      </div>

      {showCreate && (
        <CreateProjectModal
          onClose={() => setShowCreate(false)}
          onCreated={(project) => {
            setShowCreate(false);
            navigate(`/projects/${project.id}`);
          }}
        />
      )}
    </AppShell>
  );
}

export default Projects;
