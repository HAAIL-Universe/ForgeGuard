/**
 * Tests for Phase 45 — Build Observability & Cognitive Dashboard.
 *
 * Tests the ReconSummary, InvariantStrip, TaskDAGPanel, and JournalTimeline
 * inline sections by rendering minimal DOM with the same structure as
 * BuildProgress.tsx.
 */
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { useState } from 'react';

/* ------------------------------------------------------------------ */
/*  Shared types (mirrored from BuildProgress)                        */
/* ------------------------------------------------------------------ */

interface ReconData {
  total_files: number;
  total_lines: number;
  test_count: number;
  symbols_count: number;
  tables: string[];
}

interface DAGTask {
  id: string;
  title: string;
  file_path: string | null;
  status: 'pending' | 'in_progress' | 'completed' | 'failed' | 'blocked' | 'skipped';
  depends_on: string[];
}

interface DAGProgressData {
  total: number;
  completed: number;
  failed: number;
  blocked: number;
  in_progress: number;
  pending: number;
  skipped: number;
  percentage: number;
}

interface InvariantEntry {
  passed: boolean;
  expected: number;
  actual: number;
  constraint: string;
}

interface JournalEvent {
  timestamp: string;
  message: string;
}

/* ------------------------------------------------------------------ */
/*  ReconSummary — inline section extracted for testing                */
/* ------------------------------------------------------------------ */

function ReconSummary({ data, compactionCount }: { data: ReconData | null; compactionCount: number }) {
  if (!data) return null;
  return (
    <div data-testid="recon-summary" style={{ display: 'flex', gap: '16px', fontSize: '0.72rem', color: '#64748B' }}>
      <span>📂 {data.total_files.toLocaleString()} files</span>
      <span>·</span>
      <span>{data.total_lines.toLocaleString()} lines</span>
      <span>·</span>
      <span>{data.symbols_count.toLocaleString()} symbols</span>
      <span>·</span>
      <span>{data.test_count.toLocaleString()} tests</span>
      <span>·</span>
      <span>{data.tables.length} tables</span>
      {compactionCount > 0 && (
        <>
          <span>·</span>
          <span style={{ color: '#A78BFA' }}>🔄 {compactionCount} compaction{compactionCount > 1 ? 's' : ''}</span>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  InvariantStrip — inline section extracted for testing              */
/* ------------------------------------------------------------------ */

function InvariantStrip({ invariants }: { invariants: Map<string, InvariantEntry> }) {
  if (invariants.size === 0) return null;
  return (
    <div data-testid="invariant-strip" style={{ display: 'flex', gap: '8px' }}>
      {Array.from(invariants.entries()).map(([name, inv]) => {
        const ok = inv.passed;
        const arrow = inv.constraint === 'monotonic_up' ? ' ↑' : inv.constraint === 'monotonic_down' ? ' ↓' : '';
        return (
          <span
            key={name}
            data-testid={`invariant-badge-${name}`}
            title={`${name}: expected ${inv.expected}, actual ${inv.actual} (${inv.constraint})`}
            style={{
              background: ok ? '#0D2818' : '#7F1D1D',
              color: ok ? '#22C55E' : '#FCA5A5',
            }}
          >
            {name.replace(/_/g, ' ')}: {inv.actual}{arrow} {ok ? '✓' : '✗'}
          </span>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  TaskDAGPanel — inline section extracted for testing                */
/* ------------------------------------------------------------------ */

function TaskDAGPanel({
  tasks,
  progress,
  initialExpanded,
}: {
  tasks: DAGTask[];
  progress: DAGProgressData | null;
  initialExpanded: boolean;
}) {
  const [expanded, setExpanded] = useState(initialExpanded);
  if (tasks.length === 0) return null;
  const icons: Record<string, string> = {
    pending: '⏳', in_progress: '⚙️', completed: '✅', failed: '❌', blocked: '🚫', skipped: '⏭',
  };
  return (
    <div data-testid="dag-panel">
      <div onClick={() => setExpanded(!expanded)} style={{ cursor: 'pointer' }}>
        <h3>
          Task DAG{progress ? ` (${progress.completed}/${progress.total} — ${Math.round(progress.percentage)}%)` : ''}
        </h3>
        <span>▼</span>
      </div>
      {expanded && progress && (
        <div data-testid="dag-progress-bar" style={{ width: `${progress.percentage}%` }} />
      )}
      {expanded && tasks.map((t) => (
        <div key={t.id} data-testid={`dag-task-${t.id}`}>
          <span>{icons[t.status] ?? '⏳'}</span>
          <span>{t.file_path ?? t.title}</span>
          {t.depends_on.length > 0 && <span>dep: {t.depends_on.length}</span>}
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  JournalTimeline — inline section extracted for testing             */
/* ------------------------------------------------------------------ */

function JournalTimeline({ events, initialExpanded }: { events: JournalEvent[]; initialExpanded: boolean }) {
  const [expanded, setExpanded] = useState(initialExpanded);
  if (events.length === 0) return null;
  return (
    <div data-testid="journal-timeline">
      <div onClick={() => setExpanded(!expanded)} style={{ cursor: 'pointer' }}>
        <h3>Journal ({events.length})</h3>
        <span>▼</span>
      </div>
      {expanded && events.map((e, i) => (
        <div key={i} data-testid={`journal-event-${i}`}>
          <span>●</span>
          <span>{e.timestamp}</span>
          <span>{e.message}</span>
        </div>
      ))}
    </div>
  );
}

/* ================================================================== */
/*  Tests                                                             */
/* ================================================================== */

describe('ReconSummary', () => {
  const recon: ReconData = {
    total_files: 142,
    total_lines: 18400,
    test_count: 48,
    symbols_count: 6,
    tables: ['users', 'repos', 'builds', 'audit_runs'],
  };

  it('renders nothing when data is null', () => {
    const { container } = render(<ReconSummary data={null} compactionCount={0} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders file count', () => {
    render(<ReconSummary data={recon} compactionCount={0} />);
    expect(screen.getByTestId('recon-summary')).toBeInTheDocument();
    expect(screen.getByText(/142/)).toBeInTheDocument();
  });

  it('renders line count with locale formatting', () => {
    render(<ReconSummary data={recon} compactionCount={0} />);
    expect(screen.getByText(/18,400/)).toBeInTheDocument();
  });

  it('renders test count', () => {
    render(<ReconSummary data={recon} compactionCount={0} />);
    expect(screen.getByText(/48 tests/)).toBeInTheDocument();
  });

  it('renders table count', () => {
    render(<ReconSummary data={recon} compactionCount={0} />);
    expect(screen.getByText('4 tables')).toBeInTheDocument();
  });

  it('renders symbol count', () => {
    render(<ReconSummary data={recon} compactionCount={0} />);
    expect(screen.getByText(/6 symbols/)).toBeInTheDocument();
  });

  it('hides compaction when count is 0', () => {
    render(<ReconSummary data={recon} compactionCount={0} />);
    expect(screen.queryByText(/compaction/)).toBeNull();
  });

  it('shows compaction count when > 0', () => {
    render(<ReconSummary data={recon} compactionCount={3} />);
    expect(screen.getByText(/3 compactions/)).toBeInTheDocument();
  });

  it('uses singular for 1 compaction', () => {
    render(<ReconSummary data={recon} compactionCount={1} />);
    expect(screen.getByText(/1 compaction$/)).toBeInTheDocument();
  });
});

describe('InvariantStrip', () => {
  it('renders nothing when empty', () => {
    const { container } = render(<InvariantStrip invariants={new Map()} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders passing invariant badge', () => {
    const inv = new Map<string, InvariantEntry>([
      ['backend_test_count', { passed: true, expected: 100, actual: 105, constraint: 'monotonic_up' }],
    ]);
    render(<InvariantStrip invariants={inv} />);
    expect(screen.getByTestId('invariant-strip')).toBeInTheDocument();
    const badge = screen.getByTestId('invariant-badge-backend_test_count');
    expect(badge).toBeInTheDocument();
    expect(badge.textContent).toContain('105');
    expect(badge.textContent).toContain('✓');
    expect(badge.textContent).toContain('↑');
  });

  it('renders failing invariant with ✗', () => {
    const inv = new Map<string, InvariantEntry>([
      ['syntax_errors', { passed: false, expected: 0, actual: 3, constraint: 'equal' }],
    ]);
    render(<InvariantStrip invariants={inv} />);
    const badge = screen.getByTestId('invariant-badge-syntax_errors');
    expect(badge.textContent).toContain('✗');
    expect(badge.style.background).toBe('rgb(127, 29, 29)');
  });

  it('renders multiple badges', () => {
    const inv = new Map<string, InvariantEntry>([
      ['backend_test_count', { passed: true, expected: 100, actual: 100, constraint: 'monotonic_up' }],
      ['syntax_errors', { passed: true, expected: 0, actual: 0, constraint: 'equal' }],
      ['total_files', { passed: true, expected: 50, actual: 52, constraint: 'monotonic_up' }],
    ]);
    render(<InvariantStrip invariants={inv} />);
    expect(screen.getAllByTestId(/invariant-badge-/)).toHaveLength(3);
  });

  it('shows ↓ arrow for monotonic_down', () => {
    const inv = new Map<string, InvariantEntry>([
      ['errors', { passed: true, expected: 5, actual: 3, constraint: 'monotonic_down' }],
    ]);
    render(<InvariantStrip invariants={inv} />);
    expect(screen.getByTestId('invariant-badge-errors').textContent).toContain('↓');
  });

  it('replaces underscores with spaces in name', () => {
    const inv = new Map<string, InvariantEntry>([
      ['backend_test_count', { passed: true, expected: 0, actual: 0, constraint: 'equal' }],
    ]);
    render(<InvariantStrip invariants={inv} />);
    expect(screen.getByTestId('invariant-badge-backend_test_count').textContent).toContain('backend test count');
  });

  it('sets title with full details', () => {
    const inv = new Map<string, InvariantEntry>([
      ['tests', { passed: true, expected: 50, actual: 55, constraint: 'monotonic_up' }],
    ]);
    render(<InvariantStrip invariants={inv} />);
    expect(screen.getByTestId('invariant-badge-tests').title).toBe('tests: expected 50, actual 55 (monotonic_up)');
  });
});

describe('TaskDAGPanel', () => {
  const tasks: DAGTask[] = [
    { id: 't0', title: 'Generate schema', file_path: 'db/schema.sql', status: 'completed', depends_on: [] },
    { id: 't1', title: 'Generate models', file_path: 'app/models.py', status: 'in_progress', depends_on: ['t0'] },
    { id: 't2', title: 'Generate routes', file_path: 'app/routes.py', status: 'pending', depends_on: ['t1'] },
  ];

  const progress: DAGProgressData = {
    total: 3, completed: 1, failed: 0, blocked: 0, in_progress: 1, pending: 1, skipped: 0, percentage: 33.3,
  };

  it('renders nothing when no tasks', () => {
    const { container } = render(<TaskDAGPanel tasks={[]} progress={null} initialExpanded={true} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders task list when expanded', () => {
    render(<TaskDAGPanel tasks={tasks} progress={progress} initialExpanded={true} />);
    expect(screen.getByTestId('dag-panel')).toBeInTheDocument();
    expect(screen.getByTestId('dag-task-t0')).toBeInTheDocument();
    expect(screen.getByTestId('dag-task-t1')).toBeInTheDocument();
    expect(screen.getByTestId('dag-task-t2')).toBeInTheDocument();
  });

  it('shows file paths for tasks', () => {
    render(<TaskDAGPanel tasks={tasks} progress={progress} initialExpanded={true} />);
    expect(screen.getByText('db/schema.sql')).toBeInTheDocument();
    expect(screen.getByText('app/models.py')).toBeInTheDocument();
  });

  it('shows progress percentage in header', () => {
    render(<TaskDAGPanel tasks={tasks} progress={progress} initialExpanded={true} />);
    expect(screen.getByText(/1\/3.*33%/)).toBeInTheDocument();
  });

  it('shows dependency count', () => {
    render(<TaskDAGPanel tasks={tasks} progress={progress} initialExpanded={true} />);
    const depLabels = screen.getAllByText(/dep: \d+/);
    expect(depLabels.length).toBe(2); // t1 and t2 each have 1 dep
  });

  it('shows status icons', () => {
    render(<TaskDAGPanel tasks={tasks} progress={progress} initialExpanded={true} />);
    expect(screen.getByTestId('dag-task-t0').textContent).toContain('✅');
    expect(screen.getByTestId('dag-task-t1').textContent).toContain('⚙️');
    expect(screen.getByTestId('dag-task-t2').textContent).toContain('⏳');
  });

  it('hides tasks when collapsed', () => {
    render(<TaskDAGPanel tasks={tasks} progress={progress} initialExpanded={false} />);
    expect(screen.getByTestId('dag-panel')).toBeInTheDocument();
    expect(screen.queryByTestId('dag-task-t0')).toBeNull();
  });

  it('toggles expanded on click', () => {
    render(<TaskDAGPanel tasks={tasks} progress={progress} initialExpanded={false} />);
    expect(screen.queryByTestId('dag-task-t0')).toBeNull();
    fireEvent.click(screen.getByText(/Task DAG/));
    expect(screen.getByTestId('dag-task-t0')).toBeInTheDocument();
  });

  it('shows progress bar when expanded', () => {
    render(<TaskDAGPanel tasks={tasks} progress={progress} initialExpanded={true} />);
    expect(screen.getByTestId('dag-progress-bar')).toBeInTheDocument();
  });

  it('falls back to title when file_path is null', () => {
    const nullPathTasks: DAGTask[] = [
      { id: 't0', title: 'Run migrations', file_path: null, status: 'pending', depends_on: [] },
    ];
    render(<TaskDAGPanel tasks={nullPathTasks} progress={null} initialExpanded={true} />);
    expect(screen.getByText('Run migrations')).toBeInTheDocument();
  });
});

describe('JournalTimeline', () => {
  const events: JournalEvent[] = [
    { timestamp: '10:00:05', message: 'Phase 2 checkpoint — 12 entries, hash abc123' },
    { timestamp: '10:05:30', message: 'Phase 3 checkpoint — 8 entries, hash def456' },
  ];

  it('renders nothing when no events', () => {
    const { container } = render(<JournalTimeline events={[]} initialExpanded={true} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders event count in header', () => {
    render(<JournalTimeline events={events} initialExpanded={true} />);
    expect(screen.getByText(/Journal \(2\)/)).toBeInTheDocument();
  });

  it('renders events when expanded', () => {
    render(<JournalTimeline events={events} initialExpanded={true} />);
    expect(screen.getByTestId('journal-event-0')).toBeInTheDocument();
    expect(screen.getByTestId('journal-event-1')).toBeInTheDocument();
    expect(screen.getByText(/Phase 2 checkpoint/)).toBeInTheDocument();
  });

  it('shows timestamps', () => {
    render(<JournalTimeline events={events} initialExpanded={true} />);
    expect(screen.getByText('10:00:05')).toBeInTheDocument();
    expect(screen.getByText('10:05:30')).toBeInTheDocument();
  });

  it('hides events when collapsed', () => {
    render(<JournalTimeline events={events} initialExpanded={false} />);
    expect(screen.getByTestId('journal-timeline')).toBeInTheDocument();
    expect(screen.queryByTestId('journal-event-0')).toBeNull();
  });

  it('toggles expand on click', () => {
    render(<JournalTimeline events={events} initialExpanded={false} />);
    expect(screen.queryByTestId('journal-event-0')).toBeNull();
    fireEvent.click(screen.getByText(/Journal/));
    expect(screen.getByTestId('journal-event-0')).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/*  WS event shape validation                                         */
/* ------------------------------------------------------------------ */

describe('WS event payload shapes', () => {
  it('recon_complete payload maps to ReconData', () => {
    const payload = {
      total_files: 142,
      total_lines: 18400,
      test_count: 48,
      symbols_count: 6,
      tables: ['users', 'repos'],
    };
    const data: ReconData = {
      total_files: payload.total_files,
      total_lines: payload.total_lines,
      test_count: payload.test_count,
      symbols_count: payload.symbols_count,
      tables: payload.tables,
    };
    expect(data.total_files).toBe(142);
    expect(data.tables).toHaveLength(2);
  });

  it('dag_initialized payload extracts tasks', () => {
    const payload = {
      phase: 'Phase 2',
      dag: {
        nodes: [
          { id: 't0', title: 'Gen file', file_path: 'a.py', status: 'pending', depends_on: [] },
        ],
      },
    };
    const nodes = payload.dag.nodes;
    const tasks: DAGTask[] = nodes.map((n) => ({
      id: n.id,
      title: n.title,
      file_path: n.file_path,
      status: n.status as DAGTask['status'],
      depends_on: n.depends_on,
    }));
    expect(tasks).toHaveLength(1);
    expect(tasks[0].id).toBe('t0');
  });

  it('invariant_check payload maps to InvariantEntry', () => {
    const payload = {
      name: 'backend_test_count',
      passed: true,
      expected: 100,
      actual: 105,
      constraint: 'monotonic_up',
    };
    const entry: InvariantEntry = {
      passed: Boolean(payload.passed),
      expected: payload.expected,
      actual: payload.actual,
      constraint: payload.constraint,
    };
    expect(entry.passed).toBe(true);
    expect(entry.constraint).toBe('monotonic_up');
  });

  it('dag_progress payload maps to DAGProgressData', () => {
    const payload = {
      total: 5, completed: 3, failed: 0, blocked: 0,
      in_progress: 1, pending: 1, skipped: 0, percentage: 60.0,
    };
    const data: DAGProgressData = { ...payload };
    expect(data.percentage).toBe(60.0);
    expect(data.completed).toBe(3);
  });
});
