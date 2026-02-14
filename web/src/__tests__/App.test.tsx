import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import Login from '../pages/Login';

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
