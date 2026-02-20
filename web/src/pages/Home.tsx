import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useWebSocket } from '../hooks/useWebSocket';
import AppShell from '../components/AppShell';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

interface Stats {
  repos: number;
  projects: number;
  recentAudits: number;
  activeBuilds: number;
}

function Home() {
  const { token, user } = useAuth();
  const navigate = useNavigate();
  const [stats, setStats] = useState<Stats>({ repos: 0, projects: 0, recentAudits: 0, activeBuilds: 0 });
  const [loading, setLoading] = useState(true);

  const fetchStats = useCallback(async () => {
    try {
      const [reposRes, projectsRes] = await Promise.all([
        fetch(`${API_BASE}/repos`, { headers: { Authorization: `Bearer ${token}` } }),
        fetch(`${API_BASE}/projects`, { headers: { Authorization: `Bearer ${token}` } }),
      ]);

      const repos = reposRes.ok ? await reposRes.json() : { items: [] };
      const projects = projectsRes.ok ? await projectsRes.json() : { items: [] };

      const repoItems = repos.items ?? repos ?? [];
      const projectItems = projects.items ?? projects ?? [];

      // Count active builds (running, pending, or paused)
      const activeBuilds = projectItems.filter(
        (p: { latest_build_status?: string }) =>
          p.latest_build_status === 'running' || p.latest_build_status === 'pending' || p.latest_build_status === 'paused',
      ).length;

      // Count repos that have been audited (have a last_audit_at timestamp)
      const recentAudits = repoItems.filter(
        (r: { last_audit_at?: string }) => r.last_audit_at,
      ).length;

      setStats({
        repos: repoItems.length,
        projects: projectItems.length,
        recentAudits,
        activeBuilds,
      });
    } catch {
      // best effort
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    fetchStats();
  }, [fetchStats]);

  // Live-refresh on WS events
  useWebSocket(
    useCallback(
      (data) => {
        if (
          data.type === 'audit_update' ||
          data.type === 'build_complete' ||
          data.type === 'build_error' ||
          data.type === 'build_started'
        ) {
          fetchStats();
        }
      },
      [fetchStats],
    ),
  );

  const greeting = user?.github_login ? `Welcome back, ${user.github_login}` : 'Welcome to ForgeGuard';

  const statCards: { label: string; value: number; color: string; icon: string }[] = [
    { label: 'Connected Repos', value: stats.repos, color: '#2563EB', icon: '📦' },
    { label: 'Projects', value: stats.projects, color: '#7C3AED', icon: '🏗️' },
    { label: 'Audited Repos', value: stats.recentAudits, color: '#059669', icon: '✅' },
    { label: 'Active Builds', value: stats.activeBuilds, color: '#D97706', icon: '⚡' },
  ];

  const quickActions: { label: string; description: string; icon: string; onClick: () => void }[] = [
    {
      label: 'Manage Repos',
      description: 'Connect, view, and manage your GitHub repositories',
      icon: '📁',
      onClick: () => navigate('/repos'),
    },
    {
      label: 'Run Scout',
      description: 'Deep-scan a repo for upgrade opportunities',
      icon: '🔍',
      onClick: () => navigate('/scout'),
    },
    {
      label: 'Settings',
      description: 'API keys, spend caps, and account preferences',
      icon: '⚙️',
      onClick: () => navigate('/settings'),
    },
  ];

  return (
    <AppShell>
      <div style={{ padding: '32px 24px', maxWidth: '960px', margin: '0 auto' }}>
        {/* Hero */}
        <div style={{ marginBottom: '32px' }}>
          <h1 style={{ margin: '0 0 8px', fontSize: '1.5rem', fontWeight: 700 }}>{greeting}</h1>
          <p style={{ margin: 0, color: '#94A3B8', fontSize: '0.9rem', lineHeight: 1.5 }}>
            AI-powered code governance — audits, upgrades, and builds managed from one place.
          </p>
        </div>

        {/* Stat cards */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
            gap: '16px',
            marginBottom: '32px',
          }}
        >
          {statCards.map((card) => (
            <div
              key={card.label}
              style={{
                background: '#1E293B',
                borderRadius: '10px',
                padding: '20px',
                borderLeft: `4px solid ${card.color}`,
                transition: 'transform 0.15s',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                <span style={{ fontSize: '1.2rem' }}>{card.icon}</span>
                <span style={{ color: '#94A3B8', fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  {card.label}
                </span>
              </div>
              <div style={{ fontSize: '1.8rem', fontWeight: 700, color: '#F8FAFC' }}>
                {loading ? '–' : card.value}
              </div>
            </div>
          ))}
        </div>

        {/* Quick actions */}
        <div style={{ marginBottom: '32px' }}>
          <h2 style={{ margin: '0 0 16px', fontSize: '1rem', fontWeight: 600, color: '#CBD5E1' }}>Quick Actions</h2>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
              gap: '12px',
            }}
          >
            {quickActions.map((action) => (
              <div
                key={action.label}
                onClick={action.onClick}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && action.onClick()}
                style={{
                  background: '#1E293B',
                  borderRadius: '10px',
                  padding: '20px',
                  cursor: 'pointer',
                  transition: 'background 0.15s, transform 0.15s',
                  border: '1px solid #334155',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLDivElement).style.background = '#334155';
                  (e.currentTarget as HTMLDivElement).style.transform = 'translateY(-1px)';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLDivElement).style.background = '#1E293B';
                  (e.currentTarget as HTMLDivElement).style.transform = 'translateY(0)';
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '6px' }}>
                  <span style={{ fontSize: '1.2rem' }}>{action.icon}</span>
                  <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>{action.label}</span>
                </div>
                <p style={{ margin: 0, color: '#94A3B8', fontSize: '0.8rem', lineHeight: 1.4 }}>
                  {action.description}
                </p>
              </div>
            ))}
          </div>
        </div>

        {/* Placeholder for future content */}
        <div
          style={{
            background: '#1E293B',
            borderRadius: '10px',
            padding: '24px',
            border: '1px dashed #334155',
            textAlign: 'center',
          }}
        >
          <p style={{ margin: 0, color: '#64748B', fontSize: '0.85rem' }}>
            More dashboard content coming soon — activity feed, health overview, and build history.
          </p>
        </div>
      </div>
    </AppShell>
  );
}

export default Home;
