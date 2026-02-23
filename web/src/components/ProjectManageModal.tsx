import { useState } from 'react';

interface ProjectManageModalProps {
  projectName: string;
  hasRepo: boolean;
  loading: boolean;
  onRestart: (deleteRepo: boolean) => void;
  onRemove: (deleteRepo: boolean) => void;
  onCancel: () => void;
}

type ModalView = 'main' | 'restart' | 'remove';

function ProjectManageModal({
  projectName,
  hasRepo,
  loading,
  onRestart,
  onRemove,
  onCancel,
}: ProjectManageModalProps) {
  const [view, setView] = useState<ModalView>('main');
  const [confirmText, setConfirmText] = useState('');
  const [deleteRepo, setDeleteRepo] = useState(false);

  const overlay: React.CSSProperties = {
    position: 'fixed',
    inset: 0,
    backgroundColor: 'rgba(0,0,0,0.6)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 100,
  };

  const card: React.CSSProperties = {
    background: '#1E293B',
    borderRadius: '8px',
    padding: '24px',
    maxWidth: '460px',
    width: '90%',
  };

  const btnBase: React.CSSProperties = {
    border: 'none',
    borderRadius: '6px',
    padding: '8px 16px',
    cursor: loading ? 'not-allowed' : 'pointer',
    fontSize: '0.875rem',
    fontWeight: 600,
    opacity: loading ? 0.6 : 1,
    transition: 'opacity 0.15s',
  };

  const btnGhost: React.CSSProperties = {
    ...btnBase,
    background: 'transparent',
    color: '#94A3B8',
    border: '1px solid #334155',
  };

  const btnAmber: React.CSSProperties = {
    ...btnBase,
    background: '#F59E0B',
    color: '#000',
  };

  const btnRed: React.CSSProperties = {
    ...btnBase,
    background: '#EF4444',
    color: '#fff',
  };

  const subtitle: React.CSSProperties = {
    color: '#94A3B8',
    margin: '0 0 20px 0',
    fontSize: '0.85rem',
    lineHeight: 1.5,
  };

  // ── Main view: choose action ──
  if (view === 'main') {
    return (
      <div style={overlay} onClick={onCancel}>
        <div onClick={(e) => e.stopPropagation()} style={card}>
          <h3 style={{ margin: '0 0 8px 0', fontSize: '1rem' }}>Manage Project</h3>
          <p style={subtitle}>
            Choose an action for &ldquo;{projectName}&rdquo;.
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {/* Restart */}
            <button
              onClick={() => setView('restart')}
              style={{
                ...btnBase,
                background: '#1E3A5F',
                color: '#60A5FA',
                border: '1px solid #2563EB44',
                textAlign: 'left',
                padding: '12px 16px',
              }}
            >
              <div style={{ fontWeight: 600 }}>Restart Build</div>
              <div style={{ fontSize: '0.75rem', color: '#94A3B8', marginTop: '2px' }}>
                Delete builds &amp; start fresh. Keeps contracts &amp; questionnaire.
              </div>
            </button>

            {/* Remove */}
            <button
              onClick={() => setView('remove')}
              style={{
                ...btnBase,
                background: '#3B1111',
                color: '#F87171',
                border: '1px solid #EF444444',
                textAlign: 'left',
                padding: '12px 16px',
              }}
            >
              <div style={{ fontWeight: 600 }}>Remove Project</div>
              <div style={{ fontSize: '0.75rem', color: '#94A3B8', marginTop: '2px' }}>
                Delete everything — contracts, builds, questionnaire data.
              </div>
            </button>
          </div>

          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '16px' }}>
            <button onClick={onCancel} style={btnGhost}>Cancel</button>
          </div>
        </div>
      </div>
    );
  }

  // ── Restart view ──
  if (view === 'restart') {
    return (
      <div style={overlay} onClick={onCancel}>
        <div onClick={(e) => e.stopPropagation()} style={card}>
          <h3 style={{ margin: '0 0 8px 0', fontSize: '1rem' }}>Restart Build</h3>
          <p style={subtitle}>
            This will delete all builds and reset the project to &ldquo;contracts ready&rdquo;.
            Your contracts and questionnaire answers are preserved.
          </p>

          {hasRepo && (
            <label
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                marginBottom: '16px',
                color: '#F59E0B',
                fontSize: '0.85rem',
                cursor: 'pointer',
              }}
            >
              <input
                type="checkbox"
                checked={deleteRepo}
                onChange={(e) => setDeleteRepo(e.target.checked)}
                style={{ accentColor: '#F59E0B' }}
              />
              Also delete the GitHub repository
            </label>
          )}

          <div style={{ display: 'flex', gap: '8px', justifyContent: 'space-between' }}>
            <button onClick={() => { setView('main'); setDeleteRepo(false); }} style={btnGhost}>
              Back
            </button>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button onClick={onCancel} style={btnGhost}>Cancel</button>
              <button
                onClick={() => onRestart(deleteRepo)}
                disabled={loading}
                style={btnAmber}
              >
                {loading ? 'Restarting...' : 'Restart'}
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ── Remove view (destructive confirmation) ──
  const canRemove = confirmText.toLowerCase() === 'delete';

  return (
    <div style={overlay} onClick={onCancel}>
      <div onClick={(e) => e.stopPropagation()} style={card}>
        <h3 style={{ margin: '0 0 8px 0', fontSize: '1rem', color: '#EF4444' }}>
          Remove Project
        </h3>
        <p style={subtitle}>
          This will permanently delete &ldquo;{projectName}&rdquo; — all contracts,
          builds, and questionnaire data. This cannot be undone.
        </p>

        {hasRepo && (
          <label
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              marginBottom: '12px',
              color: '#F59E0B',
              fontSize: '0.85rem',
              cursor: 'pointer',
            }}
          >
            <input
              type="checkbox"
              checked={deleteRepo}
              onChange={(e) => setDeleteRepo(e.target.checked)}
              style={{ accentColor: '#F59E0B' }}
            />
            Also delete the GitHub repository
          </label>
        )}

        <div style={{ marginBottom: '16px' }}>
          <label style={{ display: 'block', color: '#94A3B8', fontSize: '0.8rem', marginBottom: '6px' }}>
            Type <strong style={{ color: '#EF4444' }}>delete</strong> to confirm:
          </label>
          <input
            type="text"
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder="delete"
            autoFocus
            style={{
              width: '100%',
              padding: '8px 12px',
              background: '#0F172A',
              border: '1px solid #334155',
              borderRadius: '6px',
              color: '#F1F5F9',
              fontSize: '0.875rem',
              boxSizing: 'border-box',
            }}
          />
        </div>

        <div style={{ display: 'flex', gap: '8px', justifyContent: 'space-between' }}>
          <button onClick={() => { setView('main'); setConfirmText(''); setDeleteRepo(false); }} style={btnGhost}>
            Back
          </button>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button onClick={onCancel} style={btnGhost}>Cancel</button>
            <button
              onClick={() => onRemove(deleteRepo)}
              disabled={loading || !canRemove}
              style={{
                ...btnRed,
                opacity: (loading || !canRemove) ? 0.4 : 1,
                cursor: (loading || !canRemove) ? 'not-allowed' : 'pointer',
              }}
            >
              {loading ? 'Removing...' : 'Remove Project'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default ProjectManageModal;
