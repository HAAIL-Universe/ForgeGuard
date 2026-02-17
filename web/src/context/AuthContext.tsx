import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

interface User {
  id: string;
  github_login: string;
  avatar_url: string | null;
  has_anthropic_key?: boolean;
  has_anthropic_key_2?: boolean;
  audit_llm_enabled?: boolean;
  build_spend_cap?: number | null;
}

interface AuthContextValue {
  token: string | null;
  user: User | null;
  /** True while the initial /auth/me validation is in-flight. */
  loading: boolean;
  login: (token: string, user: User) => void;
  logout: () => void;
  updateUser: (patch: Partial<User>) => void;
  /** Wrapper around fetch that auto-injects the Authorization header and
   *  triggers logout + redirect on 401. */
  authFetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
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
  // Start in loading state when there is a token to validate
  const [loading, setLoading] = useState<boolean>(() => !!localStorage.getItem('forgeguard_token'));

  const login = (newToken: string, newUser: User) => {
    setToken(newToken);
    setUser(newUser);
    localStorage.setItem('forgeguard_token', newToken);
    localStorage.setItem('forgeguard_user', JSON.stringify(newUser));
  };

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    localStorage.removeItem('forgeguard_token');
    localStorage.removeItem('forgeguard_user');
  }, []);

  const updateUser = (patch: Partial<User>) => {
    setUser((prev) => {
      if (!prev) return prev;
      const updated = { ...prev, ...patch };
      localStorage.setItem('forgeguard_user', JSON.stringify(updated));
      return updated;
    });
  };

  // Wrapper around fetch: injects Authorization header and handles 401
  const authFetch = useCallback(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const headers = new Headers(init?.headers);
    const currentToken = localStorage.getItem('forgeguard_token');
    if (currentToken) {
      headers.set('Authorization', `Bearer ${currentToken}`);
    }
    const res = await fetch(input, { ...init, headers });
    if (res.status === 401) {
      logout();
    }
    return res;
  }, [logout]);

  useEffect(() => {
    if (!token) {
      setLoading(false);
      return;
    }
    // Validate token on mount by calling /auth/me
    fetch(`${API_BASE}/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    }).then(async (res) => {
      if (!res.ok) {
        logout();
        return;
      }
      try {
        const data = await res.json();
        setUser(data);
        localStorage.setItem('forgeguard_user', JSON.stringify(data));
      } catch {
        // Response wasn't JSON — keep existing user data
      }
    }).catch(() => {
      // Network error -- keep token, user can retry
    }).finally(() => {
      setLoading(false);
    });
  }, [token, logout]);

  return (
    <AuthContext.Provider value={{ token, user, loading, login, logout, updateUser, authFetch }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
