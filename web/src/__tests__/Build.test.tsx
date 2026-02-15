import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import PhaseProgressBar from '../components/PhaseProgressBar';
import BuildLogViewer from '../components/BuildLogViewer';
import BuildAuditCard from '../components/BuildAuditCard';
import ProjectCard from '../components/ProjectCard';
import BuildComplete from '../pages/BuildComplete';

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

// ── BuildComplete ───────────────────────────────────────────────────────

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ token: 'test-token' }),
  AuthProvider: ({ children }: { children: unknown }) => children,
}));

const mockSummary = {
  build: {
    id: 'b1',
    project_id: 'p1',
    phase: 'Phase 5',
    status: 'completed',
    loop_count: 1,
    started_at: '2026-01-01T00:00:00Z',
    completed_at: '2026-01-01T01:00:00Z',
    error_detail: null,
    created_at: '2026-01-01T00:00:00Z',
  },
  cost: {
    total_input_tokens: 50000,
    total_output_tokens: 25000,
    total_cost_usd: 2.625,
    phases: [
      { phase: 'Phase 0', input_tokens: 10000, output_tokens: 5000, model: 'claude-opus-4-6', estimated_cost_usd: 0.525 },
    ],
  },
  elapsed_seconds: 3600,
  loop_count: 1,
};

const mockInstructions = {
  project_name: 'TestApp',
  instructions: '# Deployment Instructions — TestApp\n\n## Prerequisites\n- Python 3.12+',
};

function renderBuildComplete(fetchImpl?: typeof global.fetch) {
  if (fetchImpl) {
    global.fetch = fetchImpl;
  }
  return render(
    <MemoryRouter initialEntries={['/projects/p1/build/complete']}>
      <Routes>
        <Route path="/projects/:projectId/build/complete" element={<BuildComplete />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('BuildComplete', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('shows skeleton while loading', () => {
    global.fetch = vi.fn(() => new Promise(() => {})) as unknown as typeof fetch;
    renderBuildComplete();
    expect(screen.getByTestId('build-complete-skeleton')).toBeInTheDocument();
  });

  it('renders build complete page with data', async () => {
    global.fetch = vi.fn((url: string | URL | Request) => {
      const u = typeof url === 'string' ? url : url.toString();
      if (u.includes('/summary')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSummary) } as Response);
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockInstructions) } as Response);
    }) as unknown as typeof fetch;

    renderBuildComplete();
    await waitFor(() => expect(screen.getByTestId('build-complete')).toBeInTheDocument());

    expect(screen.getByText('Build Complete')).toBeInTheDocument();
    expect(screen.getByText('TestApp')).toBeInTheDocument();
  });

  it('displays cost summary', async () => {
    global.fetch = vi.fn((url: string | URL | Request) => {
      const u = typeof url === 'string' ? url : url.toString();
      if (u.includes('/summary')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSummary) } as Response);
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockInstructions) } as Response);
    }) as unknown as typeof fetch;

    renderBuildComplete();
    await waitFor(() => expect(screen.getByTestId('summary-cost')).toBeInTheDocument());

    expect(screen.getByText('$2.63')).toBeInTheDocument();
  });

  it('displays deployment instructions', async () => {
    global.fetch = vi.fn((url: string | URL | Request) => {
      const u = typeof url === 'string' ? url : url.toString();
      if (u.includes('/summary')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSummary) } as Response);
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockInstructions) } as Response);
    }) as unknown as typeof fetch;

    renderBuildComplete();
    await waitFor(() => expect(screen.getByTestId('deploy-instructions')).toBeInTheDocument());

    expect(screen.getByText(/Prerequisites/)).toBeInTheDocument();
  });

  it('shows status banner for completed build', async () => {
    global.fetch = vi.fn((url: string | URL | Request) => {
      const u = typeof url === 'string' ? url : url.toString();
      if (u.includes('/summary')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSummary) } as Response);
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockInstructions) } as Response);
    }) as unknown as typeof fetch;

    renderBuildComplete();
    await waitFor(() => expect(screen.getByTestId('build-status-banner')).toBeInTheDocument());

    expect(screen.getByText('COMPLETED')).toBeInTheDocument();
    expect(screen.getByText('All phases passed')).toBeInTheDocument();
  });

  it('shows failed status for failed build', async () => {
    const failedSummary = {
      ...mockSummary,
      build: { ...mockSummary.build, status: 'failed', error_detail: 'RISK_EXCEEDS_SCOPE' },
    };
    global.fetch = vi.fn((url: string | URL | Request) => {
      const u = typeof url === 'string' ? url : url.toString();
      if (u.includes('/summary')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(failedSummary) } as Response);
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockInstructions) } as Response);
    }) as unknown as typeof fetch;

    renderBuildComplete();
    await waitFor(() => expect(screen.getByTestId('build-status-banner')).toBeInTheDocument());

    expect(screen.getByText('Build Failed')).toBeInTheDocument();
    expect(screen.getByText('FAILED')).toBeInTheDocument();
  });
});
