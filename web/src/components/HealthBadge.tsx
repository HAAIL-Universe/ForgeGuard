const COLORS: Record<string, string> = {
  green: '#22C55E',
  yellow: '#EAB308',
  red: '#EF4444',
  pending: '#64748B',
};

interface HealthBadgeProps {
  score: string;
  size?: number;
}

function HealthBadge({ score, size = 12 }: HealthBadgeProps) {
  const color = COLORS[score] ?? COLORS.pending;

  return (
    <span
      role="status"
      aria-label={`Health: ${score}`}
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
