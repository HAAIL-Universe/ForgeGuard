/**
 * BuildTargetModal -- lets user choose where the build writes files.
 *
 * Three target types:
 *   - github_new     — create a new GitHub repo and push generated code
 *   - github_existing — clone an existing repo, add generated code, push
 *   - local_path     — write files to a local directory
 *
 * Passes { target_type, target_ref } back to the caller.
 */
import { useState } from 'react';

export interface BuildTarget {
  target_type: string;
  target_ref: string;
}

interface BuildTargetModalProps {
  onConfirm: (target: BuildTarget) => void;
  onCancel: () => void;
  starting?: boolean;
}

type TargetOption = 'github_new' | 'github_existing' | 'local_path';

const TARGET_OPTIONS: { value: TargetOption; label: string; description: string; placeholder: string }[] = [
  {
    value: 'github_new',
    label: 'New GitHub Repo',
    description: 'Create a new repository under your GitHub account and push generated code.',
    placeholder: 'my-new-project',
  },
  {
    value: 'github_existing',
    label: 'Existing GitHub Repo',
    description: 'Clone an existing repo, add generated files, and push.',
    placeholder: 'owner/repo-name',
  },
  {
    value: 'local_path',
    label: 'Local Directory',
    description: 'Write generated files to a directory on the server.',
    placeholder: 'C:\\Projects\\my-app  or  /home/user/projects/my-app',
  },
];

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

export default function BuildTargetModal({ onConfirm, onCancel, starting }: BuildTargetModalProps) {
  const [selected, setSelected] = useState<TargetOption>('github_new');
  const [targetRef, setTargetRef] = useState('');

  const currentOption = TARGET_OPTIONS.find((o) => o.value === selected)!;

  const canConfirm = targetRef.trim().length > 0 && !starting;

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
          maxWidth: '540px',
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
          <h3 style={{ margin: 0, fontSize: '1rem', color: '#F8FAFC' }}>Build Target</h3>
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
        <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <p style={{ margin: 0, fontSize: '0.8rem', color: '#94A3B8' }}>
            Choose where ForgeGuard should write the generated files.
          </p>

          {/* Target type cards */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {TARGET_OPTIONS.map((opt) => (
              <div
                key={opt.value}
                style={selected === opt.value ? cardSelected : cardBase}
                onClick={() => {
                  setSelected(opt.value);
                  setTargetRef('');
                }}
                data-testid={`target-${opt.value}`}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '4px' }}>
                  <span
                    style={{
                      width: '16px',
                      height: '16px',
                      borderRadius: '50%',
                      border: `2px solid ${selected === opt.value ? '#2563EB' : '#475569'}`,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      flexShrink: 0,
                    }}
                  >
                    {selected === opt.value && (
                      <span
                        style={{
                          width: '8px',
                          height: '8px',
                          borderRadius: '50%',
                          background: '#2563EB',
                        }}
                      />
                    )}
                  </span>
                  <span style={{ fontWeight: 600, fontSize: '0.85rem', color: '#F8FAFC' }}>{opt.label}</span>
                </div>
                <p style={{ margin: '0 0 0 26px', fontSize: '0.72rem', color: '#94A3B8' }}>{opt.description}</p>
              </div>
            ))}
          </div>

          {/* Target ref input */}
          <div>
            <label
              style={{ display: 'block', fontSize: '0.75rem', color: '#94A3B8', marginBottom: '6px' }}
            >
              {selected === 'github_new'
                ? 'Repository name'
                : selected === 'github_existing'
                  ? 'Repository (owner/name)'
                  : 'Directory path'}
            </label>
            <input
              type="text"
              value={targetRef}
              onChange={(e) => setTargetRef(e.target.value)}
              placeholder={currentOption.placeholder}
              data-testid="target-ref-input"
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
              }}
              onFocus={(e) => (e.currentTarget.style.borderColor = '#2563EB')}
              onBlur={(e) => (e.currentTarget.style.borderColor = '#334155')}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && canConfirm) {
                  onConfirm({ target_type: selected, target_ref: targetRef.trim() });
                }
              }}
              autoFocus
            />
          </div>
        </div>

        {/* Footer */}
        <div
          style={{
            padding: '12px 20px',
            borderTop: '1px solid #334155',
            display: 'flex',
            justifyContent: 'flex-end',
            gap: '10px',
          }}
        >
          <button
            onClick={onCancel}
            style={{
              background: 'transparent',
              color: '#94A3B8',
              border: '1px solid #334155',
              borderRadius: '6px',
              padding: '8px 16px',
              cursor: 'pointer',
              fontSize: '0.8rem',
            }}
          >
            Cancel
          </button>
          <button
            onClick={() => onConfirm({ target_type: selected, target_ref: targetRef.trim() })}
            disabled={!canConfirm}
            data-testid="target-confirm-btn"
            style={{
              background: canConfirm ? '#2563EB' : '#1E293B',
              color: canConfirm ? '#fff' : '#475569',
              border: 'none',
              borderRadius: '6px',
              padding: '8px 20px',
              cursor: canConfirm ? 'pointer' : 'not-allowed',
              fontSize: '0.8rem',
              fontWeight: 600,
            }}
          >
            {starting ? 'Starting...' : 'Start Build'}
          </button>
        </div>
      </div>
    </div>
  );
}
