/**
 * ProjectDetail -- overview page for a single project.
 * Links to questionnaire, contracts, and build progress.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import AppShell from '../components/AppShell';
import Skeleton from '../components/Skeleton';
import ConfirmDialog from '../components/ConfirmDialog';
import QuestionnaireModal from '../components/QuestionnaireModal';
import ContractProgress from '../components/ContractProgress';
import BuildTargetModal, { type BuildTarget } from '../components/BuildTargetModal';
import BranchPickerModal, { type BranchChoice } from '../components/BranchPickerModal';
import CertificateModal from '../components/CertificateModal';
import { useWebSocket } from '../hooks/useWebSocket';

const BG_TOTAL_CONTRACTS = 9;

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
  build_mode?: string;
  repo_id: string | null;
  repo_full_name: string | null;
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

  /* Contract version history */
  const [versionHistory, setVersionHistory] = useState<{batch: number; created_at: string; count: number}[]>([]);
  const [expandedBatch, setExpandedBatch] = useState<number | null>(null);
  const [batchContracts, setBatchContracts] = useState<Record<number, {contract_type: string; content: string}[]>>({});
  const [historyExpanded, setHistoryExpanded] = useState(false);
  const [loadingBatch, setLoadingBatch] = useState<number | null>(null);
  const [showBranchPicker, setShowBranchPicker] = useState(false);
  const [selectedBranch, setSelectedBranch] = useState('main');
  const [selectedContractBatch, setSelectedContractBatch] = useState<number | null>(null);
  const [showCertificate, setShowCertificate] = useState(false);

  /* Background contract generation tracking */
  const [bgGenActive, setBgGenActive] = useState(false);
  const [bgGenDone, setBgGenDone] = useState<string[]>([]);
  const genStartedRef = useRef(false);

  /* Workspace-ready gate: holds the build ID we're waiting on before navigating */
  const pendingBuildIdRef = useRef<string | null>(null);

  /* Dynamic loading status text shown on the 3-D orb while starting */
  const [loadingStatus, setLoadingStatus] = useState<string | undefined>(undefined);

  /* Phase 58: Build cycle state */
  interface BuildCycle {
    id: string;
    status: string;
    branch_name: string;
    baseline_sha: string | null;
    created_at: string;
    sealed_at: string | null;
  }
  const [buildCycle, setBuildCycle] = useState<BuildCycle | null>(null);

  /* Build history */
  interface BuildHistoryItem {
    id: string;
    phase: string;
    status: string;
    branch: string;
    loop_count: number;
    started_at: string | null;
    completed_at: string | null;
    created_at: string;
    error_detail: string | null;
  }
  const [buildHistory, setBuildHistory] = useState<BuildHistoryItem[]>([]);
  const [selectMode, setSelectMode] = useState(false);
  const [selectedBuilds, setSelectedBuilds] = useState<Set<string>>(new Set());
  const [deletingBuilds, setDeletingBuilds] = useState(false);
  const [showDeleteBuildsConfirm, setShowDeleteBuildsConfirm] = useState(false);

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

  /* Fetch build history */
  useEffect(() => {
    const fetchBuilds = async () => {
      try {
        const res = await fetch(`${API_BASE}/projects/${projectId}/builds`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const data = await res.json();
          setBuildHistory(data.items || []);
        }
      } catch { /* ignore */ }
    };
    if (projectId && token) fetchBuilds();
  }, [projectId, token]);

  /* Phase 58: Fetch active build cycle */
  useEffect(() => {
    const fetchCycle = async () => {
      try {
        const res = await fetch(`${API_BASE}/projects/${projectId}/build-cycle`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const data = await res.json();
          setBuildCycle(data.cycle ?? null);
        }
      } catch { /* ignore */ }
    };
    if (projectId && token) fetchCycle();
  }, [projectId, token]);

  const hasContracts = (project?.contracts?.length ?? 0) > 0;

  /* Listen for WS events: contract_progress + workspace_ready + build_log */
  useWebSocket(
    useCallback(
      (data: { type: string; payload: any }) => {
        /* ── workspace_ready: contracts are in git, safe to navigate ── */
        if (data.type === 'workspace_ready') {
          const pending = pendingBuildIdRef.current;
          if (pending && data.payload?.id === pending) {
            setLoadingStatus('Workspace ready!');
            // Brief delay so user sees the "ready" message before navigating
            setTimeout(() => {
              pendingBuildIdRef.current = null;
              setStarting(false);
              setShowBranchPicker(false);
              setShowTargetPicker(false);
              setLoadingStatus(undefined);
              navigate(`/projects/${projectId}/build`);
            }, 600);
          }
          return;
        }

        /* ── build_log: update loading status while workspace is syncing ── */
        if (data.type === 'build_log' && pendingBuildIdRef.current) {
          const msg = data.payload?.message;
          if (typeof msg === 'string' && msg.length > 0) {
            // Show user-friendly workspace setup messages
            if (msg.includes('Created GitHub repo'))   setLoadingStatus('Repository created…');
            else if (msg.includes('Cloned'))            setLoadingStatus('Repository cloned…');
            else if (msg.includes('branch'))            setLoadingStatus('Switching branch…');
            else if (msg.includes('Pushed Forge'))      setLoadingStatus('Contracts synced…');
          }
          return;
        }

        /* ── contract_progress (background tracking) ── */
        if (data.type !== 'contract_progress') return;
        const p = data.payload;
        if (p.project_id !== projectId) return;

        if (p.status === 'generating') {
          genStartedRef.current = true;
        } else if (p.status === 'done') {
          setBgGenDone((prev) => (prev.includes(p.contract_type) ? prev : [...prev, p.contract_type]));
        } else if (p.status === 'cancelled') {
          genStartedRef.current = false;
          setBgGenActive(false);
        }
      },
      [projectId, addToast, navigate],
    ),
  );

  /* Auto-complete: when all contracts done in background, refresh & deactivate */
  useEffect(() => {
    if (bgGenActive && bgGenDone.length >= BG_TOTAL_CONTRACTS) {
      setBgGenActive(false);
      setBgGenDone([]);
      genStartedRef.current = false;
      addToast('Contracts generated!', 'success');
      /* Refresh project data */
      (async () => {
        try {
          const res = await fetch(`${API_BASE}/projects/${projectId}`, {
            headers: { Authorization: `Bearer ${token}` },
          });
          if (res.ok) setProject(await res.json());
        } catch { /* ignore */ }
      })();
    }
  }, [bgGenActive, bgGenDone, projectId, token, addToast]);

  const handleStartBuild = async () => {
    /* If no contracts exist, open the questionnaire first */
    if (!hasContracts) {
      setShowQuestionnaire(true);
      return;
    }
    /* Pre-fetch contract version history so BranchPickerModal knows
       whether to show the version selection step */
    if (versionHistory.length === 0) {
      try {
        const hres = await fetch(`${API_BASE}/projects/${projectId}/contracts/history`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (hres.ok) {
          const data = await hres.json();
          setVersionHistory(data.items || []);
        }
      } catch { /* ignore */ }
    }
    /* Show branch picker first */
    setShowBranchPicker(true);
  };

  const handleBranchConfirm = async (choice: BranchChoice) => {
    setSelectedBranch(choice.branch);
    setSelectedContractBatch(choice.contractBatch);
    /* Keep BranchPicker open — it shows the sync spinner while starting=true */
    /* If a repo is already connected, build straight into it */
    if (project?.repo_id && project?.repo_full_name) {
      await handleTargetConfirm({
        target_type: 'github_existing',
        target_ref: project.repo_full_name,
      }, choice.branch, choice.contractBatch);
      return;
    }
    /* No repo connected — close picker, show target picker modal */
    setShowBranchPicker(false);
    setShowTargetPicker(true);
  };

  const handleTargetConfirm = async (target: BuildTarget, branch?: string, contractBatch?: number | null) => {
    setStarting(true);
    setLoadingStatus('Queuing build…');
    let keepStarting = false;
    try {
      const res = await fetch(`${API_BASE}/projects/${projectId}/build`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          ...target,
          branch: branch ?? selectedBranch,
          contract_batch: contractBatch ?? selectedContractBatch ?? null,
        }),
      });
      if (res.ok) {
        const build = await res.json().catch(() => null);
        if (build?.id) {
          // Don't navigate yet — wait for workspace_ready WS event.
          // Keep starting=true so the BranchPicker shows the 3-D orb loader.
          // NOTE: `finally` always runs in JS even after `return`, so we use
          // a flag to prevent it from resetting `starting`.
          keepStarting = true;
          pendingBuildIdRef.current = build.id;
          setLoadingStatus('Preparing workspace…');
          addToast('Build queued — syncing workspace…', 'success');
          // Safety timeout: navigate after 30s even if WS event is lost
          setTimeout(() => {
            if (pendingBuildIdRef.current === build.id) {
              pendingBuildIdRef.current = null;
              setStarting(false);
              setLoadingStatus(undefined);
              setShowBranchPicker(false);
              setShowTargetPicker(false);
              navigate(`/projects/${projectId}/build`);
            }
          }, 30_000);
          return;
        } else {
          // Fallback: navigate immediately if we can't parse the build ID
          setShowTargetPicker(false);
          setShowBranchPicker(false);
          addToast('Build started', 'success');
          navigate(`/projects/${projectId}/build`);
        }
      } else {
        const data = await res.json().catch(() => ({ detail: 'Failed to start build' }));
        addToast(data.detail || 'Failed to start build');
      }
    } catch {
      addToast('Network error starting build');
    } finally {
      if (!keepStarting) {
        setStarting(false);
        setLoadingStatus(undefined);
      }
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
    /* Refresh version history (a snapshot was just created) */
    try {
      const hres = await fetch(`${API_BASE}/projects/${projectId}/contracts/history`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (hres.ok) {
        const data = await hres.json();
        setVersionHistory(data.items || []);
      }
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

  const handleDeleteBuilds = async () => {
    if (selectedBuilds.size === 0) return;
    setDeletingBuilds(true);
    try {
      const res = await fetch(`${API_BASE}/projects/${projectId}/builds`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ build_ids: Array.from(selectedBuilds) }),
      });
      if (res.ok) {
        const data = await res.json();
        addToast(`Deleted ${data.deleted} build${data.deleted === 1 ? '' : 's'}`, 'success');
        setBuildHistory((prev) => prev.filter((b) => !selectedBuilds.has(b.id)));
        setSelectedBuilds(new Set());
        setSelectMode(false);
      } else {
        const data = await res.json().catch(() => ({ detail: 'Failed to delete builds' }));
        addToast(data.detail || 'Failed to delete builds');
      }
    } catch {
      addToast('Network error deleting builds');
    } finally {
      setDeletingBuilds(false);
      setShowDeleteBuildsConfirm(false);
    }
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
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
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
            <span style={{ fontWeight: 600, fontSize: '0.85rem' }}>Builds</span>
            <span style={{ color: '#94A3B8', fontSize: '0.7rem' }}>
              {buildHistory.length} total
            </span>
          </div>

          <div
            onClick={() => setShowCertificate(true)}
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
            <span style={{ fontWeight: 600, fontSize: '0.85rem' }}>🛡️ Forge Seal</span>
            <span style={{ color: '#94A3B8', fontSize: '0.7rem' }}>
              Build certificate
            </span>
          </div>
        </div>

        {/* Background generation progress bar */}
        {bgGenActive && !showRegenerate && !showQuestionnaire && (
          <div
            onClick={() => setShowQuestionnaire(true)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setShowQuestionnaire(true); }}
            style={{
              background: '#1E293B',
              borderRadius: '8px',
              padding: '14px 20px',
              marginBottom: '16px',
              display: 'flex',
              flexDirection: 'column',
              gap: '8px',
              cursor: 'pointer',
              transition: 'background 0.15s',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = '#253348')}
            onMouseLeave={(e) => (e.currentTarget.style.background = '#1E293B')}
            title="Click to view progress"
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#F8FAFC', display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span className="typing-dots">⏳</span> Generating contracts… {bgGenDone.length}/{BG_TOTAL_CONTRACTS}
              </span>
              <span style={{ fontSize: '0.7rem', color: '#64748B' }}>
                {Math.round((bgGenDone.length / BG_TOTAL_CONTRACTS) * 100)}%
              </span>
            </div>
            <div
              style={{
                height: '6px',
                background: '#0F172A',
                borderRadius: '3px',
                overflow: 'hidden',
              }}
            >
              <div
                style={{
                  width: `${(bgGenDone.length / BG_TOTAL_CONTRACTS) * 100}%`,
                  height: '100%',
                  background: bgGenDone.length >= BG_TOTAL_CONTRACTS ? '#22C55E' : '#2563EB',
                  borderRadius: '3px',
                  transition: 'width 0.5s ease',
                }}
              />
            </div>
          </div>
        )}

        {/* Contracts expanded panel */}
        {contractsExpanded && hasContracts && (
          <div style={{ background: '#1E293B', borderRadius: '8px', padding: '16px 20px', marginBottom: '16px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
              <h3 style={{ margin: 0, fontSize: '0.9rem' }}>Generated Contracts</h3>
              <div style={{ display: 'flex', gap: '8px' }}>
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

            {/* Version History */}
            <div style={{ marginTop: '12px', borderTop: '1px solid #334155', paddingTop: '10px' }}>
              <div
                onClick={async () => {
                  const next = !historyExpanded;
                  setHistoryExpanded(next);
                  if (next && versionHistory.length === 0) {
                    try {
                      const res = await fetch(`${API_BASE}/projects/${projectId}/contracts/history`, {
                        headers: { Authorization: `Bearer ${token}` },
                      });
                      if (res.ok) {
                        const data = await res.json();
                        setVersionHistory(data.items || []);
                      }
                    } catch { /* ignore */ }
                  }
                }}
                style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer', padding: '4px 0' }}
              >
                <span style={{ fontSize: '0.78rem', fontWeight: 600, color: '#94A3B8' }}>Previous Versions</span>
                <span style={{ color: '#64748B', fontSize: '0.7rem', transition: 'transform 0.2s', transform: historyExpanded ? 'rotate(180deg)' : 'rotate(0)' }}>▼</span>
              </div>
              {historyExpanded && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '8px' }}>
                  {versionHistory.length === 0 && (
                    <span style={{ color: '#475569', fontSize: '0.75rem', fontStyle: 'italic' }}>No previous versions</span>
                  )}
                  {versionHistory.map((v) => (
                    <div key={v.batch} style={{ background: '#0F172A', borderRadius: '6px', overflow: 'hidden' }}>
                      <div
                        onClick={async () => {
                          if (expandedBatch === v.batch) {
                            setExpandedBatch(null);
                            return;
                          }
                          setExpandedBatch(v.batch);
                          if (!batchContracts[v.batch]) {
                            setLoadingBatch(v.batch);
                            try {
                              const res = await fetch(`${API_BASE}/projects/${projectId}/contracts/history/${v.batch}`, {
                                headers: { Authorization: `Bearer ${token}` },
                              });
                              if (res.ok) {
                                const data = await res.json();
                                setBatchContracts((prev) => ({ ...prev, [v.batch]: data.items || [] }));
                              }
                            } catch { /* ignore */ }
                            setLoadingBatch(null);
                          }
                        }}
                        style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 10px', cursor: 'pointer' }}
                      >
                        <span style={{ fontSize: '0.78rem', fontWeight: 600, color: '#F8FAFC' }}>V{v.batch}</span>
                        <span style={{ color: '#64748B', fontSize: '0.7rem' }}>
                          {v.count} contracts · {new Date(v.created_at).toLocaleDateString()}
                          {' '}{expandedBatch === v.batch ? '▲' : '▼'}
                        </span>
                      </div>
                      {expandedBatch === v.batch && (
                        <div style={{ padding: '0 10px 8px', display: 'flex', flexDirection: 'column', gap: '3px' }}>
                          {loadingBatch === v.batch && <span style={{ color: '#64748B', fontSize: '0.7rem' }}>Loading...</span>}
                          {(batchContracts[v.batch] || []).map((c) => (
                            <div
                              key={c.contract_type}
                              style={{
                                display: 'flex',
                                justifyContent: 'space-between',
                                alignItems: 'center',
                                padding: '4px 8px',
                                background: '#1E293B',
                                borderRadius: '3px',
                                fontSize: '0.72rem',
                              }}
                            >
                              <span style={{ color: '#CBD5E1', textTransform: 'capitalize' }}>
                                {c.contract_type.replace(/_/g, ' ')}
                              </span>
                              <span style={{ color: '#475569', fontSize: '0.65rem' }}>
                                {c.content.length.toLocaleString()} chars
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* BYOK warning */}
        {hasContracts && !buildActive && !(user?.has_anthropic_key) && needsKeyBanner}

        {/* Phase 58: Active Build Cycle Banner */}
        {buildCycle && buildCycle.status === 'active' && (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            background: '#1E293B',
            border: '1px solid #3B82F6',
            borderRadius: '8px',
            padding: '10px 16px',
            marginBottom: '16px',
            fontSize: '0.8rem',
          }}>
            <span style={{ fontSize: '1rem' }}>🔄</span>
            <div style={{ flex: 1 }}>
              <span style={{ color: '#93C5FD', fontWeight: 600 }}>Build Cycle Active</span>
              <span style={{ color: '#64748B', marginLeft: '8px', fontFamily: 'monospace', fontSize: '0.72rem' }}>
                {buildCycle.branch_name}
              </span>
              {buildCycle.baseline_sha && (
                <span style={{ color: '#64748B', marginLeft: '8px', fontFamily: 'monospace', fontSize: '0.72rem' }}>
                  baseline {buildCycle.baseline_sha.slice(0, 8)}
                </span>
              )}
              <div style={{ color: '#94A3B8', fontSize: '0.7rem', marginTop: '2px' }}>
                Started {new Date(buildCycle.created_at).toLocaleString()}
              </div>
            </div>
          </div>
        )}
        {buildCycle && buildCycle.status === 'sealed' && (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            background: '#052E16',
            border: '1px solid #22C55E',
            borderRadius: '8px',
            padding: '10px 16px',
            marginBottom: '16px',
            fontSize: '0.8rem',
          }}>
            <span style={{ fontSize: '1rem' }}>🛡️</span>
            <div style={{ flex: 1 }}>
              <span style={{ color: '#4ADE80', fontWeight: 600 }}>Build Cycle Sealed</span>
              <span style={{ color: '#86EFAC', marginLeft: '8px', fontFamily: 'monospace', fontSize: '0.72rem' }}>
                {buildCycle.branch_name}
              </span>
              {buildCycle.sealed_at && (
                <div style={{ color: '#6EE7B7', fontSize: '0.7rem', marginTop: '2px' }}>
                  Sealed {new Date(buildCycle.sealed_at).toLocaleString()}
                </div>
              )}
            </div>
          </div>
        )}

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

        {/* Build History */}
        {buildHistory.length > 0 && (
          <div style={{ background: '#1E293B', borderRadius: '8px', padding: '16px 20px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
              <h3 style={{ margin: 0, fontSize: '0.9rem' }}>Build History</h3>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                {selectMode && selectedBuilds.size > 0 && (
                  <button
                    onClick={() => setShowDeleteBuildsConfirm(true)}
                    disabled={deletingBuilds}
                    style={{
                      background: '#7F1D1D',
                      border: '1px solid #EF4444',
                      borderRadius: '4px',
                      color: '#FCA5A5',
                      padding: '3px 10px',
                      fontSize: '0.7rem',
                      cursor: deletingBuilds ? 'wait' : 'pointer',
                      opacity: deletingBuilds ? 0.6 : 1,
                      fontWeight: 500,
                    }}
                  >
                    {deletingBuilds ? 'Deleting...' : `Delete ${selectedBuilds.size}`}
                  </button>
                )}
                <button
                  onClick={() => {
                    setSelectMode((prev) => !prev);
                    setSelectedBuilds(new Set());
                  }}
                  style={{
                    background: selectMode ? '#1E3A5F' : 'transparent',
                    border: selectMode ? '1px solid #3B82F6' : '1px solid #334155',
                    borderRadius: '4px',
                    color: selectMode ? '#93C5FD' : '#94A3B8',
                    padding: '3px 10px',
                    fontSize: '0.7rem',
                    cursor: 'pointer',
                    fontWeight: 500,
                    transition: 'all 0.15s',
                  }}
                >
                  {selectMode ? 'Done' : 'Select'}
                </button>
              </div>
            </div>
            {selectMode && buildHistory.filter((b) => b.status !== 'running' && b.status !== 'pending').length > 1 && (
              <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
                <button
                  onClick={() => {
                    const eligible = buildHistory.filter((b) => b.status !== 'running' && b.status !== 'pending');
                    setSelectedBuilds(new Set(eligible.map((b) => b.id)));
                  }}
                  style={{ background: 'transparent', border: 'none', color: '#60A5FA', fontSize: '0.68rem', cursor: 'pointer', padding: 0 }}
                >
                  Select all
                </button>
                {selectedBuilds.size > 0 && (
                  <button
                    onClick={() => setSelectedBuilds(new Set())}
                    style={{ background: 'transparent', border: 'none', color: '#64748B', fontSize: '0.68rem', cursor: 'pointer', padding: 0 }}
                  >
                    Clear
                  </button>
                )}
              </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              {buildHistory.map((b, idx) => {
                const statusColors: Record<string, string> = {
                  completed: '#22C55E',
                  running: '#3B82F6',
                  pending: '#F59E0B',
                  failed: '#EF4444',
                  cancelled: '#94A3B8',
                  paused: '#A855F7',
                };
                const statusColor = statusColors[b.status] || '#94A3B8';
                const isActive = b.status === 'running' || b.status === 'pending';
                const isSelected = selectedBuilds.has(b.id);
                return (
                  <div
                    key={b.id}
                    onClick={() => {
                      if (selectMode) {
                        if (isActive) return; // can't select active builds
                        setSelectedBuilds((prev) => {
                          const next = new Set(prev);
                          if (next.has(b.id)) next.delete(b.id);
                          else next.add(b.id);
                          return next;
                        });
                      } else {
                        navigate(`/projects/${projectId}/build?buildId=${b.id}`);
                      }
                    }}
                    style={{
                      display: 'grid',
                      gridTemplateColumns: selectMode ? 'auto auto 1fr auto' : 'auto 1fr auto',
                      gap: '12px',
                      alignItems: 'center',
                      padding: '10px 12px',
                      background: isSelected ? '#1E293B' : '#0F172A',
                      borderRadius: '6px',
                      cursor: selectMode && isActive ? 'not-allowed' : 'pointer',
                      fontSize: '0.78rem',
                      transition: 'background 0.15s',
                      opacity: selectMode && isActive ? 0.5 : 1,
                      border: isSelected ? '1px solid #3B82F6' : '1px solid transparent',
                    }}
                    onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = '#1A2740'; }}
                    onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = '#0F172A'; }}
                  >
                    {selectMode && (
                      <span
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          width: '16px',
                          height: '16px',
                          borderRadius: '3px',
                          border: isActive ? '1px solid #334155' : isSelected ? '1px solid #3B82F6' : '1px solid #475569',
                          background: isSelected ? '#3B82F6' : 'transparent',
                          fontSize: '0.6rem',
                          color: '#fff',
                          flexShrink: 0,
                        }}
                      >
                        {isSelected && '✓'}
                      </span>
                    )}
                    <span style={{ color: '#64748B', fontWeight: 600, fontSize: '0.7rem', minWidth: '24px' }}>
                      #{buildHistory.length - idx}
                    </span>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span
                          style={{
                            display: 'inline-block',
                            width: '7px',
                            height: '7px',
                            borderRadius: '50%',
                            background: statusColor,
                            flexShrink: 0,
                          }}
                        />
                        <span style={{ color: '#F8FAFC', fontWeight: 500 }}>{b.phase}</span>
                        <span style={{ color: '#64748B', fontSize: '0.7rem' }}>
                          {b.status}
                        </span>
                      </div>
                      <div style={{ display: 'flex', gap: '12px', color: '#64748B', fontSize: '0.7rem' }}>
                        <span>🌿 {b.branch || 'main'}</span>
                        <span>{b.loop_count} loops</span>
                      </div>
                    </div>
                    <span style={{ color: '#64748B', fontSize: '0.68rem', whiteSpace: 'nowrap' }}>
                      {new Date(b.created_at).toLocaleDateString()}{' '}
                      {new Date(b.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </span>
                  </div>
                );
              })}
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

      {showDeleteBuildsConfirm && (
        <ConfirmDialog
          title="Delete Builds"
          message={`Delete ${selectedBuilds.size} selected build${selectedBuilds.size === 1 ? '' : 's'}? All logs and cost data will be permanently removed.`}
          confirmLabel={deletingBuilds ? 'Deleting...' : `Delete ${selectedBuilds.size}`}
          onConfirm={handleDeleteBuilds}
          onCancel={() => setShowDeleteBuildsConfirm(false)}
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
          buildMode={project.build_mode === 'mini' ? 'mini' : 'full'}
          initialGenerating={bgGenActive}
          initialDoneContracts={bgGenDone}
          onClose={() => setShowQuestionnaire(false)}
          onContractsGenerated={handleContractsGenerated}
          onDismissDuringGeneration={() => {
            setBgGenActive(true);
          }}
        />
      )}

      {showTargetPicker && (
        <BuildTargetModal
          onConfirm={handleTargetConfirm}
          onCancel={() => setShowTargetPicker(false)}
          starting={starting}
        />
      )}

      {showBranchPicker && (
        <BranchPickerModal
          onConfirm={handleBranchConfirm}
          onCancel={() => setShowBranchPicker(false)}
          repoConnected={!!(project?.repo_id && project?.repo_full_name)}
          starting={starting}
          loadingStatus={loadingStatus}
          contractVersions={versionHistory}
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
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <span style={{ color: '#64748B', fontSize: '0.65rem', whiteSpace: 'nowrap', display: 'flex', alignItems: 'center', gap: '4px' }}>
                  Continues in background <span style={{ fontSize: '0.8rem' }}>→</span>
                </span>
                <button
                  onClick={() => {
                    setShowRegenerate(false);
                    setBgGenDone([]);
                    setBgGenActive(true);
                  }}
                  style={{ background: 'transparent', border: 'none', color: '#94A3B8', fontSize: '1.2rem', cursor: 'pointer' }}
                >
                  ✕
                </button>
              </div>
            </div>
            <ContractProgress
              projectId={project.id}
              tokenUsage={{ input_tokens: 0, output_tokens: 0 }}
              model="claude-sonnet-4-5"
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
      {showCertificate && project && (
        <CertificateModal
          projectId={project.id}
          token={token!}
          onClose={() => setShowCertificate(false)}
        />
      )}
    </AppShell>
  );
}

export default ProjectDetail;
