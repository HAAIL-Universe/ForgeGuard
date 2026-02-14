import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import Login from '../pages/Login';
import HealthBadge from '../components/HealthBadge';
import ConfirmDialog from '../components/ConfirmDialog';

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
