import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import Login from '../pages/Login';
import HealthBadge from '../components/HealthBadge';
import ConfirmDialog from '../components/ConfirmDialog';
import ResultBadge from '../components/ResultBadge';
import CheckResultCard from '../components/CheckResultCard';
import Skeleton, { SkeletonCard, SkeletonRow } from '../components/Skeleton';
import EmptyState from '../components/EmptyState';
import CreateProjectModal from '../components/CreateProjectModal';
import QuestionnaireModal from '../components/QuestionnaireModal';

describe('Login', () => {
  it('renders the sign in button', () => {
    render(<Login />);
    expect(screen.getByText('Sign in with GitHub')).toBeInTheDocument();
  });

  it('renders the ForgeGuard heading', () => {
    render(<Login />);
    expect(screen.getByText('ForgeGuard')).toBeInTheDocument();
  });
});

describe('HealthBadge', () => {
  it('renders with pending status', () => {
    render(<HealthBadge score="pending" />);
    const badge = screen.getByRole('status');
    expect(badge).toBeInTheDocument();
    expect(badge.getAttribute('aria-label')).toBe('Health: pending');
  });

  it('renders with green status', () => {
    render(<HealthBadge score="green" />);
    const badge = screen.getByRole('status');
    expect(badge.getAttribute('aria-label')).toBe('Health: green');
  });
});

describe('ConfirmDialog', () => {
  it('renders title and message', () => {
    render(
      <ConfirmDialog
        title="Test Title"
        message="Test message"
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText('Test Title')).toBeInTheDocument();
    expect(screen.getByText('Test message')).toBeInTheDocument();
  });
});

describe('ResultBadge', () => {
  it('renders PASS with green color', () => {
    render(<ResultBadge result="PASS" />);
    expect(screen.getByText('PASS')).toBeInTheDocument();
  });

  it('renders FAIL', () => {
    render(<ResultBadge result="FAIL" />);
    expect(screen.getByText('FAIL')).toBeInTheDocument();
  });

  it('renders PENDING when null', () => {
    render(<ResultBadge result={null} />);
    expect(screen.getByText('PENDING')).toBeInTheDocument();
  });
});

describe('CheckResultCard', () => {
  it('renders check info and detail', () => {
    render(
      <CheckResultCard
        check={{
          id: '1',
          check_code: 'A4',
          check_name: 'Boundary compliance',
          result: 'PASS',
          detail: null,
        }}
      />,
    );
    expect(screen.getByText('A4')).toBeInTheDocument();
    expect(screen.getByText('Boundary compliance')).toBeInTheDocument();
  });
});

describe('Skeleton', () => {
  it('renders skeleton element', () => {
    render(<Skeleton />);
    expect(screen.getByTestId('skeleton')).toBeInTheDocument();
  });

  it('renders SkeletonCard', () => {
    render(<SkeletonCard />);
    const skeletons = screen.getAllByTestId('skeleton');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it('renders SkeletonRow', () => {
    render(<SkeletonRow />);
    const skeletons = screen.getAllByTestId('skeleton');
    expect(skeletons.length).toBeGreaterThan(0);
  });
});

describe('EmptyState', () => {
  it('renders message', () => {
    render(<EmptyState message="Nothing here" />);
    expect(screen.getByText('Nothing here')).toBeInTheDocument();
  });

  it('renders empty-state test id', () => {
    render(<EmptyState message="Test" />);
    expect(screen.getByTestId('empty-state')).toBeInTheDocument();
  });

  it('renders action button when provided', () => {
    const fn = () => {};
    render(<EmptyState message="Empty" actionLabel="Add Item" onAction={fn} />);
    expect(screen.getByText('Add Item')).toBeInTheDocument();
  });
});

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ token: 'test-token', user: { id: '1', login: 'test' }, logout: () => {} }),
}));

describe('CreateProjectModal', () => {
  it('renders form inputs and source toggle', () => {
    render(<CreateProjectModal onClose={() => {}} onCreated={() => {}} />);
    expect(screen.getByTestId('project-name-input')).toBeInTheDocument();
    expect(screen.getByTestId('project-desc-input')).toBeInTheDocument();
    expect(screen.getByTestId('create-project-submit')).toBeInTheDocument();
    expect(screen.getByTestId('source-github')).toBeInTheDocument();
    expect(screen.getByTestId('source-local')).toBeInTheDocument();
  });

  it('shows error when submitting empty name', () => {
    render(<CreateProjectModal onClose={() => {}} onCreated={() => {}} />);
    fireEvent.click(screen.getByTestId('create-project-submit'));
    expect(screen.getByTestId('create-error')).toBeInTheDocument();
    expect(screen.getByText('Project name is required')).toBeInTheDocument();
  });

  it('calls onClose when cancel is clicked', () => {
    const onClose = vi.fn();
    render(<CreateProjectModal onClose={onClose} onCreated={() => {}} />);
    fireEvent.click(screen.getByText('Cancel'));
    expect(onClose).toHaveBeenCalled();
  });

  it('calls onClose when overlay is clicked', () => {
    const onClose = vi.fn();
    render(<CreateProjectModal onClose={onClose} onCreated={() => {}} />);
    fireEvent.click(screen.getByTestId('create-project-overlay'));
    expect(onClose).toHaveBeenCalled();
  });

  it('shows github create/select pills when source is GitHub', () => {
    render(<CreateProjectModal onClose={() => {}} onCreated={() => {}} />);
    expect(screen.getByTestId('gh-create')).toBeInTheDocument();
    expect(screen.getByTestId('gh-select')).toBeInTheDocument();
  });

  it('shows private toggle in create new mode', () => {
    render(<CreateProjectModal onClose={() => {}} onCreated={() => {}} />);
    expect(screen.getByTestId('private-toggle')).toBeInTheDocument();
  });

  it('shows local path input when source is Local', () => {
    render(<CreateProjectModal onClose={() => {}} onCreated={() => {}} />);
    fireEvent.click(screen.getByTestId('source-local'));
    expect(screen.getByTestId('local-path-input')).toBeInTheDocument();
    // GitHub options should be hidden
    expect(screen.queryByTestId('gh-create')).toBeNull();
  });

  it('shows error when local path is empty on submit', () => {
    render(<CreateProjectModal onClose={() => {}} onCreated={() => {}} />);
    fireEvent.change(screen.getByTestId('project-name-input'), { target: { value: 'Test' } });
    fireEvent.click(screen.getByTestId('source-local'));
    fireEvent.click(screen.getByTestId('create-project-submit'));
    expect(screen.getByText('Project path is required for local projects')).toBeInTheDocument();
  });

  it('shows error when no repo selected in select mode', () => {
    render(<CreateProjectModal onClose={() => {}} onCreated={() => {}} />);
    fireEvent.change(screen.getByTestId('project-name-input'), { target: { value: 'Test' } });
    fireEvent.click(screen.getByTestId('gh-select'));
    fireEvent.click(screen.getByTestId('create-project-submit'));
    expect(screen.getByText('Select a repository')).toBeInTheDocument();
  });
});

describe('QuestionnaireModal', () => {
  beforeEach(() => {
    /* Mock fetch: first call = state endpoint, subsequent = questionnaire POST */
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        if (url.includes('/questionnaire/state')) {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                current_section: 'product_intent',
                completed_sections: [],
                remaining_sections: [
                  'product_intent',
                  'tech_stack',
                  'database_schema',
                  'api_endpoints',
                  'ui_requirements',
                  'architectural_boundaries',
                  'deployment_target',
                  'phase_breakdown',
                ],
                is_complete: false,
                conversation_history: [],
              }),
          });
        }
        if (url.includes('/questionnaire') && !url.includes('/state')) {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                reply: 'Tell me about your product.',
                section: 'product_intent',
                completed_sections: [],
                remaining_sections: [
                  'product_intent',
                  'tech_stack',
                  'database_schema',
                  'api_endpoints',
                  'ui_requirements',
                  'architectural_boundaries',
                  'deployment_target',
                  'phase_breakdown',
                ],
                is_complete: false,
              }),
          });
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      }),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders modal with header, progress, input and send button', async () => {
    render(
      <QuestionnaireModal
        projectId="test-id"
        projectName="TestProject"
        onClose={() => {}}
        onContractsGenerated={() => {}}
      />,
    );
    expect(screen.getByTestId('questionnaire-modal')).toBeInTheDocument();
    expect(screen.getByTestId('questionnaire-progress')).toBeInTheDocument();
    expect(screen.getByTestId('questionnaire-input')).toBeInTheDocument();
    expect(screen.getByTestId('questionnaire-send')).toBeInTheDocument();
    expect(screen.getByText(/TestProject/)).toBeInTheDocument();
  });

  it('calls onClose when close button is clicked', async () => {
    const onClose = vi.fn();
    render(
      <QuestionnaireModal
        projectId="test-id"
        projectName="Test"
        onClose={onClose}
        onContractsGenerated={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId('questionnaire-close'));
    expect(onClose).toHaveBeenCalled();
  });

  it('calls onClose when overlay is clicked', async () => {
    const onClose = vi.fn();
    render(
      <QuestionnaireModal
        projectId="test-id"
        projectName="Test"
        onClose={onClose}
        onContractsGenerated={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId('questionnaire-overlay'));
    expect(onClose).toHaveBeenCalled();
  });

  it('has a voice toggle button', () => {
    render(
      <QuestionnaireModal
        projectId="test-id"
        projectName="Test"
        onClose={() => {}}
        onContractsGenerated={() => {}}
      />,
    );
    expect(screen.getByTestId('voice-toggle')).toBeInTheDocument();
  });

  it('shows generate banner when questionnaire is complete', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            current_section: null,
            completed_sections: [
              'product_intent',
              'tech_stack',
              'database_schema',
              'api_endpoints',
              'ui_requirements',
              'architectural_boundaries',
              'deployment_target',
              'phase_breakdown',
            ],
            remaining_sections: [],
            is_complete: true,
            conversation_history: [
              { role: 'assistant', content: 'All done!' },
            ],
          }),
      }),
    );

    render(
      <QuestionnaireModal
        projectId="test-id"
        projectName="Test"
        onClose={() => {}}
        onContractsGenerated={() => {}}
      />,
    );

    /* Wait for state to load */
    await vi.waitFor(() => {
      expect(screen.getByTestId('generate-banner')).toBeInTheDocument();
    });
    expect(screen.getByTestId('generate-contracts-btn')).toBeInTheDocument();
  });
});
