const COLORS: Record<string, string> = {
  green: '#22C55E',
  yellow: '#EAB308',
  red: '#EF4444',
  pending: '#64748B',
  deleted: '#475569',
  archived: '#6366F1',
  inaccessible: '#F97316',
};

const STATUS_LABEL: Record<string, string> = {
  deleted: 'Deleted on GitHub',
  archived: 'Archived',
  inaccessible: 'Access lost',
};

interface HealthBadgeProps {
  score: string;
  size?: number;
}

function HealthBadge({ score, size = 12 }: HealthBadgeProps) {
  const color = COLORS[score] ?? COLORS.pending;
  const label = STATUS_LABEL[score] ?? score;

  return (
    <span
      role="status"
      aria-label={`Health: ${label}`}
      title={label}
      style={{
        display: 'inline-block',
        width: size,
        height: size,
        borderRadius: '50%',
        backgroundColor: color,
        flexShrink: 0,
      }}
    />
  );
}

export default HealthBadge;
export { STATUS_LABEL };
