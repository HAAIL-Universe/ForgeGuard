/**
 * ProjectDetail -- overview page for a single project.
 * Links to questionnaire, contracts, and build progress.
 */
import { useState, useEffect } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import AppShell from '../components/AppShell';
import Skeleton from '../components/Skeleton';
import ConfirmDialog from '../components/ConfirmDialog';
import QuestionnaireModal from '../components/QuestionnaireModal';
import ContractProgress from '../components/ContractProgress';
import BuildTargetModal, { type BuildTarget } from '../components/BuildTargetModal';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

const needsKeyBanner = (
  <div style={{
    background: '#1E293B',
    border: '1px solid #92400E',
    borderRadius: '6px',
    padding: '10px 16px',
    marginBottom: '16px',
    fontSize: '0.8rem',
    color: '#FBBF24',
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
  }}>
    <span style={{ fontSize: '1rem' }}>🔑</span>
    <span>
      Add your Anthropic API key in{' '}
      <Link to="/settings" style={{ color: '#60A5FA', textDecoration: 'underline' }}>Settings</Link>{' '}
      to start a build. Questionnaires and audits are free.
    </span>
  </div>
);

interface ProjectDetailData {
  id: string;
  name: string;
  description: string | null;
  status: string;
  repo_id: string | null;
  created_at: string;
  updated_at: string;
  contracts: { contract_type: string; version: number; updated_at: string }[];
  latest_build: {
    id: string;
    phase: string;
    status: string;
    loop_count: number;
    started_at: string | null;
    completed_at: string | null;
  } | null;
}

function ProjectDetail() {
  const { projectId } = useParams<{ projectId: string }>();
  const { user, token } = useAuth();
  const { addToast } = useToast();
  const navigate = useNavigate();
  const [project, setProject] = useState<ProjectDetailData | null>(null);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [showQuestionnaire, setShowQuestionnaire] = useState(false);
  const [contractsExpanded, setContractsExpanded] = useState(false);
  const [showRegenerate, setShowRegenerate] = useState(false);
  const [showTargetPicker, setShowTargetPicker] = useState(false);

  useEffect(() => {
    const fetchProject = async () => {
      try {
        const res = await fetch(`${API_BASE}/projects/${projectId}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) setProject(await res.json());
        else addToast('Failed to load project');
      } catch {
        addToast('Network error loading project');
      } finally {
        setLoading(false);
      }
    };
    fetchProject();
  }, [projectId, token, addToast]);

  const hasContracts = (project?.contracts?.length ?? 0) > 0;

  const handleStartBuild = async () => {
    /* If no contracts exist, open the questionnaire first */
    if (!hasContracts) {
      setShowQuestionnaire(true);
      return;
    }
    /* Show target picker modal */
    setShowTargetPicker(true);
  };

  const handleTargetConfirm = async (target: BuildTarget) => {
    setStarting(true);
    try {
      const res = await fetch(`${API_BASE}/projects/${projectId}/build`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(target),
      });
      if (res.ok) {
        setShowTargetPicker(false);
        addToast('Build started', 'success');
        navigate(`/projects/${projectId}/build`);
      } else {
        const data = await res.json().catch(() => ({ detail: 'Failed to start build' }));
        addToast(data.detail || 'Failed to start build');
      }
    } catch {
      addToast('Network error starting build');
    } finally {
      setStarting(false);
    }
  };

  const handleContractsGenerated = async () => {
    setShowQuestionnaire(false);
    addToast('Contracts generated!', 'success');
    /* Refresh project data to pick up contracts */
    try {
      const res = await fetch(`${API_BASE}/projects/${projectId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) setProject(await res.json());
    } catch { /* ignore */ }
  };

  const handleCancelBuild = async () => {
    try {
      const res = await fetch(`${API_BASE}/projects/${projectId}/build/cancel`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        addToast('Build cancelled', 'info');
        setShowCancelConfirm(false);
        // Refresh project data
        const updated = await fetch(`${API_BASE}/projects/${projectId}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (updated.ok) setProject(await updated.json());
      } else {
        addToast('Failed to cancel build');
      }
    } catch {
      addToast('Network error cancelling build');
    }
    setShowCancelConfirm(false);
  };

  const handleDeleteProject = async () => {
    setDeleting(true);
    try {
      const res = await fetch(`${API_BASE}/projects/${projectId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        addToast('Project removed', 'success');
        navigate('/');
      } else {
        const data = await res.json().catch(() => ({ detail: 'Failed to remove project' }));
        addToast(data.detail || 'Failed to remove project');
      }
    } catch {
      addToast('Network error removing project');
    } finally {
      setDeleting(false);
      setShowDeleteConfirm(false);
    }
  };

  if (loading) {
    return (
      <AppShell>
        <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
          <Skeleton style={{ width: '200px', height: '28px', marginBottom: '24px' }} />
          <Skeleton style={{ width: '100%', height: '120px', marginBottom: '16px' }} />
          <Skeleton style={{ width: '100%', height: '80px' }} />
        </div>
      </AppShell>
    );
  }

  if (!project) {
    return (
      <AppShell>
        <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto', color: '#94A3B8' }}>
          Project not found.
        </div>
      </AppShell>
    );
  }

  const buildActive = project.latest_build && ['pending', 'running'].includes(project.latest_build.status);

  return (
    <AppShell>
      <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px' }}>
          <button
            onClick={() => navigate('/')}
            style={{ background: 'transparent', color: '#94A3B8', border: '1px solid #334155', borderRadius: '6px', padding: '6px 12px', cursor: 'pointer', fontSize: '0.8rem' }}
          >
            Back
          </button>
          <h2 style={{ margin: 0, fontSize: '1.2rem' }}>{project.name}</h2>
          <span
            style={{
              padding: '2px 10px',
              borderRadius: '4px',
              background: '#1E293B',
              color: '#94A3B8',
              fontSize: '0.7rem',
              fontWeight: 700,
              textTransform: 'uppercase',
            }}
          >
            {project.status}
          </span>
          <div style={{ marginLeft: 'auto' }}>
            <button
              onClick={() => setShowDeleteConfirm(true)}
              style={{
                background: 'transparent',
                color: '#EF4444',
                border: '1px solid #EF4444',
                borderRadius: '6px',
                padding: '6px 14px',
                cursor: 'pointer',
                fontSize: '0.8rem',
                fontWeight: 600,
                transition: 'background 0.15s',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(239,68,68,0.1)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
            >
              Remove
            </button>
          </div>
        </div>

        {project.description && (
          <p style={{ color: '#94A3B8', fontSize: '0.85rem', marginBottom: '24px' }}>{project.description}</p>
        )}

        {/* Quick Links */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '12px', marginBottom: '24px' }}>
          <Link
            to={`/projects/${projectId}/build`}
            style={{
              background: '#1E293B',
              borderRadius: '8px',
              padding: '16px',
              textDecoration: 'none',
              color: '#F8FAFC',
              display: 'flex',
              flexDirection: 'column',
              gap: '8px',
              transition: 'background 0.15s',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = '#263348')}
            onMouseLeave={(e) => (e.currentTarget.style.background = '#1E293B')}
          >
            <span style={{ fontWeight: 600, fontSize: '0.85rem' }}>Build Progress</span>
            <span style={{ color: '#94A3B8', fontSize: '0.7rem' }}>
              {project.latest_build
                ? `${project.latest_build.phase} — ${project.latest_build.status}`
                : 'No builds yet'}
            </span>
          </Link>

          <div
            onClick={() => setContractsExpanded(!contractsExpanded)}
            style={{
              background: '#1E293B',
              borderRadius: '8px',
              padding: '16px',
              display: 'flex',
              flexDirection: 'column',
              gap: '8px',
              cursor: 'pointer',
              transition: 'background 0.15s',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = '#263348')}
            onMouseLeave={(e) => (e.currentTarget.style.background = '#1E293B')}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontWeight: 600, fontSize: '0.85rem' }}>Contracts</span>
              <span style={{ color: '#64748B', fontSize: '0.7rem', transition: 'transform 0.2s', transform: contractsExpanded ? 'rotate(180deg)' : 'rotate(0)' }}>▼</span>
            </div>
            <span style={{ color: '#94A3B8', fontSize: '0.7rem' }}>
              {project.contracts?.length ?? 0} generated
            </span>
          </div>

          <div
            style={{
              background: '#1E293B',
              borderRadius: '8px',
              padding: '16px',
              display: 'flex',
              flexDirection: 'column',
              gap: '8px',
            }}
          >
            <span style={{ fontWeight: 600, fontSize: '0.85rem' }}>Created</span>
            <span style={{ color: '#94A3B8', fontSize: '0.7rem' }}>
              {new Date(project.created_at).toLocaleDateString()}
            </span>
          </div>
        </div>

        {/* Contracts expanded panel */}
        {contractsExpanded && hasContracts && (
          <div style={{ background: '#1E293B', borderRadius: '8px', padding: '16px 20px', marginBottom: '16px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
              <h3 style={{ margin: 0, fontSize: '0.9rem' }}>Generated Contracts</h3>
              <button
                onClick={() => setShowRegenerate(true)}
                style={{
                  background: '#2563EB',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '6px',
                  padding: '6px 14px',
                  cursor: 'pointer',
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  transition: 'background 0.15s',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = '#1D4ED8')}
                onMouseLeave={(e) => (e.currentTarget.style.background = '#2563EB')}
              >
                ↻ Regenerate
              </button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              {project.contracts.map((c) => (
                <div
                  key={c.contract_type}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '6px 10px',
                    background: '#0F172A',
                    borderRadius: '4px',
                    fontSize: '0.78rem',
                  }}
                >
                  <span style={{ color: '#F8FAFC', textTransform: 'capitalize' }}>
                    {c.contract_type.replace(/_/g, ' ')}
                  </span>
                  <span style={{ color: '#64748B', fontSize: '0.7rem' }}>
                    v{c.version} · {new Date(c.updated_at).toLocaleDateString()}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* BYOK warning */}
        {hasContracts && !buildActive && !(user?.has_anthropic_key) && needsKeyBanner}

        {/* Build Actions */}
        <div style={{ display: 'flex', gap: '12px', marginBottom: '24px' }}>
          {!buildActive && (
            <button
              onClick={handleStartBuild}
              disabled={starting}
              style={{
                background: '#2563EB',
                color: '#fff',
                border: 'none',
                borderRadius: '6px',
                padding: '10px 20px',
                cursor: starting ? 'wait' : 'pointer',
                fontSize: '0.875rem',
                fontWeight: 600,
                opacity: starting ? 0.6 : 1,
              }}
            >
              {starting ? 'Starting...' : hasContracts ? 'Start Build' : 'Start Build — Begin Intake'}
            </button>
          )}
          {buildActive && (
            <>
              <button
                onClick={() => navigate(`/projects/${projectId}/build`)}
                style={{
                  background: '#2563EB',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '6px',
                  padding: '10px 20px',
                  cursor: 'pointer',
                  fontSize: '0.875rem',
                  fontWeight: 600,
                }}
              >
                View Build
              </button>
              <button
                onClick={() => setShowCancelConfirm(true)}
                style={{
                  background: 'transparent',
                  color: '#EF4444',
                  border: '1px solid #EF4444',
                  borderRadius: '6px',
                  padding: '10px 20px',
                  cursor: 'pointer',
                  fontSize: '0.875rem',
                  fontWeight: 600,
                }}
              >
                Cancel Build
              </button>
            </>
          )}
        </div>

        {/* Latest Build Summary */}
        {project.latest_build && (
          <div style={{ background: '#1E293B', borderRadius: '8px', padding: '16px 20px' }}>
            <h3 style={{ margin: '0 0 12px', fontSize: '0.9rem' }}>Latest Build</h3>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', fontSize: '0.8rem' }}>
              <div>
                <span style={{ color: '#94A3B8' }}>Phase: </span>
                {project.latest_build.phase}
              </div>
              <div>
                <span style={{ color: '#94A3B8' }}>Status: </span>
                {project.latest_build.status}
              </div>
              <div>
                <span style={{ color: '#94A3B8' }}>Loops: </span>
                {project.latest_build.loop_count}
              </div>
              {project.latest_build.started_at && (
                <div>
                  <span style={{ color: '#94A3B8' }}>Started: </span>
                  {new Date(project.latest_build.started_at).toLocaleString()}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {showCancelConfirm && (
        <ConfirmDialog
          title="Cancel Build"
          message="Are you sure you want to cancel the active build? This cannot be undone."
          confirmLabel="Cancel Build"
          onConfirm={handleCancelBuild}
          onCancel={() => setShowCancelConfirm(false)}
        />
      )}

      {showDeleteConfirm && (
        <ConfirmDialog
          title="Remove Project"
          message={`Are you sure you want to remove "${project.name}"? This will delete all contracts, builds, and questionnaire data. This cannot be undone.`}
          confirmLabel={deleting ? 'Removing...' : 'Remove Project'}
          onConfirm={handleDeleteProject}
          onCancel={() => setShowDeleteConfirm(false)}
        />
      )}

      {showQuestionnaire && project && (
        <QuestionnaireModal
          projectId={project.id}
          projectName={project.name}
          onClose={() => setShowQuestionnaire(false)}
          onContractsGenerated={handleContractsGenerated}
        />
      )}

      {showTargetPicker && (
        <BuildTargetModal
          onConfirm={handleTargetConfirm}
          onCancel={() => setShowTargetPicker(false)}
          starting={starting}
        />
      )}

      {showRegenerate && project && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0,0,0,0.65)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 100,
          }}
          onClick={() => {}}
        >
          <div
            style={{
              background: '#1E293B',
              borderRadius: '10px',
              display: 'flex',
              flexDirection: 'column',
              maxWidth: '560px',
              width: '95%',
              maxHeight: '80vh',
              overflow: 'hidden',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ padding: '16px 20px', borderBottom: '1px solid #334155', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <h3 style={{ margin: 0, fontSize: '1rem' }}>Regenerating Contracts</h3>
              <button
                onClick={() => { setShowRegenerate(false); }}
                style={{ background: 'transparent', border: 'none', color: '#94A3B8', fontSize: '1.2rem', cursor: 'pointer' }}
              >
                ✕
              </button>
            </div>
            <ContractProgress
              projectId={project.id}
              tokenUsage={{ input_tokens: 0, output_tokens: 0 }}
              model="claude-haiku-4-5"
              onComplete={async () => {
                setShowRegenerate(false);
                addToast('Contracts regenerated!', 'success');
                try {
                  const res = await fetch(`${API_BASE}/projects/${projectId}`, {
                    headers: { Authorization: `Bearer ${token}` },
                  });
                  if (res.ok) setProject(await res.json());
                } catch { /* ignore */ }
              }}
            />
          </div>
        </div>
      )}
    </AppShell>
  );
}

export default ProjectDetail;
