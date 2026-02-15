import { useState, useEffect, useCallback, type ReactNode } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
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

  useEffect(() => {
    if (sidebarRepos) {
      setRepos(sidebarRepos);
      return;
    }
    // Load repos for sidebar if not provided
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/repos`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const data = await res.json();
          setRepos(data.items);
        }
      } catch {
        // best effort
      }
    };
    load();
  }, [token, sidebarRepos]);

  // Responsive: collapse sidebar below 1024px
  useEffect(() => {
    const check = () => setCollapsed(window.innerWidth < 1024);
    check();
    window.addEventListener('resize', check);
    return () => window.removeEventListener('resize', check);
  }, []);

  const sidebarWidth = collapsed ? 0 : 240;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh', background: '#0F172A', color: '#F8FAFC' }}>
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
          {collapsed && (
            <button
              onClick={() => setCollapsed(false)}
              aria-label="Open menu"
              style={{
                background: 'transparent',
                color: '#94A3B8',
                border: '1px solid #334155',
                borderRadius: '6px',
                padding: '4px 8px',
                cursor: 'pointer',
                fontSize: '1rem',
              }}
            >
              &#9776;
            </button>
          )}
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
          <span style={{ color: '#94A3B8', fontSize: '0.85rem' }}>{user?.github_login}</span>
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

      <div style={{ display: 'flex', flex: 1 }}>
        {/* Sidebar */}
        {!collapsed && (
          <aside
            style={{
              width: sidebarWidth,
              borderRight: '1px solid #1E293B',
              padding: '16px 0',
              overflowY: 'auto',
              flexShrink: 0,
              background: '#0F172A',
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            <div style={{ padding: '0 16px 12px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ color: '#94A3B8', fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                Repos
              </span>
              {window.innerWidth < 1024 && (
                <button
                  onClick={() => setCollapsed(true)}
                  aria-label="Close menu"
                  style={{
                    background: 'transparent',
                    color: '#94A3B8',
                    border: 'none',
                    cursor: 'pointer',
                    fontSize: '1rem',
                  }}
                >
                  &times;
                </button>
              )}
            </div>
            {repos.length === 0 ? (
              <div style={{ padding: '12px 16px', color: '#64748B', fontSize: '0.8rem' }}>
                No repos connected
              </div>
            ) : (
              repos.map((repo) => {
                const isActive = location.pathname.startsWith(`/repos/${repo.id}`);
                return (
                  <div
                    key={repo.id}
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
                      {repo.full_name}
                    </span>
                  </div>
                );
              })
            )}
            <div
              style={{
                marginTop: 'auto',
                padding: '12px 16px',
                borderTop: '1px solid #1E293B',
                color: '#64748B',
                fontSize: '0.7rem',
              }}
            >
              v0.1.0
            </div>
          </aside>
        )}

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
