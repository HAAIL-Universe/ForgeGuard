/**
 * BuildAuditCard -- per-phase audit result checklist (A1-A9) with PASS/FAIL badges.
 */
import ResultBadge from './ResultBadge';

interface AuditCheck {
  code: string;
  name: string;
  result: string;
  detail: string | null;
}

interface BuildAuditCardProps {
  phase: string;
  iteration: number;
  checks: AuditCheck[];
  overall: string;
}

function BuildAuditCard({ phase, iteration, checks, overall }: BuildAuditCardProps) {
  return (
    <div
      data-testid="build-audit-card"
      style={{
        background: '#1E293B',
        borderRadius: '8px',
        padding: '16px 20px',
        marginBottom: '8px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>{phase}</span>
          {iteration > 1 && (
            <span style={{ color: '#EAB308', fontSize: '0.7rem', fontWeight: 600 }}>
              Iteration {iteration}
            </span>
          )}
        </div>
        <ResultBadge result={overall} />
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        {checks.map((check) => (
          <div key={check.code} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '4px 0' }}>
            <span style={{ width: '28px', fontWeight: 600, fontSize: '0.7rem', color: '#94A3B8', flexShrink: 0 }}>{check.code}</span>
            <span style={{ fontSize: '0.75rem', flex: 1, minWidth: 0 }}>{check.name}</span>
            <ResultBadge result={check.result} />
          </div>
        ))}
      </div>
      {checks.some((c) => c.detail) && (
        <div style={{ marginTop: '8px', borderTop: '1px solid #334155', paddingTop: '8px' }}>
          {checks
            .filter((c) => c.detail)
            .map((c) => (
              <div key={c.code} style={{ color: '#94A3B8', fontSize: '0.7rem', marginBottom: '4px', wordBreak: 'break-word' }}>
                <strong>{c.code}:</strong> {c.detail}
              </div>
            ))}
        </div>
      )}
    </div>
  );
}

export type { AuditCheck };
export default BuildAuditCard;
