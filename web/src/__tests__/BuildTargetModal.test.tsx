import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import BuildTargetModal from '../components/BuildTargetModal';

describe('BuildTargetModal', () => {
  const mockConfirm = vi.fn();
  const mockCancel = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders three target options', () => {
    render(<BuildTargetModal onConfirm={mockConfirm} onCancel={mockCancel} />);
    expect(screen.getByText('New GitHub Repo')).toBeInTheDocument();
    expect(screen.getByText('Existing GitHub Repo')).toBeInTheDocument();
    expect(screen.getByText('Local Directory')).toBeInTheDocument();
  });

  it('renders the Build Target title', () => {
    render(<BuildTargetModal onConfirm={mockConfirm} onCancel={mockCancel} />);
    expect(screen.getByText('Build Target')).toBeInTheDocument();
  });

  it('shows correct placeholder for github_new (default)', () => {
    render(<BuildTargetModal onConfirm={mockConfirm} onCancel={mockCancel} />);
    const input = screen.getByTestId('target-ref-input') as HTMLInputElement;
    expect(input.placeholder).toBe('my-new-project');
  });

  it('changes placeholder when selecting existing repo', () => {
    render(<BuildTargetModal onConfirm={mockConfirm} onCancel={mockCancel} />);
    fireEvent.click(screen.getByTestId('target-github_existing'));
    const input = screen.getByTestId('target-ref-input') as HTMLInputElement;
    expect(input.placeholder).toBe('owner/repo-name');
  });

  it('changes placeholder when selecting local path', () => {
    render(<BuildTargetModal onConfirm={mockConfirm} onCancel={mockCancel} />);
    fireEvent.click(screen.getByTestId('target-local_path'));
    const input = screen.getByTestId('target-ref-input') as HTMLInputElement;
    expect(input.placeholder).toContain('Projects');
  });

  it('disables Start Build when input is empty', () => {
    render(<BuildTargetModal onConfirm={mockConfirm} onCancel={mockCancel} />);
    const btn = screen.getByTestId('target-confirm-btn');
    expect(btn).toBeDisabled();
  });

  it('enables Start Build when input has value', () => {
    render(<BuildTargetModal onConfirm={mockConfirm} onCancel={mockCancel} />);
    const input = screen.getByTestId('target-ref-input');
    fireEvent.change(input, { target: { value: 'my-repo' } });
    const btn = screen.getByTestId('target-confirm-btn');
    expect(btn).not.toBeDisabled();
  });

  it('calls onConfirm with target data', () => {
    render(<BuildTargetModal onConfirm={mockConfirm} onCancel={mockCancel} />);
    const input = screen.getByTestId('target-ref-input');
    fireEvent.change(input, { target: { value: 'my-repo' } });
    fireEvent.click(screen.getByTestId('target-confirm-btn'));
    expect(mockConfirm).toHaveBeenCalledWith({
      target_type: 'github_new',
      target_ref: 'my-repo',
    });
  });

  it('calls onCancel when cancel button clicked', () => {
    render(<BuildTargetModal onConfirm={mockConfirm} onCancel={mockCancel} />);
    fireEvent.click(screen.getByText('Cancel'));
    expect(mockCancel).toHaveBeenCalledOnce();
  });

  it('calls onCancel when close button (✕) clicked', () => {
    render(<BuildTargetModal onConfirm={mockConfirm} onCancel={mockCancel} />);
    fireEvent.click(screen.getByText('✕'));
    expect(mockCancel).toHaveBeenCalledOnce();
  });

  it('shows "Starting..." when starting prop is true', () => {
    render(<BuildTargetModal onConfirm={mockConfirm} onCancel={mockCancel} starting />);
    const input = screen.getByTestId('target-ref-input');
    fireEvent.change(input, { target: { value: 'blah' } });
    expect(screen.getByText('Starting...')).toBeInTheDocument();
  });

  it('clears input when switching target types', () => {
    render(<BuildTargetModal onConfirm={mockConfirm} onCancel={mockCancel} />);
    const input = screen.getByTestId('target-ref-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'my-repo' } });
    expect(input.value).toBe('my-repo');

    fireEvent.click(screen.getByTestId('target-local_path'));
    const inputAfter = screen.getByTestId('target-ref-input') as HTMLInputElement;
    expect(inputAfter.value).toBe('');
  });
});
