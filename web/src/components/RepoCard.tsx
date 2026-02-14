import HealthBadge from './HealthBadge';

interface Repo {
  id: string;
  full_name: string;
  default_branch: string;
  webhook_active: boolean;
  health_score: string;
  last_audit_at: string | null;
  recent_pass_rate: number | null;
}

interface RepoCardProps {
  repo: Repo;
  onDisconnect: (repo: Repo) => void;
  onClick: (repo: Repo) => void;
}

function RepoCard({ repo, onDisconnect, onClick }: RepoCardProps) {
  return (
    <div
      onClick={() => onClick(repo)}
      style={{
        background: '#1E293B',
        borderRadius: '8px',
        padding: '16px 20px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        cursor: 'pointer',
        transition: 'background 0.15s',
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = '#263348')}
      onMouseLeave={(e) => (e.currentTarget.style.background = '#1E293B')}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <HealthBadge score={repo.health_score} />
        <div>
          <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>{repo.full_name}</div>
          <div style={{ color: '#94A3B8', fontSize: '0.75rem', marginTop: '4px' }}>
            {repo.last_audit_at
              ? `Last audit: ${new Date(repo.last_audit_at).toLocaleString()}`
              : 'No audits yet'}
            {repo.recent_pass_rate !== null && (
              <span style={{ marginLeft: '12px' }}>
                Pass rate: {Math.round(repo.recent_pass_rate * 100)}%
              </span>
            )}
          </div>
        </div>
      </div>
      <button
        onClick={(e) => {
          e.stopPropagation();
          onDisconnect(repo);
        }}
        style={{
          background: 'transparent',
          color: '#94A3B8',
          border: '1px solid #334155',
          borderRadius: '6px',
          padding: '6px 12px',
          cursor: 'pointer',
          fontSize: '0.75rem',
        }}
      >
        Disconnect
      </button>
    </div>
  );
}

export type { Repo };
export default RepoCard;
