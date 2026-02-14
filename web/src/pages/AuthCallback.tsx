import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

function AuthCallback() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { login } = useAuth();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const code = searchParams.get('code');
    const state = searchParams.get('state');

    if (!code || !state) {
      setError('Missing authorization parameters');
      return;
    }

    fetch(`/auth/github/callback?code=${code}&state=${state}`)
      .then((res) => {
        if (!res.ok) throw new Error('Authentication failed');
        return res.json();
      })
      .then((data) => {
        login(data.token, data.user);
        navigate('/', { replace: true });
      })
      .catch((err) => {
        setError(err.message || 'Authentication failed');
      });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) {
    return (
      <div
        style={{
          background: '#0F172A',
          color: '#EF4444',
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '1rem',
        }}
      >
        <p>{error}</p>
        <a href="/login" style={{ color: '#2563EB' }}>
          Try again
        </a>
      </div>
    );
  }

  return (
    <div
      style={{
        background: '#0F172A',
        color: '#94A3B8',
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      Signing in...
    </div>
  );
}

export default AuthCallback;
