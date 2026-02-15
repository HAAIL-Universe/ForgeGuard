/**
 * ProjectCard -- card showing project name, build status, and phase progress.
 */

interface Project {
  id: string;
  name: string;
  description: string | null;
  status: string;
  created_at: string;
  latest_build?: {
    id: string;
    phase: string;
    status: string;
    loop_count: number;
  } | null;
}

interface ProjectCardProps {
  project: Project;
  onClick: (project: Project) => void;
}

const STATUS_COLORS: Record<string, { bg: string; text: string }> = {
  draft: { bg: '#334155', text: '#94A3B8' },
  ready: { bg: '#1E3A5F', text: '#2563EB' },
  building: { bg: '#1E3A5F', text: '#2563EB' },
  completed: { bg: '#14532D', text: '#22C55E' },
  failed: { bg: '#7F1D1D', text: '#EF4444' },
};

function ProjectCard({ project, onClick }: ProjectCardProps) {
  const colors = STATUS_COLORS[project.status] ?? STATUS_COLORS.draft;

  return (
    <div
      data-testid="project-card"
      onClick={() => onClick(project)}
      style={{
        background: '#1E293B',
        borderRadius: '8px',
        padding: '16px 20px',
        cursor: 'pointer',
        transition: 'background 0.15s',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = '#263348')}
      onMouseLeave={(e) => (e.currentTarget.style.background = '#1E293B')}
    >
      <div>
        <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>{project.name}</div>
        {project.description && (
          <div style={{ color: '#94A3B8', fontSize: '0.75rem', marginTop: '4px' }}>
            {project.description.length > 100
              ? project.description.substring(0, 100) + '...'
              : project.description}
          </div>
        )}
        {project.latest_build && (
          <div style={{ color: '#64748B', fontSize: '0.7rem', marginTop: '6px' }}>
            {project.latest_build.phase} &middot; Loops: {project.latest_build.loop_count}
          </div>
        )}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexShrink: 0 }}>
        <span
          style={{
            display: 'inline-block',
            padding: '2px 10px',
            borderRadius: '4px',
            background: colors.bg,
            color: colors.text,
            fontSize: '0.7rem',
            fontWeight: 700,
            letterSpacing: '0.5px',
            textTransform: 'uppercase',
          }}
        >
          {project.status}
        </span>
      </div>
    </div>
  );
}

export type { Project };
export default ProjectCard;
