import { useAuth } from '../context/AuthContext';

function Dashboard() {
  const { user, logout } = useAuth();

  return (
    <div style={{ background: '#0F172A', color: '#F8FAFC', minHeight: '100vh' }}>
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '16px 24px',
          borderBottom: '1px solid #1E293B',
        }}
      >
        <h1 style={{ fontSize: '1.25rem', fontWeight: 700 }}>ForgeGuard</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {user?.avatar_url && (
            <img
              src={user.avatar_url}
              alt={user.github_login}
              style={{ width: 32, height: 32, borderRadius: '50%' }}
            />
          )}
          <span style={{ color: '#94A3B8' }}>{user?.github_login}</span>
          <button
            onClick={logout}
            style={{
              background: 'transparent',
              color: '#94A3B8',
              border: '1px solid #334155',
              borderRadius: '6px',
              padding: '6px 16px',
              cursor: 'pointer',
              fontSize: '0.875rem',
            }}
          >
            Logout
          </button>
        </div>
      </header>
      <main
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: 'calc(100vh - 65px)',
          color: '#94A3B8',
        }}
      >
        <p>No repos connected yet. Connect a repo to get started.</p>
      </main>
    </div>
  );
}

export default Dashboard;
