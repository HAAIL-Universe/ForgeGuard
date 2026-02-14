import { useState } from 'react';

interface ConfirmDialogProps {
  title: string;
  message: string;
  confirmLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

function ConfirmDialog({
  title,
  message,
  confirmLabel = 'Confirm',
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const [loading, setLoading] = useState(false);

  const handleConfirm = async () => {
    setLoading(true);
    onConfirm();
  };

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
      onClick={onCancel}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: '#1E293B',
          borderRadius: '8px',
          padding: '24px',
          maxWidth: '400px',
          width: '90%',
        }}
      >
        <h3 style={{ margin: '0 0 8px 0', fontSize: '1rem' }}>{title}</h3>
        <p style={{ color: '#94A3B8', margin: '0 0 24px 0', fontSize: '0.875rem' }}>
          {message}
        </p>
        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
          <button
            onClick={onCancel}
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
            onClick={handleConfirm}
            disabled={loading}
            style={{
              background: '#EF4444',
              color: '#fff',
              border: 'none',
              borderRadius: '6px',
              padding: '8px 16px',
              cursor: loading ? 'not-allowed' : 'pointer',
              fontSize: '0.875rem',
              opacity: loading ? 0.6 : 1,
            }}
          >
            {loading ? 'Removing...' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export default ConfirmDialog;
