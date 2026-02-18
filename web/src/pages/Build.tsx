import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useWebSocket } from '../hooks/useWebSocket';
import AppShell from '../components/AppShell';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

interface ProjectSummary {
  id: string;
  name: string;
  status?: string;
  build_mode?: string;
  latest_build_status?: string;
  has_contracts?: boolean;
}

/* ── Mini-build journey steps ─────────────────────────────────────────── */

const JOURNEY_STEPS = [
  {
    number: '1',
    title: 'Create a Project',
    description: 'Give your project a name and connect it to a GitHub repo (or start local).',
    icon: '📋',
    color: '#2563EB',
  },
  {
    number: '2',
    title: 'Questionnaire (Free)',
    description:
      'An AI assistant walks you through key decisions — product intent, tech stack, database, API design, UI, and deployment. Takes 5-10 minutes.',
    icon: '💬',
    color: '#7C3AED',
  },
  {
    number: '3',
    title: 'Generate Contracts',
    description:
      'Forge produces 9 governance files — blueprint, manifesto, stack, schema, physics, boundaries, UI spec, phases, and a builder directive. These become the source of truth.',
    icon: '📜',
    color: '#059669',
  },
  {
    number: '4',
    title: 'Build (Autonomous)',
    description:
      'The AI builder reads your contracts and works through each phase — scaffolding, models, API, frontend, tests — with real-time streaming and governance audits after every phase.',
    icon: '⚡',
    color: '#D97706',
  },
  {
    number: '5',
    title: 'Ship It',
    description:
      'Code is committed to your repo as it passes audits. Review the build summary, check your Forge Seal, and deploy.',
    icon: '🚀',
    color: '#DC2626',
  },
];

/* ── The 9 Contract types ─────────────────────────────────────────────── */

const CONTRACTS = [
  { name: 'Blueprint', file: 'blueprint.md', description: 'Product intent, MVP scope, hard boundaries' },
  { name: 'Manifesto', file: 'manifesto.md', description: 'Non-negotiable engineering principles' },
  { name: 'Stack', file: 'stack.md', description: 'Language, framework, DB, auth, deployment decisions' },
  { name: 'Schema', file: 'schema.md', description: 'Canonical database tables and indexes' },
  { name: 'Physics', file: 'physics.yaml', description: 'API specification — endpoints, shapes, auth' },
  { name: 'Boundaries', file: 'boundaries.json', description: 'Layer rules enforced by audit' },
  { name: 'UI', file: 'ui.md', description: 'Shell layout, screens, routes, theme, responsive' },
  { name: 'Phases', file: 'phases.md', description: 'Ordered build phases with exit criteria' },
  { name: 'Builder Directive', file: 'builder_directive.md', description: 'System prompt for the autonomous builder' },
];

/* ── Page component ───────────────────────────────────────────────────── */

function Build() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchProjects = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/projects`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setProjects(data.items ?? data ?? []);
      }
    } catch {
      /* best effort */
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    fetchProjects();
  }, [fetchProjects]);

  // Re-fetch when contracts finish generating (WS notification)
  useWebSocket(
    useCallback((data: { type: string; payload: unknown }) => {
      if (data.type !== 'contract_progress') return;
      const p = data.payload as { status?: string; index?: number; total?: number };
      if (p.status === 'done' && typeof p.index === 'number' && typeof p.total === 'number' && p.index === p.total - 1) {
        fetchProjects();
      }
    }, [fetchProjects]),
  );

  // Find active mini build with contracts ready
  const activeMini = projects.find(
    (p) => p.build_mode === 'mini' && p.status === 'contracts_ready',
  );

  // Card hover helpers
  const cardBase: React.CSSProperties = {
    background: '#1E293B',
    borderRadius: '10px',
    padding: '20px',
    cursor: 'pointer',
    transition: 'background 0.15s, transform 0.15s',
    border: '1px solid #334155',
  };

  const onHover = (e: React.MouseEvent<HTMLDivElement>) => {
    (e.currentTarget as HTMLDivElement).style.background = '#334155';
    (e.currentTarget as HTMLDivElement).style.transform = 'translateY(-1px)';
  };
  const onLeave = (e: React.MouseEvent<HTMLDivElement>) => {
    (e.currentTarget as HTMLDivElement).style.background = '#1E293B';
    (e.currentTarget as HTMLDivElement).style.transform = 'translateY(0)';
  };

  return (
    <AppShell>
      <div style={{ padding: '32px 24px', maxWidth: '960px', margin: '0 auto' }}>
        {/* ── Hero ─────────────────────────────────────────────────── */}
        <div style={{ marginBottom: '32px' }}>
          <h1 style={{ margin: '0 0 8px', fontSize: '1.5rem', fontWeight: 700 }}>
            🏗️ Forge Build
          </h1>
          <p style={{ margin: 0, color: '#94A3B8', fontSize: '0.9rem', lineHeight: 1.6 }}>
            Forge builds software autonomously from contracts — governance files
            generated from a quick AI-guided questionnaire. The builder reads
            your contracts, works through phases, runs audits after each one,
            and commits passing code to your repo.
          </p>
        </div>

        {/* ── Mini Build callout ───────────────────────────────────── */}
        <div
          style={{
            background: 'linear-gradient(135deg, #1E293B 0%, #0F172A 100%)',
            borderRadius: '12px',
            padding: '24px',
            marginBottom: '32px',
            border: '1px solid #2563EB',
            position: 'relative',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              position: 'absolute',
              top: '12px',
              right: '16px',
              background: '#2563EB',
              color: '#fff',
              fontSize: '0.65rem',
              fontWeight: 700,
              padding: '3px 10px',
              borderRadius: '10px',
              textTransform: 'uppercase',
              letterSpacing: '0.5px',
            }}
          >
            Available Now
          </div>
          <h2 style={{ margin: '0 0 8px', fontSize: '1.1rem', fontWeight: 700 }}>
            ⚡ Mini Build
          </h2>
          <p style={{ margin: '0 0 12px', color: '#CBD5E1', fontSize: '0.85rem', lineHeight: 1.5 }}>
            Get a working proof-of-concept in minutes. The mini build runs a
            shortened questionnaire (product intent + UI flow), auto-selects a
            tech stack, and produces a Docker-ready scaffold — backend, frontend,
            database, and API — in just 2 phases.
          </p>
          <div style={{ display: 'flex', gap: '24px', flexWrap: 'wrap', marginBottom: '16px', alignItems: 'flex-end' }}>
            {[
              { label: '~5 min', sublabel: 'Questionnaire' },
              { label: '2 phases', sublabel: 'Scaffold' },
              { label: '~$1-3', sublabel: 'Token cost' },
              { label: 'Docker-ready', sublabel: 'Output' },
            ].map((stat) => (
              <div key={stat.label}>
                <div style={{ fontSize: '1rem', fontWeight: 700, color: '#F8FAFC' }}>
                  {stat.label}
                </div>
                <div style={{ fontSize: '0.7rem', color: '#64748B' }}>{stat.sublabel}</div>
              </div>
            ))}
            {activeMini && (
              <div style={{ marginLeft: 'auto' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span style={{ color: '#22C55E', fontSize: '1rem', lineHeight: 1 }}>✓</span>
                  <span style={{ fontSize: '0.85rem', fontWeight: 600, color: '#22C55E' }}>
                    Contracts Generated
                  </span>
                </div>
              </div>
            )}
          </div>
          <button
            onClick={() => activeMini ? navigate(`/projects/${activeMini.id}`) : navigate('/repos')}
            style={{
              background: activeMini ? '#22C55E' : '#2563EB',
              color: '#fff',
              border: 'none',
              borderRadius: '8px',
              padding: '10px 20px',
              cursor: 'pointer',
              fontSize: '0.85rem',
              fontWeight: 600,
              transition: 'background 0.2s',
            }}
          >
            {activeMini ? 'Continue Mini Build →' : 'Start a Mini Build →'}
          </button>
        </div>

        {/* ── Full Build coming soon ───────────────────────────────── */}
        <div
          style={{
            background: '#1E293B',
            borderRadius: '12px',
            padding: '24px',
            marginBottom: '32px',
            border: '1px dashed #334155',
            opacity: 0.75,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
            <h2 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 700 }}>
              🔮 Full Build
            </h2>
            <span
              style={{
                background: '#334155',
                color: '#94A3B8',
                fontSize: '0.65rem',
                fontWeight: 700,
                padding: '3px 10px',
                borderRadius: '10px',
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
              }}
            >
              Coming Soon
            </span>
          </div>
          <p style={{ margin: 0, color: '#94A3B8', fontSize: '0.85rem', lineHeight: 1.5 }}>
            The full build expands the questionnaire to 7 sections, generates
            production-depth contracts, and works through 6-12 phases with
            comprehensive test suites, governance audits, and a Forge Seal
            certificate on completion. Everything the mini build does, but at
            production scale.
          </p>
        </div>

        {/* ── How it works ─────────────────────────────────────────── */}
        <div style={{ marginBottom: '32px' }}>
          <h2 style={{ margin: '0 0 16px', fontSize: '1rem', fontWeight: 600, color: '#CBD5E1' }}>
            How It Works
          </h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
            {JOURNEY_STEPS.map((step, i) => (
              <div
                key={step.number}
                style={{
                  display: 'flex',
                  gap: '16px',
                  padding: '16px 20px',
                  background: '#1E293B',
                  borderRadius: i === 0 ? '10px 10px 2px 2px' : i === JOURNEY_STEPS.length - 1 ? '2px 2px 10px 10px' : '2px',
                }}
              >
                <div
                  style={{
                    width: '32px',
                    height: '32px',
                    borderRadius: '50%',
                    background: step.color,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: '0.9rem',
                    flexShrink: 0,
                  }}
                >
                  {step.icon}
                </div>
                <div>
                  <div style={{ fontWeight: 600, fontSize: '0.85rem', marginBottom: '4px' }}>
                    Step {step.number}: {step.title}
                  </div>
                  <div style={{ color: '#94A3B8', fontSize: '0.8rem', lineHeight: 1.4 }}>
                    {step.description}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* ── Contracts explained ──────────────────────────────────── */}
        <div style={{ marginBottom: '32px' }}>
          <h2 style={{ margin: '0 0 6px', fontSize: '1rem', fontWeight: 600, color: '#CBD5E1' }}>
            The 9 Forge Contracts
          </h2>
          <p style={{ margin: '0 0 16px', color: '#64748B', fontSize: '0.8rem', lineHeight: 1.4 }}>
            Contracts are the source of truth. The builder cannot write a line
            of code that isn't justified by a contract. They're generated from
            your questionnaire answers and can be edited before building.
          </p>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
              gap: '8px',
            }}
          >
            {CONTRACTS.map((contract) => (
              <div
                key={contract.name}
                style={{
                  background: '#1E293B',
                  borderRadius: '8px',
                  padding: '14px 16px',
                  border: '1px solid #334155',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                  <span style={{ fontWeight: 600, fontSize: '0.85rem' }}>{contract.name}</span>
                  <span style={{ color: '#64748B', fontSize: '0.7rem', fontFamily: 'monospace' }}>
                    {contract.file}
                  </span>
                </div>
                <div style={{ color: '#94A3B8', fontSize: '0.78rem', lineHeight: 1.3 }}>
                  {contract.description}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* ── Your projects (quick jump) ───────────────────────────── */}
        {!loading && projects.length > 0 && (
          <div style={{ marginBottom: '32px' }}>
            <h2 style={{ margin: '0 0 16px', fontSize: '1rem', fontWeight: 600, color: '#CBD5E1' }}>
              Your Projects
            </h2>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
                gap: '12px',
              }}
            >
              {projects.map((project) => (
                <div
                  key={project.id}
                  onClick={() => navigate(`/projects/${project.id}`)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => e.key === 'Enter' && navigate(`/projects/${project.id}`)}
                  onMouseEnter={onHover}
                  onMouseLeave={onLeave}
                  style={cardBase}
                >
                  <div style={{ fontWeight: 600, fontSize: '0.9rem', marginBottom: '4px' }}>
                    {project.name}
                  </div>
                  <div style={{ color: '#64748B', fontSize: '0.75rem' }}>
                    {project.latest_build_status
                      ? `Last build: ${project.latest_build_status}`
                      : 'No builds yet'}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── Pricing note ─────────────────────────────────────────── */}
        <div
          style={{
            background: '#1E293B',
            borderRadius: '10px',
            padding: '20px',
            border: '1px solid #334155',
            display: 'flex',
            alignItems: 'flex-start',
            gap: '12px',
          }}
        >
          <span style={{ fontSize: '1.1rem', flexShrink: 0 }}>💡</span>
          <div>
            <div style={{ fontWeight: 600, fontSize: '0.85rem', marginBottom: '4px' }}>
              Bring Your Own Key (BYOK)
            </div>
            <div style={{ color: '#94A3B8', fontSize: '0.8rem', lineHeight: 1.5 }}>
              Forge uses your Anthropic API key — you pay only for the tokens
              your build consumes. Questionnaires and audits are free. A mini
              build typically costs $1-3, a full build $5-20 depending on
              complexity. You can set a personal spend cap in Settings.
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

export default Build;
