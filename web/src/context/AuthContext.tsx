import { createContext, useContext, useState, useEffect, type ReactNode } from 'react';

interface User {
  id: string;
  github_login: string;
  avatar_url: string | null;
}

interface AuthContextValue {
  token: string | null;
  user: User | null;
  login: (token: string, user: User) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() =>
    localStorage.getItem('forgeguard_token'),
  );
  const [user, setUser] = useState<User | null>(() => {
    const stored = localStorage.getItem('forgeguard_user');
    return stored ? JSON.parse(stored) : null;
  });

  const login = (newToken: string, newUser: User) => {
    setToken(newToken);
    setUser(newUser);
    localStorage.setItem('forgeguard_token', newToken);
    localStorage.setItem('forgeguard_user', JSON.stringify(newUser));
  };

  const logout = () => {
    setToken(null);
    setUser(null);
    localStorage.removeItem('forgeguard_token');
    localStorage.removeItem('forgeguard_user');
  };

  useEffect(() => {
    if (!token) return;
    // Validate token on mount by calling /auth/me
    fetch('/auth/me', {
      headers: { Authorization: `Bearer ${token}` },
    }).then((res) => {
      if (!res.ok) {
        logout();
      }
    }).catch(() => {
      // Network error -- keep token, user can retry
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <AuthContext.Provider value={{ token, user, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
