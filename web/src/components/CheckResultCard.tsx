import ResultBadge from './ResultBadge';

interface CheckResultData {
  id: string;
  check_code: string;
  check_name: string;
  result: string;
  detail: string | null;
}

interface CheckResultCardProps {
  check: CheckResultData;
}

function CheckResultCard({ check }: CheckResultCardProps) {
  return (
    <div
      style={{
        background: '#1E293B',
        borderRadius: '6px',
        padding: '14px 18px',
        marginBottom: '8px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '6px' }}>
        <span style={{ fontWeight: 600, fontSize: '0.85rem', color: '#94A3B8' }}>
          {check.check_code}
        </span>
        <span style={{ fontSize: '0.85rem' }}>{check.check_name}</span>
        <ResultBadge result={check.result} />
      </div>
      {check.detail && (
        <div
          style={{
            color: '#94A3B8',
            fontSize: '0.75rem',
            marginTop: '4px',
            paddingLeft: '4px',
            wordBreak: 'break-word',
          }}
        >
          {check.detail}
        </div>
      )}
    </div>
  );
}

export type { CheckResultData };
export default CheckResultCard;
