import { useState, useEffect, useMemo } from 'react';
import { useAuth } from '../context/AuthContext';

/** Extract a human-readable message from a FastAPI error response body. */
function extractError(body: Record<string, unknown>, fallback: string): string {
  const d = body.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) {
    return d.map((e: Record<string, unknown>) => String(e.msg ?? e.message ?? JSON.stringify(e))).join('; ');
  }
  return fallback;
}

const API_BASE = import.meta.env.VITE_API_URL ?? '';

type SourceType = 'github';
type GitHubMode = 'create' | 'select';

interface GitHubRepo {
  github_repo_id: number;
  full_name: string;
  default_branch: string;
  private: boolean;
  connected: boolean;
  repo_id: string | null;
}

interface CreateProjectModalProps {
  onClose: () => void;
  onCreated: (project: { id: string; name: string }) => void;
}

/* ------------------------------------------------------------------ */
/*  Shared inline styles                                              */
/* ------------------------------------------------------------------ */

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '8px 12px',
  background: '#0F172A',
  border: '1px solid #334155',
  borderRadius: '6px',
  color: '#F8FAFC',
  fontSize: '0.875rem',
  boxSizing: 'border-box',
};

const pillBase: React.CSSProperties = {
  flex: 1,
  padding: '6px 0',
  border: '1px solid #334155',
  background: 'transparent',
  color: '#94A3B8',
  fontSize: '0.8rem',
  cursor: 'pointer',
  transition: 'all 0.15s',
};

const pillActive: React.CSSProperties = {
  ...pillBase,
  background: '#2563EB',
  borderColor: '#2563EB',
  color: '#fff',
};

function Pill({
  active,
  onClick,
  children,
  leftRadius,
  rightRadius,
  testId,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  leftRadius?: boolean;
  rightRadius?: boolean;
  testId?: string;
}) {
  const radius = `${leftRadius ? '6px' : '0'} ${rightRadius ? '6px' : '0'} ${rightRadius ? '6px' : '0'} ${leftRadius ? '6px' : '0'}`;
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      style={{
        ...(active ? pillActive : pillBase),
        borderRadius: radius,
      }}
    >
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/*  Component                                                         */
/* ------------------------------------------------------------------ */

function CreateProjectModal({ onClose, onCreated }: CreateProjectModalProps) {
  const { token } = useAuth();

  /* form state */
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [buildMode, setBuildMode] = useState<'mini' | 'full'>('mini');
  const [source, setSource] = useState<SourceType>('github');
  const [ghMode, setGhMode] = useState<GitHubMode>('create');
  const [isPrivate, setIsPrivate] = useState(false);

  /* repo picker state (for "select existing") */
  const [repos, setRepos] = useState<GitHubRepo[]>([]);
  const [reposLoading, setReposLoading] = useState(false);
  const [repoSearch, setRepoSearch] = useState('');
  const [selectedRepo, setSelectedRepo] = useState<GitHubRepo | null>(null);

  /* submit state */
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  /* fetch repos when user switches to "select existing" */
  useEffect(() => {
    if (source !== 'github' || ghMode !== 'select') return;
    let cancelled = false;
    const fetchRepos = async () => {
      setReposLoading(true);
      try {
        const res = await fetch(`${API_BASE}/repos/all`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) throw new Error('Failed to load repos');
        const data = await res.json();
        if (!cancelled) setRepos(data.items);
      } catch {
        if (!cancelled) setError('Failed to load GitHub repos');
      } finally {
        if (!cancelled) setReposLoading(false);
      }
    };
    fetchRepos();
    return () => { cancelled = true; };
  }, [source, ghMode, token]);

  const filteredRepos = useMemo(() => {
    if (!repoSearch) return repos;
    const q = repoSearch.toLowerCase();
    return repos.filter((r) => r.full_name.toLowerCase().includes(q));
  }, [repos, repoSearch]);

  /* ---------------------------------------------------------------- */
  /*  Submit handler                                                  */
  /* ---------------------------------------------------------------- */

  const handleSubmit = async () => {
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError('Project name is required');
      return;
    }
    if (source === 'github' && ghMode === 'select' && !selectedRepo) {
      setError('Select a repository');
      return;
    }

    setLoading(true);
    setError('');

    try {
      let repoId: string | null = null;

      if (source === 'github') {
        if (ghMode === 'create') {
          /* Create a new GitHub repo and connect it */
          const repoRes = await fetch(`${API_BASE}/repos/create`, {
            method: 'POST',
            headers: {
              Authorization: `Bearer ${token}`,
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({
              name: trimmedName.replace(/\s+/g, '-').toLowerCase(),
              description: description.trim() || undefined,
              private: isPrivate,
            }),
          });
          if (!repoRes.ok) {
            const d = await repoRes.json().catch(() => ({}));
            setError(extractError(d, 'Failed to create GitHub repo'));
            setLoading(false);
            return;
          }
          const repoData = await repoRes.json();
          repoId = repoData.id;
        } else {
          /* Select existing repo */
          const repo = selectedRepo!;
          if (repo.connected && repo.repo_id) {
            repoId = repo.repo_id;
          } else {
            /* Connect it first */
            const connRes = await fetch(`${API_BASE}/repos/connect`, {
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
            if (!connRes.ok) {
              const d = await connRes.json().catch(() => ({}));
              setError(extractError(d, 'Failed to connect repo'));
              setLoading(false);
              return;
            }
            const connData = await connRes.json();
            repoId = connData.id;
          }
        }
      }

      /* Create the project */
      const res = await fetch(`${API_BASE}/projects`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          name: trimmedName,
          description: description.trim() || undefined,
          repo_id: repoId ?? undefined,
          build_mode: buildMode,
        }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        setError(extractError(d, 'Failed to create project'));
        setLoading(false);
        return;
      }
      const project = await res.json();
      onCreated(project);
    } catch {
      setError('Network error');
      setLoading(false);
    }
  };

  /* ---------------------------------------------------------------- */
  /*  Render                                                          */
  /* ---------------------------------------------------------------- */

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
      data-testid="create-project-overlay"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: '#1E293B',
          borderRadius: '8px',
          padding: '24px',
          maxWidth: '480px',
          width: '90%',
          maxHeight: '85vh',
          overflowY: 'auto',
        }}
        data-testid="create-project-modal"
      >
        <h3 style={{ margin: '0 0 16px 0', fontSize: '1rem', color: '#F8FAFC' }}>
          Create Project
        </h3>

        {/* ---- Name ---- */}
        <label style={{ display: 'block', marginBottom: '12px' }}>
          <span style={{ display: 'block', color: '#94A3B8', fontSize: '0.8rem', marginBottom: '4px' }}>
            Name *
          </span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="My Awesome App"
            maxLength={255}
            autoFocus
            data-testid="project-name-input"
            style={inputStyle}
          />
        </label>

        {/* ---- Description ---- */}
        <label style={{ display: 'block', marginBottom: '16px' }}>
          <span style={{ display: 'block', color: '#94A3B8', fontSize: '0.8rem', marginBottom: '4px' }}>
            Description (optional)
          </span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Brief project description..."
            rows={3}
            maxLength={2000}
            data-testid="project-desc-input"
            style={{ ...inputStyle, resize: 'vertical', fontFamily: 'inherit' }}
          />
          {description.length > 0 && (
            <span style={{ display: 'block', textAlign: 'right', color: description.length > 1800 ? '#FBBF24' : '#64748B', fontSize: '0.7rem', marginTop: '2px' }}>
              {description.length}/2000
            </span>
          )}
        </label>

        {/* ---- Build Mode toggle ---- */}
        <div style={{ marginBottom: '14px' }}>
          <span style={{ display: 'block', color: '#94A3B8', fontSize: '0.8rem', marginBottom: '6px' }}>
            Build Mode
          </span>
          <div style={{ display: 'flex' }}>
            <Pill active={buildMode === 'mini'} onClick={() => setBuildMode('mini')} leftRadius testId="mode-mini">
              ⚡ Mini
            </Pill>
            <Pill active={buildMode === 'full'} onClick={() => setBuildMode('full')} rightRadius testId="mode-full">
              🔮 Full
            </Pill>
          </div>
          <span style={{ display: 'block', color: '#64748B', fontSize: '0.7rem', marginTop: '4px' }}>
            {buildMode === 'mini'
              ? 'Quick PoC — 2 questions, 2 phases (backend + frontend). ~$1-3.'
              : 'Production build — 7 sections, 6-12 phases. ~$5-20.'}
          </span>
        </div>

        {/* ---- GitHub options ---- */}
        <div style={{ marginBottom: '14px' }}>
            <div style={{ display: 'flex', marginBottom: '10px' }}>
              <Pill active={ghMode === 'create'} onClick={() => { setGhMode('create'); setSelectedRepo(null); }} leftRadius testId="gh-create">
                Create New
              </Pill>
              <Pill active={ghMode === 'select'} onClick={() => setGhMode('select')} rightRadius testId="gh-select">
                Use Existing
              </Pill>
            </div>

            {ghMode === 'create' && (
              <label
                style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}
                data-testid="private-toggle"
              >
                <input
                  type="checkbox"
                  checked={isPrivate}
                  onChange={(e) => setIsPrivate(e.target.checked)}
                  style={{ accentColor: '#2563EB' }}
                />
                <span style={{ color: '#94A3B8', fontSize: '0.8rem' }}>Private repository</span>
              </label>
            )}

            {ghMode === 'select' && (
              <div>
                <input
                  type="text"
                  placeholder="Search repos..."
                  value={repoSearch}
                  onChange={(e) => setRepoSearch(e.target.value)}
                  data-testid="repo-search-input"
                  style={{ ...inputStyle, marginBottom: '8px' }}
                />
                <div
                  style={{
                    maxHeight: '180px',
                    overflowY: 'auto',
                    border: '1px solid #334155',
                    borderRadius: '6px',
                    background: '#0F172A',
                  }}
                  data-testid="repo-list"
                >
                  {reposLoading ? (
                    <p style={{ color: '#94A3B8', textAlign: 'center', padding: '12px', margin: 0, fontSize: '0.8rem' }}>
                      Loading repos...
                    </p>
                  ) : filteredRepos.length === 0 ? (
                    <p style={{ color: '#94A3B8', textAlign: 'center', padding: '12px', margin: 0, fontSize: '0.8rem' }}>
                      No repos found
                    </p>
                  ) : (
                    filteredRepos.map((repo) => {
                      const isSelected = selectedRepo?.github_repo_id === repo.github_repo_id;
                      return (
                        <div
                          key={repo.github_repo_id}
                          onClick={() => setSelectedRepo(repo)}
                          data-testid={`repo-option-${repo.github_repo_id}`}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            padding: '8px 10px',
                            cursor: 'pointer',
                            borderBottom: '1px solid #1E293B',
                            background: isSelected ? 'rgba(37,99,235,0.15)' : 'transparent',
                          }}
                        >
                          <div>
                            <div style={{ fontSize: '0.825rem', color: '#F8FAFC' }}>{repo.full_name}</div>
                            <div style={{ color: '#64748B', fontSize: '0.7rem' }}>
                              {repo.private ? 'Private' : 'Public'}
                              {repo.connected && <span style={{ color: '#22C55E', marginLeft: '6px' }}>● Connected</span>}
                            </div>
                          </div>
                          <div
                            style={{
                              width: '16px',
                              height: '16px',
                              borderRadius: '50%',
                              border: isSelected ? '5px solid #2563EB' : '2px solid #475569',
                              boxSizing: 'border-box',
                              flexShrink: 0,
                            }}
                          />
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* ---- Error ---- */}
        {error && (
          <p style={{ color: '#EF4444', fontSize: '0.8rem', margin: '0 0 12px 0' }} data-testid="create-error">
            {error}
          </p>
        )}

        {/* ---- Actions ---- */}
        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
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
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading}
            data-testid="create-project-submit"
            style={{
              background: '#2563EB',
              color: '#fff',
              border: 'none',
              borderRadius: '6px',
              padding: '8px 16px',
              cursor: loading ? 'not-allowed' : 'pointer',
              fontSize: '0.875rem',
              opacity: loading ? 0.6 : 1,
            }}
          >
            {loading ? 'Creating...' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default CreateProjectModal;
