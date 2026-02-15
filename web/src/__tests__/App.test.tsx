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
  it('renders form inputs', () => {
    render(<CreateProjectModal onClose={() => {}} onCreated={() => {}} />);
    expect(screen.getByTestId('project-name-input')).toBeInTheDocument();
    expect(screen.getByTestId('project-desc-input')).toBeInTheDocument();
    expect(screen.getByTestId('create-project-submit')).toBeInTheDocument();
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
});
