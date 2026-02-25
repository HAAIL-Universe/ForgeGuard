/**
 * BranchPickerModal -- asks user to continue on main or create a new branch
 * before starting a build.
 *
 * When `starting` is true the full modal collapses into a compact loading
 * card with a 3-D dot-spiral spinner.
 */
import { useState } from 'react';

export interface BranchChoice {
  branch: string;
  contractBatch: number | null;
  freshStart?: boolean;
}

interface ContractVersion {
  batch: number;
  created_at: string;
  count: number;
}

interface BranchPickerModalProps {
  onConfirm: (choice: BranchChoice) => void;
  onCancel: () => void;
  repoConnected: boolean;
  starting?: boolean;
  /** Dynamic status text shown below the loading orb while starting */
  loadingStatus?: string;
  /** Available contract snapshot versions (fetched by parent). Empty = only current. */
  contractVersions?: ContractVersion[];
}

export default function BranchPickerModal({
  onConfirm,
  onCancel,
  repoConnected,
  starting,
  loadingStatus,
  contractVersions = [],
}: BranchPickerModalProps) {
  const suggestedBranch = `forge/build-v${(contractVersions?.length ?? 0) + 2}`;
  const [mode, setMode] = useState<'main' | 'new'>('main');
  const [customBranch, setCustomBranch] = useState('');
  const [step, setStep] = useState<'branch' | 'version'>('branch');
  const [selectedBatch, setSelectedBatch] = useState<number | null>(null);
  const [freshStart, setFreshStart] = useState(false);

  const branchName = mode === 'main' ? 'main' : (customBranch.trim() || suggestedBranch);
  const canConfirm = !starting && (mode === 'main' || branchName.length > 0);
  const hasMultipleVersions = contractVersions.length > 0;

  const handleBranchNext = () => {
    if (!canConfirm) return;
    if (hasMultipleVersions) {
      setStep('version');
    } else {
      onConfirm({ branch: branchName, contractBatch: null, freshStart: mode === 'new' && freshStart });
    }
  };

  const handleVersionConfirm = () => {
    onConfirm({ branch: branchName, contractBatch: selectedBatch, freshStart: mode === 'new' && freshStart });
  };

  const handleBack = () => {
    setStep('branch');
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
    border: '2px solid #2563EB',
    background: '#1E293B',
  };

  /* ── 3-D Orb loading state ─────────────────────────────────── */
  if (starting) {
    return (
      <div
        style={{
          position: 'fixed',
          inset: 0,
          backgroundColor: 'rgba(0,0,0,0.75)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 100,
        }}
      >
        <style>{`
          @keyframes orbCorePulse {
            0%, 100% { transform: translate(-50%,-50%) scale(1);   opacity: 0.9; }
            50%      { transform: translate(-50%,-50%) scale(1.08); opacity: 1; }
          }
          @keyframes orbRingSpin1 {
            from { transform: rotateX(70deg) rotateZ(0deg); }
            to   { transform: rotateX(70deg) rotateZ(360deg); }
          }
          @keyframes orbRingSpin2 {
            from { transform: rotateX(50deg) rotateY(0deg); }
            to   { transform: rotateX(50deg) rotateY(360deg); }
          }
          @keyframes orbRingSpin3 {
            from { transform: rotateY(70deg) rotateZ(0deg); }
            to   { transform: rotateY(70deg) rotateZ(-360deg); }
          }
          @keyframes orbGlow {
            0%, 100% { box-shadow: 0 0 60px 10px rgba(37,99,235,0.25), 0 0 120px 40px rgba(37,99,235,0.08); }
            50%      { box-shadow: 0 0 80px 20px rgba(37,99,235,0.4),  0 0 160px 60px rgba(37,99,235,0.12); }
          }
          @keyframes orbFadeIn {
            from { opacity: 0; transform: scale(0.6); }
            to   { opacity: 1; transform: scale(1); }
          }
          @keyframes statusPulse {
            0%, 100% { opacity: 0.7; }
            50%      { opacity: 1; }
          }
        `}</style>
        <div
          data-testid="build-loading-card"
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: '32px',
            animation: 'orbFadeIn 0.5s ease-out',
          }}
        >
          {/* Orb container */}
          <div
            style={{
              position: 'relative',
              width: '180px',
              height: '180px',
              animation: 'orbGlow 3s ease-in-out infinite',
              borderRadius: '50%',
            }}
          >
            {/* Core sphere */}
            <div
              style={{
                position: 'absolute',
                top: '50%',
                left: '50%',
                transform: 'translate(-50%,-50%)',
                width: '80px',
                height: '80px',
                borderRadius: '50%',
                background:
                  'radial-gradient(circle at 35% 35%, #93C5FD 0%, #2563EB 40%, #1E3A5F 80%, #0F172A 100%)',
                animation: 'orbCorePulse 2.5s ease-in-out infinite',
                boxShadow: '0 0 30px 10px rgba(59,130,246,0.3)',
              }}
            />
            {/* Ring 1 */}
            <div
              style={{
                position: 'absolute',
                top: '50%',
                left: '50%',
                width: '140px',
                height: '140px',
                marginTop: '-70px',
                marginLeft: '-70px',
                border: '2px solid rgba(96,165,250,0.3)',
                borderTopColor: 'rgba(96,165,250,0.7)',
                borderRadius: '50%',
                animation: 'orbRingSpin1 4s linear infinite',
              }}
            />
            {/* Ring 2 */}
            <div
              style={{
                position: 'absolute',
                top: '50%',
                left: '50%',
                width: '160px',
                height: '160px',
                marginTop: '-80px',
                marginLeft: '-80px',
                border: '1.5px solid rgba(96,165,250,0.15)',
                borderRightColor: 'rgba(96,165,250,0.5)',
                borderRadius: '50%',
                animation: 'orbRingSpin2 6s linear infinite',
              }}
            />
            {/* Ring 3 */}
            <div
              style={{
                position: 'absolute',
                top: '50%',
                left: '50%',
                width: '120px',
                height: '120px',
                marginTop: '-60px',
                marginLeft: '-60px',
                border: '1px solid rgba(147,197,253,0.2)',
                borderBottomColor: 'rgba(147,197,253,0.6)',
                borderRadius: '50%',
                animation: 'orbRingSpin3 3s linear infinite',
              }}
            />
          </div>

          {/* Status text */}
          <span
            style={{
              fontSize: '0.85rem',
              color: '#94A3B8',
              fontWeight: 500,
              letterSpacing: '0.02em',
              animation: 'statusPulse 2s ease-in-out infinite',
              textAlign: 'center',
              maxWidth: '280px',
            }}
          >
            {loadingStatus ?? 'Preparing workspace…'}
          </span>
        </div>
      </div>
    );
  }

  /* ── Full modal ─────────────────────────────────────────────── */

  const formatDate = (iso: string) => {
    try {
      return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch { return iso; }
  };

  /* ── Version step content ───────────────────────────────────── */
  const versionBody = (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <p style={{ margin: 0, fontSize: '0.8rem', color: '#94A3B8' }}>
        Multiple contract versions exist. Choose which contracts to build with.
      </p>

      {/* Current (latest) option */}
      <div
        style={selectedBatch === null ? cardSelected : cardBase}
        onClick={() => setSelectedBatch(null)}
        data-testid="version-current"
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span
            style={{
              width: '16px', height: '16px', borderRadius: '50%',
              border: `2px solid ${selectedBatch === null ? '#2563EB' : '#475569'}`,
              display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
            }}
          >
            {selectedBatch === null && (
              <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#2563EB' }} />
            )}
          </span>
          <div>
            <span style={{ fontWeight: 600, fontSize: '0.85rem', color: '#F8FAFC' }}>
              Current (Latest)
            </span>
            <p style={{ margin: '2px 0 0', fontSize: '0.72rem', color: '#94A3B8' }}>
              Live contracts — most recent generation
            </p>
          </div>
        </div>
      </div>

      {/* Snapshot versions */}
      {contractVersions.map((v) => (
        <div
          key={v.batch}
          style={selectedBatch === v.batch ? cardSelected : cardBase}
          onClick={() => setSelectedBatch(v.batch)}
          data-testid={`version-batch-${v.batch}`}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span
              style={{
                width: '16px', height: '16px', borderRadius: '50%',
                border: `2px solid ${selectedBatch === v.batch ? '#2563EB' : '#475569'}`,
                display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
              }}
            >
              {selectedBatch === v.batch && (
                <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#2563EB' }} />
              )}
            </span>
            <div>
              <span style={{ fontWeight: 600, fontSize: '0.85rem', color: '#F8FAFC' }}>
                Version {v.batch}
              </span>
              <p style={{ margin: '2px 0 0', fontSize: '0.72rem', color: '#94A3B8' }}>
                {formatDate(v.created_at)} · {v.count} contracts
              </p>
            </div>
          </div>
        </div>
      ))}
    </div>
  );

  /* ── Branch step content ────────────────────────────────────── */
  const branchBody = (
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
              width: '16px', height: '16px', borderRadius: '50%',
              border: `2px solid ${mode === 'main' ? '#2563EB' : '#475569'}`,
              display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
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
              width: '16px', height: '16px', borderRadius: '50%',
              border: `2px solid ${mode === 'new' ? '#2563EB' : '#475569'}`,
              display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
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
            value={customBranch || suggestedBranch}
            onChange={(e) => setCustomBranch(e.target.value.replace(/\s/g, '-'))}
            placeholder={suggestedBranch}
            autoFocus
            data-testid="branch-name-input"
            style={{
              width: '100%', padding: '10px 12px', background: '#0F172A',
              border: '1px solid #334155', borderRadius: '6px', color: '#F8FAFC',
              fontSize: '0.85rem', outline: 'none', boxSizing: 'border-box',
              marginLeft: '26px', maxWidth: 'calc(100% - 26px)',
            }}
            onFocus={(e) => {
              e.currentTarget.style.borderColor = '#2563EB';
              // Select all on first focus so user can type to replace
              if (!customBranch) {
                e.currentTarget.select();
              }
            }}
            onBlur={(e) => (e.currentTarget.style.borderColor = '#334155')}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && canConfirm) handleBranchNext();
            }}
          />
        )}
        {mode === 'new' && (
          <label
            data-testid="fresh-start-toggle"
            style={{
              display: 'flex', alignItems: 'center', gap: '8px',
              marginLeft: '26px', marginTop: '4px', cursor: 'pointer',
              fontSize: '0.78rem', color: '#94A3B8', userSelect: 'none',
            }}
          >
            <input
              type="checkbox"
              checked={freshStart}
              onChange={(e) => setFreshStart(e.target.checked)}
              data-testid="fresh-start-checkbox"
              style={{ accentColor: '#2563EB', width: '14px', height: '14px', cursor: 'pointer' }}
            />
            Fresh start — ignore existing files, build from contracts only
          </label>
        )}
      </div>
    </div>
  );

  return (
    <div
      style={{
        position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.65)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
      }}
      onClick={onCancel}
    >
      <div
        style={{
          background: '#1E293B', borderRadius: '10px',
          maxWidth: '480px', width: '95%', overflow: 'hidden',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          style={{
            padding: '16px 20px', borderBottom: '1px solid #334155',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          }}
        >
          <h3 style={{ margin: 0, fontSize: '1rem', color: '#F8FAFC' }}>
            {step === 'branch' ? 'Choose Branch' : 'Choose Contract Version'}
          </h3>
          <button
            onClick={onCancel}
            style={{
              background: 'transparent', border: 'none', color: '#94A3B8',
              fontSize: '1.2rem', cursor: 'pointer',
            }}
          >
            ✕
          </button>
        </div>

        {/* Body */}
        {step === 'branch' ? branchBody : versionBody}

        {/* Footer */}
        <div
          style={{
            padding: '14px 20px', borderTop: '1px solid #334155',
            display: 'flex', flexDirection: 'column', gap: '10px',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
          {step === 'version' && (
            <button
              onClick={handleBack}
              data-testid="version-back"
              style={{
                background: 'transparent', border: '1px solid #475569',
                color: '#94A3B8', borderRadius: '6px', padding: '8px 16px',
                cursor: 'pointer', fontSize: '0.8rem', fontWeight: 600, marginRight: 'auto',
              }}
            >
              ← Back
            </button>
          )}
          <button
            onClick={onCancel}
            style={{
              background: 'transparent', border: '1px solid #475569',
              color: '#94A3B8', borderRadius: '6px', padding: '8px 16px',
              cursor: 'pointer', fontSize: '0.8rem', fontWeight: 600,
            }}
          >
            Cancel
          </button>
          <button
            onClick={step === 'branch' ? handleBranchNext : handleVersionConfirm}
            disabled={step === 'branch' ? !canConfirm : false}
            data-testid={step === 'branch' ? 'branch-confirm' : 'version-confirm'}
            style={{
              background: (step === 'branch' ? canConfirm : true) ? '#2563EB' : '#1E3A5F',
              color: '#fff', border: 'none', borderRadius: '6px',
              padding: '8px 20px',
              cursor: (step === 'branch' ? canConfirm : true) ? 'pointer' : 'not-allowed',
              fontSize: '0.8rem', fontWeight: 600,
              opacity: (step === 'branch' ? canConfirm : true) ? 1 : 0.5,
            }}
          >
            {step === 'branch' && hasMultipleVersions ? 'Next' : 'Start Build'}
          </button>
          </div>
        </div>
      </div>
    </div>
  );
}
