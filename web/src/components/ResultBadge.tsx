interface ResultBadgeProps {
  result: string | null;
  size?: 'small' | 'large';
}

const RESULT_COLORS: Record<string, string> = {
  PASS: '#22C55E',
  FAIL: '#EF4444',
  WARN: '#EAB308',
  ERROR: '#F97316',
  pending: '#64748B',
};

function ResultBadge({ result, size = 'small' }: ResultBadgeProps) {
  const label = result ?? 'PENDING';
  const color = RESULT_COLORS[label] ?? RESULT_COLORS.pending;
  const isLarge = size === 'large';

  return (
    <span
      style={{
        display: 'inline-block',
        padding: isLarge ? '6px 16px' : '2px 8px',
        borderRadius: '4px',
        backgroundColor: `${color}22`,
        color: color,
        fontSize: isLarge ? '0.9rem' : '0.7rem',
        fontWeight: 700,
        letterSpacing: '0.5px',
      }}
    >
      {label}
    </span>
  );
}

export default ResultBadge;
