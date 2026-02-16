/**
 * BranchPickerModal -- asks user to continue on main or create a new branch
 * before starting a build.
 */
import { useState } from 'react';

export interface BranchChoice {
  branch: string;
}

interface BranchPickerModalProps {
  onConfirm: (choice: BranchChoice) => void;
  onCancel: () => void;
  repoConnected: boolean;
  starting?: boolean;
}

export default function BranchPickerModal({
  onConfirm,
  onCancel,
  repoConnected,
  starting,
}: BranchPickerModalProps) {
  const [mode, setMode] = useState<'main' | 'new'>('main');
  const [customBranch, setCustomBranch] = useState('');

  const branchName = mode === 'main' ? 'main' : customBranch.trim();
  const canConfirm = !starting && (mode === 'main' || branchName.length > 0);

  const handleConfirm = () => {
    if (!canConfirm) return;
    onConfirm({ branch: branchName });
  };

  const cardBase: React.CSSProperties = {
    background: '#0F172A',
    border: '2px solid #334155',
    borderRadius: '8px',
    padding: '14px 16px',
    cursor: 'pointer',
    transition: 'border-color 0.15s, background 0.15s',
  };

  const cardSelected: React.CSSProperties = {
    ...cardBase,
    borderColor: '#2563EB',
    background: '#1E293B',
  };

  return (
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
      onClick={onCancel}
    >
      <div
        style={{
          background: '#1E293B',
          borderRadius: '10px',
          maxWidth: '480px',
          width: '95%',
          overflow: 'hidden',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          style={{
            padding: '16px 20px',
            borderBottom: '1px solid #334155',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <h3 style={{ margin: 0, fontSize: '1rem', color: '#F8FAFC' }}>Choose Branch</h3>
          <button
            onClick={onCancel}
            style={{
              background: 'transparent',
              border: 'none',
              color: '#94A3B8',
              fontSize: '1.2rem',
              cursor: 'pointer',
            }}
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
          <p style={{ margin: 0, fontSize: '0.8rem', color: '#94A3B8' }}>
            {repoConnected
              ? 'Build on the main branch or create a new branch for this iteration.'
              : 'Choose which branch to build on.'}
          </p>

          {/* Option: Continue on main */}
          <div
            style={mode === 'main' ? cardSelected : cardBase}
            onClick={() => setMode('main')}
            data-testid="branch-main"
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <span
                style={{
                  width: '16px',
                  height: '16px',
                  borderRadius: '50%',
                  border: `2px solid ${mode === 'main' ? '#2563EB' : '#475569'}`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                }}
              >
                {mode === 'main' && (
                  <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#2563EB' }} />
                )}
              </span>
              <div>
                <span style={{ fontWeight: 600, fontSize: '0.85rem', color: '#F8FAFC' }}>
                  Continue on main
                </span>
                <p style={{ margin: '2px 0 0', fontSize: '0.72rem', color: '#94A3B8' }}>
                  Build directly on the default branch.
                </p>
              </div>
            </div>
          </div>

          {/* Option: New branch */}
          <div
            style={mode === 'new' ? cardSelected : cardBase}
            onClick={() => setMode('new')}
            data-testid="branch-new"
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: mode === 'new' ? '10px' : '0' }}>
              <span
                style={{
                  width: '16px',
                  height: '16px',
                  borderRadius: '50%',
                  border: `2px solid ${mode === 'new' ? '#2563EB' : '#475569'}`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                }}
              >
                {mode === 'new' && (
                  <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#2563EB' }} />
                )}
              </span>
              <div>
                <span style={{ fontWeight: 600, fontSize: '0.85rem', color: '#F8FAFC' }}>
                  Create new branch
                </span>
                <p style={{ margin: '2px 0 0', fontSize: '0.72rem', color: '#94A3B8' }}>
                  Build on a fresh branch for a separate iteration.
                </p>
              </div>
            </div>
            {mode === 'new' && (
              <input
                type="text"
                value={customBranch}
                onChange={(e) => setCustomBranch(e.target.value.replace(/\s/g, '-'))}
                placeholder="forge/build-v2"
                autoFocus
                data-testid="branch-name-input"
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  background: '#0F172A',
                  border: '1px solid #334155',
                  borderRadius: '6px',
                  color: '#F8FAFC',
                  fontSize: '0.85rem',
                  outline: 'none',
                  boxSizing: 'border-box',
                  marginLeft: '26px',
                  maxWidth: 'calc(100% - 26px)',
                }}
                onFocus={(e) => (e.currentTarget.style.borderColor = '#2563EB')}
                onBlur={(e) => (e.currentTarget.style.borderColor = '#334155')}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && canConfirm) handleConfirm();
                }}
              />
            )}
          </div>
        </div>

        {/* Footer */}
        <div
          style={{
            padding: '14px 20px',
            borderTop: '1px solid #334155',
            display: 'flex',
            flexDirection: 'column',
            gap: '10px',
          }}
        >
          {/* Git sync status banner — visible once user clicks Start Build */}
          {starting && (
            <div
              data-testid="git-sync-status"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
                padding: '10px 14px',
                background: '#0C1425',
                border: '1px solid #1E3A5F',
                borderRadius: '6px',
                fontSize: '0.78rem',
                color: '#93C5FD',
              }}
            >
              <span
                style={{
                  display: 'inline-block',
                  width: '14px',
                  height: '14px',
                  border: '2px solid #3B82F6',
                  borderTopColor: 'transparent',
                  borderRadius: '50%',
                  animation: 'forgeSpin 0.8s linear infinite',
                  flexShrink: 0,
                }}
              />
              <span>Syncing contracts to Git&nbsp;&mdash;&nbsp;cloning repo, writing Forge/Contracts/, preparing workspace…</span>
            </div>
          )}
          <style>{`@keyframes forgeSpin { to { transform: rotate(360deg); } }`}</style>

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
          <button
            onClick={onCancel}
            style={{
              background: 'transparent',
              border: '1px solid #475569',
              color: '#94A3B8',
              borderRadius: '6px',
              padding: '8px 16px',
              cursor: 'pointer',
              fontSize: '0.8rem',
              fontWeight: 600,
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleConfirm}
            disabled={!canConfirm}
            data-testid="branch-confirm"
            style={{
              background: canConfirm ? '#2563EB' : '#1E3A5F',
              color: '#fff',
              border: 'none',
              borderRadius: '6px',
              padding: '8px 20px',
              cursor: canConfirm ? 'pointer' : 'not-allowed',
              fontSize: '0.8rem',
              fontWeight: 600,
              opacity: canConfirm ? 1 : 0.5,
            }}
          >
            {starting ? 'Starting...' : 'Start Build'}
          </button>
          </div>
        </div>
      </div>
    </div>
  );
}
