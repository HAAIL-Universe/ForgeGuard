/**
 * PhaseProgressBar -- horizontal phase progress visualization.
 * Grey=pending, blue=active, green=pass, red=fail.
 */

interface Phase {
  label: string;
  status: 'pending' | 'active' | 'pass' | 'fail';
}

interface PhaseProgressBarProps {
  phases: Phase[];
}

const STATUS_COLORS: Record<string, string> = {
  pending: '#334155',
  active: '#2563EB',
  pass: '#22C55E',
  fail: '#EF4444',
};

function PhaseProgressBar({ phases }: PhaseProgressBarProps) {
  if (phases.length === 0) return null;

  return (
    <div data-testid="phase-progress-bar" style={{ display: 'flex', gap: '4px', alignItems: 'center', width: '100%' }}>
      {phases.map((phase, i) => {
        const color = STATUS_COLORS[phase.status] ?? STATUS_COLORS.pending;
        const isActive = phase.status === 'active';
        return (
          <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flex: 1, gap: '6px' }}>
            <div
              style={{
                width: '100%',
                height: '8px',
                borderRadius: '4px',
                background: color,
                boxShadow: isActive ? `0 0 8px ${color}` : 'none',
                transition: 'background 0.3s, box-shadow 0.3s',
              }}
            />
            <span
              style={{
                fontSize: '0.6rem',
                color: isActive ? '#F8FAFC' : '#64748B',
                fontWeight: isActive ? 700 : 400,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                maxWidth: '100%',
                textAlign: 'center',
              }}
            >
              {phase.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export type { Phase };
export default PhaseProgressBar;
