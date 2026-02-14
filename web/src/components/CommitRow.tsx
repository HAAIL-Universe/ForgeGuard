import ResultBadge from './ResultBadge';

interface AuditRun {
  id: string;
  commit_sha: string;
  commit_message: string;
  commit_author: string;
  branch: string;
  status: string;
  overall_result: string | null;
  started_at: string | null;
  completed_at: string | null;
  files_checked: number;
  check_summary: string | null;
}

interface CommitRowProps {
  audit: AuditRun;
  onClick: (audit: AuditRun) => void;
}

function CommitRow({ audit, onClick }: CommitRowProps) {
  const shortSha = audit.commit_sha.substring(0, 7);
  const message =
    audit.commit_message && audit.commit_message.length > 80
      ? audit.commit_message.substring(0, 80) + '...'
      : audit.commit_message || '';

  return (
    <div
      onClick={() => onClick(audit)}
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '12px 16px',
        background: '#1E293B',
        borderRadius: '6px',
        cursor: 'pointer',
        transition: 'background 0.15s',
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = '#263348')}
      onMouseLeave={(e) => (e.currentTarget.style.background = '#1E293B')}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flex: 1, minWidth: 0 }}>
        <code
          style={{
            color: '#2563EB',
            fontSize: '0.8rem',
            fontFamily: 'monospace',
            flexShrink: 0,
          }}
        >
          {shortSha}
        </code>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div
            style={{
              fontSize: '0.85rem',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {message}
          </div>
          <div style={{ color: '#94A3B8', fontSize: '0.7rem', marginTop: '2px' }}>
            {audit.commit_author} &middot; {audit.branch}
            {audit.started_at && (
              <span> &middot; {new Date(audit.started_at).toLocaleString()}</span>
            )}
          </div>
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0, marginLeft: '12px' }}>
        {audit.check_summary && (
          <span style={{ color: '#64748B', fontSize: '0.65rem', fontFamily: 'monospace', whiteSpace: 'nowrap' }}>
            {audit.check_summary}
          </span>
        )}
        <ResultBadge result={audit.overall_result} />
      </div>
    </div>
  );
}

export type { AuditRun };
export default CommitRow;
