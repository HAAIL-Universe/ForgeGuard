interface EmptyStateProps {
  message: string;
  actionLabel?: string;
  onAction?: () => void;
}

function EmptyState({ message, actionLabel, onAction }: EmptyStateProps) {
  return (
    <div
      data-testid="empty-state"
      style={{
        textAlign: 'center',
        padding: '64px 24px',
        color: '#94A3B8',
      }}
    >
      <p style={{ marginBottom: actionLabel ? '16px' : '0' }}>{message}</p>
      {actionLabel && onAction && (
        <button
          onClick={onAction}
          style={{
            background: '#2563EB',
            color: '#fff',
            border: 'none',
            borderRadius: '6px',
            padding: '8px 16px',
            cursor: 'pointer',
            fontSize: '0.85rem',
          }}
        >
          {actionLabel}
        </button>
      )}
    </div>
  );
}

export default EmptyState;
