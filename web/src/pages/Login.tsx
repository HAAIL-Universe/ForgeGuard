import '../index.css';

function Login() {
  const handleLogin = async () => {
    try {
      const res = await fetch('/auth/github');
      const data = await res.json();
      if (data.redirect_url) {
        window.location.href = data.redirect_url;
      }
    } catch {
      // Network error -- show nothing for now, toast in Phase 4
    }
  };

  return (
    <div
      style={{
        background: '#0F172A',
        color: '#F8FAFC',
        minHeight: '100vh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '2rem',
      }}
    >
      <h1 style={{ fontSize: '2.5rem', fontWeight: 700 }}>ForgeGuard</h1>
      <p style={{ color: '#94A3B8', maxWidth: '400px', textAlign: 'center' }}>
        Monitor your repos. Catch violations before they ship.
      </p>
      <button
        onClick={handleLogin}
        style={{
          background: '#2563EB',
          color: '#F8FAFC',
          border: 'none',
          borderRadius: '8px',
          padding: '12px 32px',
          fontSize: '1rem',
          fontWeight: 600,
          cursor: 'pointer',
        }}
      >
        Sign in with GitHub
      </button>
    </div>
  );
}

export default Login;
