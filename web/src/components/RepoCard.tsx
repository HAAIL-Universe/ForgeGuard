import { useState } from 'react';
import HealthBadge from './HealthBadge';
import { STATUS_LABEL } from './HealthBadge';

interface Repo {
  id: string;
  full_name: string;
  default_branch: string;
  webhook_active: boolean;
  health_score: string;
  last_audit_at: string | null;
  recent_pass_rate: number | null;
  // Health-check fields
  repo_status: string;
  latest_commit_sha: string | null;
  latest_commit_message: string | null;
  latest_commit_at: string | null;
  latest_commit_author: string | null;
  last_health_check_at: string | null;
}

interface RepoCardProps {
  repo: Repo;
  onDisconnect: (repo: Repo) => void;
  onUpdate: (repo: Repo) => void;
  onClick: (repo: Repo) => void;
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function RepoCard({ repo, onDisconnect, onUpdate, onClick }: RepoCardProps) {
  const [expanded, setExpanded] = useState(false);

  const isDeleted = repo.repo_status === 'deleted';
  const isArchived = repo.repo_status === 'archived';

  // Use repo_status as health badge score when non-standard
  const badgeScore =
    isDeleted || isArchived || repo.repo_status === 'inaccessible'
      ? repo.repo_status
      : repo.health_score;

  const hasNewCommits =
    repo.latest_commit_at && repo.last_audit_at
      ? new Date(repo.latest_commit_at) > new Date(repo.last_audit_at)
      : false;

  const handleCardClick = () => {
    if (isDeleted) return;  // deleted repos are not navigable
    if (expanded) {
      setExpanded(false);
    } else {
      onClick(repo);
    }
  };

  return (
    <div
      onClick={handleCardClick}
      style={{
        background: '#1E293B',
        borderRadius: '8px',
        padding: '16px 20px',
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
        cursor: isDeleted ? 'default' : 'pointer',
        transition: 'background 0.15s',
        opacity: isDeleted ? 0.75 : 1,
      }}
      onMouseEnter={(e) => {
        if (!isDeleted) e.currentTarget.style.background = '#263348';
      }}
      onMouseLeave={(e) => (e.currentTarget.style.background = '#1E293B')}
    >
      {/* Main row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', minWidth: 0, flex: 1 }}>
          <HealthBadge score={badgeScore} />
          <div style={{ minWidth: 0 }}>
            {/* Repo name — strikethrough when deleted */}
            <div style={{
              fontWeight: 600,
              fontSize: '0.9rem',
              textDecoration: isDeleted ? 'line-through' : 'none',
              opacity: isDeleted ? 0.5 : 1,
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}>
              {repo.full_name}
              {isArchived && (
                <span style={{
                  fontSize: '0.65rem',
                  padding: '1px 6px',
                  borderRadius: '4px',
                  background: '#312E81',
                  color: '#A5B4FC',
                  fontWeight: 600,
                  letterSpacing: '0.3px',
                }}>
                  Archived
                </span>
              )}
            </div>

            {/* Deleted label */}
            {isDeleted && (
              <div style={{ color: '#64748B', fontSize: '0.75rem', marginTop: '2px' }}>
                {STATUS_LABEL['deleted']}
              </div>
            )}

            {/* Latest commit line — shown when not deleted and commit info available */}
            {!isDeleted && repo.latest_commit_sha && (
              <div style={{
                color: '#64748B',
                fontSize: '0.73rem',
                marginTop: '3px',
                fontFamily: 'monospace',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                maxWidth: '480px',
              }}>
                <span style={{ color: '#94A3B8' }}>
                  {repo.latest_commit_sha.slice(0, 7)}
                </span>
                {'  '}
                <span style={{ color: '#CBD5E1' }}>
                  {(repo.latest_commit_message ?? '').slice(0, 60)}
                </span>
                {repo.latest_commit_at && (
                  <span style={{ color: '#475569', marginLeft: '6px' }}>
                    · {relativeTime(repo.latest_commit_at)}
                  </span>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Action buttons */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
          {!isDeleted && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                setExpanded((prev) => !prev);
              }}
              title="Expand details"
              style={{
                background: 'transparent',
                color: '#64748B',
                border: '1px solid #334155',
                borderRadius: '4px',
                padding: '4px 7px',
                cursor: 'pointer',
                fontSize: '0.7rem',
                lineHeight: 1,
              }}
            >
              {expanded ? '▾' : '▸'}
            </button>
          )}

          {/* Primary action: context-dependent */}
          {isDeleted ? (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onDisconnect(repo);
              }}
              style={{
                background: 'transparent',
                color: '#EF4444',
                border: '1px solid #7F1D1D',
                borderRadius: '6px',
                padding: '6px 12px',
                cursor: 'pointer',
                fontSize: '0.75rem',
              }}
            >
              Clear
            </button>
          ) : hasNewCommits ? (
            <>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onUpdate(repo);
                }}
                style={{
                  background: '#1D4ED8',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '6px',
                  padding: '6px 12px',
                  cursor: 'pointer',
                  fontSize: '0.75rem',
                  fontWeight: 600,
                }}
              >
                Update
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onDisconnect(repo);
                }}
                title="Disconnect repo"
                style={{
                  background: 'transparent',
                  color: '#64748B',
                  border: '1px solid #334155',
                  borderRadius: '6px',
                  padding: '6px 8px',
                  cursor: 'pointer',
                  fontSize: '0.7rem',
                }}
              >
                ···
              </button>
            </>
          ) : (
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
          )}
        </div>
      </div>

      {/* Expanded section */}
      {expanded && !isDeleted && (
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            paddingTop: '8px',
            borderTop: '1px solid #334155',
            fontSize: '0.75rem',
            color: '#94A3B8',
            display: 'flex',
            flexDirection: 'column',
            gap: '4px',
          }}
        >
          <div>
            Last audited:{' '}
            {repo.last_audit_at
              ? relativeTime(repo.last_audit_at)
              : 'never'}
          </div>
          {hasNewCommits && (
            <div style={{ color: '#FBBF24', display: 'flex', alignItems: 'center', gap: '4px' }}>
              <span>⚡</span>
              <span>New commits since last audit</span>
            </div>
          )}
          {repo.recent_pass_rate !== null && (
            <div>
              Recent pass rate:{' '}
              <span style={{
                color: repo.recent_pass_rate === 1 ? '#22C55E'
                  : repo.recent_pass_rate >= 0.5 ? '#EAB308'
                  : '#EF4444',
              }}>
                {Math.round(repo.recent_pass_rate * 100)}%
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export type { Repo };
export default RepoCard;
