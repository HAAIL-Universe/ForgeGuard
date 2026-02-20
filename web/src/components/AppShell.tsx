import { useState, useEffect, useCallback, Fragment, type ReactNode } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useWebSocket } from '../hooks/useWebSocket';
import HealthBadge from './HealthBadge';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

interface SidebarRepo {
  id: string;
  full_name: string;
  health_score: string;
}

interface AppShellProps {
  children: ReactNode;
  sidebarRepos?: SidebarRepo[];
  onReposChange?: () => void;
}

function AppShell({ children, sidebarRepos, onReposChange }: AppShellProps) {
  const { user, token, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [repos, setRepos] = useState<SidebarRepo[]>(sidebarRepos ?? []);
  const [buildBadge, setBuildBadge] = useState(0);

  // Listen for contract_progress WS events — show badge on Build tab
  useWebSocket(
    useCallback((data: { type: string; payload: unknown }) => {
      if (data.type !== 'contract_progress') return;
      const p = data.payload as { status?: string; index?: number; total?: number };
      // When the last contract finishes, show the badge
      if (p.status === 'done' && typeof p.index === 'number' && typeof p.total === 'number' && p.index === p.total - 1) {
        setBuildBadge((prev) => prev + 1);
      }
    }, []),
  );

  // Clear badge when navigating to /build
  useEffect(() => {
    if (location.pathname === '/build') setBuildBadge(0);
  }, [location.pathname]);

  useEffect(() => {
    if (sidebarRepos) {
      setRepos(sidebarRepos);
      return;
    }
    // Load repos for sidebar if not provided
    const ac = new AbortController();
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/repos`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: ac.signal,
        });
        if (!ac.signal.aborted && res.ok) {
          const data = await res.json();
          setRepos(data.items);
        }
      } catch {
        // best effort
      }
    };
    load();
    return () => ac.abort();
  }, [token, sidebarRepos]);

  // Responsive: collapse sidebar below 1024px
  useEffect(() => {
    const check = () => setCollapsed(window.innerWidth < 1024);
    check();
    window.addEventListener('resize', check);
    return () => window.removeEventListener('resize', check);
  }, []);

  const sidebarWidth = collapsed ? 48 : 240;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: '#0F172A', color: '#F8FAFC' }}>
      {/* Header */}
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '12px 24px',
          borderBottom: '1px solid #1E293B',
          background: '#0F172A',
          zIndex: 10,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <h1
            onClick={() => navigate('/')}
            style={{ fontSize: '1.15rem', fontWeight: 700, cursor: 'pointer', margin: 0 }}
          >
            ForgeGuard
          </h1>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {user?.avatar_url && (
            <img
              src={user.avatar_url}
              alt={user.github_login}
              style={{ width: 28, height: 28, borderRadius: '50%' }}
            />
          )}
          <button
            onClick={logout}
            style={{
              background: 'transparent',
              color: '#94A3B8',
              border: '1px solid #334155',
              borderRadius: '6px',
              padding: '4px 14px',
              cursor: 'pointer',
              fontSize: '0.8rem',
            }}
          >
            Logout
          </button>
        </div>
      </header>

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Sidebar */}
        <aside
          style={{
            width: sidebarWidth,
            minWidth: sidebarWidth,
            borderRight: '1px solid #1E293B',
            padding: collapsed ? '12px 0' : '16px 0',
            overflowY: 'auto',
            overflowX: 'hidden',
            flexShrink: 0,
            background: '#0F172A',
            display: 'flex',
            flexDirection: 'column',
            transition: 'width 0.2s ease, min-width 0.2s ease',
          }}
        >
          {/* Header row with REPOS label + collapse arrow */}
          <div style={{ padding: collapsed ? '0 0 8px' : '0 16px 12px', display: 'flex', alignItems: 'center', justifyContent: collapsed ? 'center' : 'space-between' }}>
            {!collapsed && (
              <span style={{ color: '#94A3B8', fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                Repos
              </span>
            )}
            <button
              onClick={() => setCollapsed(!collapsed)}
              aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              style={{
                background: 'transparent',
                color: '#64748B',
                border: 'none',
                cursor: 'pointer',
                fontSize: '0.85rem',
                padding: '2px 4px',
                borderRadius: '4px',
                lineHeight: 1,
                transition: 'color 0.15s',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.color = '#CBD5E1')}
              onMouseLeave={(e) => (e.currentTarget.style.color = '#64748B')}
            >
              {collapsed ? '▶' : '◀'}
            </button>
          </div>

          {/* Expanded content */}
          {!collapsed && (
            <>
              {repos.length === 0 ? (
                <div style={{ padding: '12px 16px', color: '#64748B', fontSize: '0.8rem' }}>
                  No repos connected
                </div>
              ) : (
                repos.map((repo, idx) => {
                  const isActive = location.pathname.startsWith(`/repos/${repo.id}`);
                  const shortName = repo.full_name.includes('/')
                    ? repo.full_name.split('/').pop()!
                    : repo.full_name;
                  return (
                    <Fragment key={repo.id}>
                      {idx > 0 && (
                        <div style={{ borderTop: '1px solid #1E293B', margin: '2px 16px' }} />
                      )}
                      <div
                        onClick={() => navigate(`/repos/${repo.id}`)}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '8px',
                          padding: '8px 16px',
                          cursor: 'pointer',
                          background: isActive ? '#1E293B' : 'transparent',
                          borderLeft: isActive ? '3px solid #2563EB' : '3px solid transparent',
                          transition: 'background 0.15s',
                          fontSize: '0.8rem',
                        }}
                      >
                        <HealthBadge score={repo.health_score} size={8} />
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {shortName}
                        </span>
                      </div>
                    </Fragment>
                  );
                })
              )}
              {/* Nav section */}
              <div
                style={{
                  borderTop: '1px solid #1E293B',
                  padding: '8px 0',
                  marginTop: 'auto',
                }}
              >
                <div
                  onClick={() => navigate('/')}
                  data-testid="nav-home"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '8px 16px',
                    cursor: 'pointer',
                    background: location.pathname === '/' ? '#1E293B' : 'transparent',
                    borderLeft: location.pathname === '/' ? '3px solid #2563EB' : '3px solid transparent',
                    transition: 'background 0.15s',
                    fontSize: '0.8rem',
                    color: location.pathname === '/' ? '#F8FAFC' : '#94A3B8',
                  }}
                >
                  <span style={{ fontSize: '0.9rem' }}>🏠</span>
                  <span>Home</span>
                </div>
                <div style={{ borderTop: '1px solid #1E293B', margin: '6px 0' }} />
                <div
                  onClick={() => navigate('/repos')}
                  data-testid="nav-repos"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '8px 16px',
                    cursor: 'pointer',
                    background: location.pathname === '/repos' ? '#1E293B' : 'transparent',
                    borderLeft: location.pathname === '/repos' ? '3px solid #2563EB' : '3px solid transparent',
                    transition: 'background 0.15s',
                    fontSize: '0.8rem',
                    color: location.pathname === '/repos' ? '#F8FAFC' : '#94A3B8',
                  }}
                >
                  <span style={{ fontSize: '0.9rem' }}>📁</span>
                  <span>Repos</span>
                </div>
                <div
                  onClick={() => navigate('/build')}
                  data-testid="nav-build"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '8px 16px',
                    cursor: 'pointer',
                    background: location.pathname === '/build' ? '#1E293B' : 'transparent',
                    borderLeft: location.pathname === '/build' ? '3px solid #2563EB' : '3px solid transparent',
                    transition: 'background 0.15s',
                    fontSize: '0.8rem',
                    color: location.pathname === '/build' ? '#F8FAFC' : '#94A3B8',
                    position: 'relative',
                  }}
                >
                  <span style={{ fontSize: '0.9rem' }}>🏗️</span>
                  <span>Build</span>
                  {buildBadge > 0 && (
                    <span
                      style={{
                        background: '#22C55E',
                        color: '#fff',
                        fontSize: '0.6rem',
                        fontWeight: 700,
                        width: '16px',
                        height: '16px',
                        borderRadius: '50%',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        marginLeft: 'auto',
                        flexShrink: 0,
                        lineHeight: 1,
                        animation: 'badge-pop 0.3s ease-out',
                      }}
                    >
                      {buildBadge}
                    </span>
                  )}
                </div>
                <div
                  onClick={() => navigate('/scout')}
                  data-testid="nav-scout"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '8px 16px',
                    cursor: 'pointer',
                    background: location.pathname.startsWith('/scout') ? '#1E293B' : 'transparent',
                    borderLeft: location.pathname.startsWith('/scout') ? '3px solid #2563EB' : '3px solid transparent',
                    transition: 'background 0.15s',
                    fontSize: '0.8rem',
                    color: location.pathname.startsWith('/scout') ? '#F8FAFC' : '#94A3B8',
                  }}
                >
                  <span style={{ fontSize: '0.9rem' }}>🔍</span>
                  <span>Scout</span>
                </div>
              </div>
              <div
                style={{
                  padding: '12px 16px',
                  borderTop: '1px solid #1E293B',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                }}
              >
                <div
                  onClick={() => navigate('/settings')}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    cursor: 'pointer',
                    flex: 1,
                    minWidth: 0,
                  }}
                  title={user?.github_login ?? 'Settings'}
                >
                  {user?.avatar_url && (
                    <img
                      src={user.avatar_url}
                      alt={user.github_login}
                      style={{ width: 22, height: 22, borderRadius: '50%', flexShrink: 0 }}
                    />
                  )}
                  <span style={{ color: '#CBD5E1', fontSize: '0.75rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {user?.github_login}
                  </span>
                </div>
                <button
                  onClick={() => navigate('/settings')}
                  title="Settings"
                  style={{
                    background: 'transparent',
                    border: 'none',
                    color: '#64748B',
                    cursor: 'pointer',
                    fontSize: '0.95rem',
                    padding: '4px',
                    flexShrink: 0,
                    lineHeight: 1,
                  }}
                >
                  ⚙
                </button>
              </div>
            </>
          )}

          {/* Collapsed: nav icons + avatar at the bottom */}
          {collapsed && (
            <div
              style={{
                marginTop: 'auto',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                borderTop: '1px solid #1E293B',
                padding: '10px 0',
                gap: '6px',
              }}
            >
              {[
                { icon: '🏠', path: '/', exact: true, label: 'Home' },
                { icon: '📁', path: '/repos', exact: true, label: 'Repos' },
                { icon: '🏗️', path: '/build', exact: true, label: 'Build', badge: buildBadge },
                { icon: '🔍', path: '/scout', exact: false, label: 'Scout' },
              ].map((item) => {
                const active = item.exact
                  ? location.pathname === item.path
                  : location.pathname.startsWith(item.path);
                return (
                  <div
                    key={item.path}
                    onClick={() => {
                      navigate(item.path);
                      if (item.path === '/build') setBuildBadge(0);
                    }}
                    title={item.label}
                    style={{
                      width: 36,
                      height: 36,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      cursor: 'pointer',
                      borderRadius: '8px',
                      background: active ? '#1E293B' : 'transparent',
                      position: 'relative',
                      transition: 'background 0.15s',
                      fontSize: '1rem',
                    }}
                  >
                    {item.icon}
                    {item.badge && item.badge > 0 ? (
                      <span
                        style={{
                          position: 'absolute',
                          top: 2,
                          right: 2,
                          background: '#22C55E',
                          color: '#fff',
                          fontSize: '0.5rem',
                          fontWeight: 700,
                          width: 14,
                          height: 14,
                          borderRadius: '50%',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          lineHeight: 1,
                        }}
                      >
                        {item.badge}
                      </span>
                    ) : null}
                  </div>
                );
              })}
              <div style={{ height: '8px' }} />
              {user?.avatar_url ? (
                <img
                  src={user.avatar_url}
                  alt={user.github_login}
                  onClick={() => navigate('/settings')}
                  title={user?.github_login ?? 'Settings'}
                  style={{ width: 28, height: 28, borderRadius: '50%', cursor: 'pointer' }}
                />
              ) : (
                <button
                  onClick={() => navigate('/settings')}
                  title="Settings"
                  style={{
                    background: 'transparent',
                    border: 'none',
                    color: '#64748B',
                    cursor: 'pointer',
                    fontSize: '1.1rem',
                    lineHeight: 1,
                  }}
                >
                  ⚙
                </button>
              )}
            </div>
          )}
        </aside>

        {/* Main */}
        <main style={{ flex: 1, overflow: 'auto' }}>
          {children}
        </main>
      </div>
    </div>
  );
}

export type { SidebarRepo };
export default AppShell;
