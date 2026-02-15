import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import PhaseProgressBar from '../components/PhaseProgressBar';
import BuildLogViewer from '../components/BuildLogViewer';
import BuildAuditCard from '../components/BuildAuditCard';
import ProjectCard from '../components/ProjectCard';

describe('PhaseProgressBar', () => {
  it('renders the progress bar', () => {
    render(
      <PhaseProgressBar
        phases={[
          { label: 'P0', status: 'pass' },
          { label: 'P1', status: 'active' },
          { label: 'P2', status: 'pending' },
        ]}
      />,
    );
    expect(screen.getByTestId('phase-progress-bar')).toBeInTheDocument();
  });

  it('renders all phase labels', () => {
    render(
      <PhaseProgressBar
        phases={[
          { label: 'P0', status: 'pass' },
          { label: 'P1', status: 'active' },
          { label: 'P2', status: 'pending' },
        ]}
      />,
    );
    expect(screen.getByText('P0')).toBeInTheDocument();
    expect(screen.getByText('P1')).toBeInTheDocument();
    expect(screen.getByText('P2')).toBeInTheDocument();
  });

  it('renders nothing with empty phases', () => {
    const { container } = render(<PhaseProgressBar phases={[]} />);
    expect(container.firstChild).toBeNull();
  });
});

describe('BuildLogViewer', () => {
  it('renders the log viewer', () => {
    render(<BuildLogViewer logs={[]} />);
    expect(screen.getByTestId('build-log-viewer')).toBeInTheDocument();
  });

  it('shows waiting message when no logs', () => {
    render(<BuildLogViewer logs={[]} />);
    expect(screen.getByText('Waiting for build output...')).toBeInTheDocument();
  });

  it('renders log entries', () => {
    render(
      <BuildLogViewer
        logs={[
          {
            id: '1',
            timestamp: '2026-01-01T00:00:00Z',
            source: 'builder',
            level: 'info',
            message: 'Hello from builder',
          },
        ]}
      />,
    );
    expect(screen.getByText('Hello from builder')).toBeInTheDocument();
    expect(screen.getByText('[builder]')).toBeInTheDocument();
  });
});

describe('BuildAuditCard', () => {
  it('renders the audit card', () => {
    render(
      <BuildAuditCard
        phase="Phase 0"
        iteration={1}
        checks={[
          { code: 'A1', name: 'Scope compliance', result: 'PASS', detail: null },
          { code: 'A2', name: 'Minimal diff', result: 'PASS', detail: null },
        ]}
        overall="PASS"
      />,
    );
    expect(screen.getByTestId('build-audit-card')).toBeInTheDocument();
    expect(screen.getByText('Phase 0')).toBeInTheDocument();
    expect(screen.getByText('A1')).toBeInTheDocument();
    expect(screen.getByText('A2')).toBeInTheDocument();
  });

  it('shows iteration count when > 1', () => {
    render(
      <BuildAuditCard
        phase="Phase 1"
        iteration={3}
        checks={[]}
        overall="FAIL"
      />,
    );
    expect(screen.getByText('Iteration 3')).toBeInTheDocument();
  });

  it('shows detail text for checks with details', () => {
    render(
      <BuildAuditCard
        phase="Phase 2"
        iteration={1}
        checks={[
          { code: 'A4', name: 'Boundary compliance', result: 'FAIL', detail: 'Violation found' },
        ]}
        overall="FAIL"
      />,
    );
    expect(screen.getByText(/Violation found/)).toBeInTheDocument();
  });
});

describe('ProjectCard', () => {
  const project = {
    id: '1',
    name: 'Test Project',
    description: 'A test project',
    status: 'building',
    created_at: '2026-01-01T00:00:00Z',
    latest_build: {
      id: 'b1',
      phase: 'Phase 3',
      status: 'running',
      loop_count: 1,
    },
  };

  it('renders the project card', () => {
    render(<ProjectCard project={project} onClick={() => {}} />);
    expect(screen.getByTestId('project-card')).toBeInTheDocument();
  });

  it('displays project name', () => {
    render(<ProjectCard project={project} onClick={() => {}} />);
    expect(screen.getByText('Test Project')).toBeInTheDocument();
  });

  it('displays project description', () => {
    render(<ProjectCard project={project} onClick={() => {}} />);
    expect(screen.getByText('A test project')).toBeInTheDocument();
  });

  it('displays build status', () => {
    render(<ProjectCard project={project} onClick={() => {}} />);
    expect(screen.getByText('building')).toBeInTheDocument();
  });

  it('displays latest build info', () => {
    render(<ProjectCard project={project} onClick={() => {}} />);
    expect(screen.getByText(/Phase 3/)).toBeInTheDocument();
  });
});
