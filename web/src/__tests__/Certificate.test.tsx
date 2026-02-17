import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

declare const global: typeof globalThis;

import CertificateModal from '../components/CertificateModal';

const SAMPLE_CERT = {
  forge_seal: {
    version: '1.0',
    generated_at: '2025-01-01T00:00:00Z',
    integrity_hash: 'abc123def456abc123def456abc123def456abc123def456abc123def456abcd',
  },
  certificate: {
    project: { id: 'p-1', name: 'TestProject', description: null },
    verdict: 'CERTIFIED',
    overall_score: 96.3,
    dimensions: {
      build_integrity: { score: 89, weight: 0.2, details: {} },
      test_coverage: { score: 95, weight: 0.2, details: {} },
      audit_compliance: { score: 100, weight: 0.2, details: {} },
      governance: { score: 100, weight: 0.15, details: {} },
      security: { score: 100, weight: 0.15, details: {} },
      cost_efficiency: { score: 95, weight: 0.1, details: {} },
    },
    build_summary: {
      status: 'completed',
      phase: 'plan_execute',
      cost_usd: 1.5,
      files_written: 5,
      git_commits: 2,
      loop_count: 1,
    },
    builds_total: 3,
    contracts_count: 9,
    generated_at: '2025-01-01T00:00:00Z',
  },
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('CertificateModal', () => {
  it('shows loading spinner initially', () => {
    global.fetch = vi.fn(() => new Promise(() => {})) as any;
    render(
      <CertificateModal projectId="p-1" token="tok" onClose={() => {}} />,
    );
    expect(screen.getByText('Generating certificate…')).toBeInTheDocument();
  });

  it('renders certificate data after fetch', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(SAMPLE_CERT),
      }),
    ) as any;

    render(
      <CertificateModal projectId="p-1" token="tok" onClose={() => {}} />,
    );

    await waitFor(() => {
      expect(screen.getByText('CERTIFIED')).toBeInTheDocument();
    });
    expect(screen.getByText('96')).toBeInTheDocument();
    expect(screen.getByText('Dimensions')).toBeInTheDocument();
    expect(screen.getByText('Build Summary')).toBeInTheDocument();
  });

  it('shows error on fetch failure', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false,
        json: () => Promise.resolve({ detail: 'No builds yet' }),
      }),
    ) as any;

    render(
      <CertificateModal projectId="p-1" token="tok" onClose={() => {}} />,
    );

    await waitFor(() => {
      expect(screen.getByText('No builds yet')).toBeInTheDocument();
    });
  });

  it('calls onClose when close button clicked', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(SAMPLE_CERT),
      }),
    ) as any;

    const onClose = vi.fn();
    render(
      <CertificateModal projectId="p-1" token="tok" onClose={onClose} />,
    );

    await waitFor(() => {
      expect(screen.getByText('CERTIFIED')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('✕'));
    expect(onClose).toHaveBeenCalled();
  });

  it('renders download buttons', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(SAMPLE_CERT),
      }),
    ) as any;

    render(
      <CertificateModal projectId="p-1" token="tok" onClose={() => {}} />,
    );

    await waitFor(() => {
      expect(screen.getByText('⬇ JSON')).toBeInTheDocument();
      expect(screen.getByText('⬇ HTML')).toBeInTheDocument();
      expect(screen.getByText('⬇ Text')).toBeInTheDocument();
    });
  });

  it('shows stats row with builds total and contracts', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(SAMPLE_CERT),
      }),
    ) as any;

    render(
      <CertificateModal projectId="p-1" token="tok" onClose={() => {}} />,
    );

    await waitFor(() => {
      expect(screen.getByText('3')).toBeInTheDocument(); // builds_total
      expect(screen.getByText('9')).toBeInTheDocument(); // contracts_count
      expect(screen.getByText('Total Builds')).toBeInTheDocument();
      expect(screen.getByText('Contracts')).toBeInTheDocument();
    });
  });

  it('renders all 6 dimension score gauges', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(SAMPLE_CERT),
      }),
    ) as any;

    render(
      <CertificateModal projectId="p-1" token="tok" onClose={() => {}} />,
    );

    await waitFor(() => {
      expect(screen.getByText('build integrity')).toBeInTheDocument();
      expect(screen.getByText('test coverage')).toBeInTheDocument();
      expect(screen.getByText('audit compliance')).toBeInTheDocument();
      expect(screen.getByText('governance')).toBeInTheDocument();
      expect(screen.getByText('security')).toBeInTheDocument();
      expect(screen.getByText('cost efficiency')).toBeInTheDocument();
    });
  });
});
