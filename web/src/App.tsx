import React, { Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './context/AuthContext';
import { ToastProvider } from './context/ToastContext';
import ErrorBoundary from './components/ErrorBoundary';

/**
 * Retry a dynamic import up to `retries` times, then force-reload the page.
 * Handles stale module URLs after Vite HMR rebuilds or cache clears.
 */
function lazyWithRetry(
  factory: () => Promise<{ default: React.ComponentType<unknown> }>,
  retries = 2,
): React.LazyExoticComponent<React.ComponentType<unknown>> {
  return React.lazy(() => {
    const attempt = (remaining: number): Promise<{ default: React.ComponentType<unknown> }> =>
      factory().catch((err: unknown) => {
        if (remaining <= 0) {
          // Last resort: full page reload once (sessionStorage guard prevents loop)
          const reloadKey = 'chunk_reload';
          if (!sessionStorage.getItem(reloadKey)) {
            sessionStorage.setItem(reloadKey, '1');
            window.location.reload();
          }
          throw err;
        }
        return new Promise<{ default: React.ComponentType<unknown> }>((resolve) =>
          setTimeout(() => resolve(attempt(remaining - 1)), 500),
        );
      });
    // Clear the reload guard on successful load
    return attempt(retries).then((mod) => {
      sessionStorage.removeItem('chunk_reload');
      return mod;
    });
  });
}

// Lazy-loaded pages — each chunk loaded on demand (with retry)
const Login = lazyWithRetry(() => import('./pages/Login'));
const AuthCallback = lazyWithRetry(() => import('./pages/AuthCallback'));
const Dashboard = lazyWithRetry(() => import('./pages/Dashboard'));
const CommitTimeline = lazyWithRetry(() => import('./pages/CommitTimeline'));
const AuditDetailPage = lazyWithRetry(() => import('./pages/AuditDetail'));
const ProjectDetail = lazyWithRetry(() => import('./pages/ProjectDetail'));
const BuildProgress = lazyWithRetry(() => import('./pages/BuildProgress'));
const BuildIDE = lazyWithRetry(() => import('./pages/BuildIDE'));
const BuildComplete = lazyWithRetry(() => import('./pages/BuildComplete'));
const Settings = lazyWithRetry(() => import('./pages/Settings'));
const Scout = lazyWithRetry(() => import('./pages/Scout'));

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { token, loading } = useAuth();
  if (loading) {
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
        <div
          style={{
            width: '40px',
            height: '40px',
            border: '3px solid #1E293B',
            borderTop: '3px solid #2563EB',
            borderRadius: '50%',
            animation: 'spin 0.8s linear infinite',
          }}
        />
      </div>
    );
  }
  if (!token) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

/** Full-page loading spinner for Suspense fallback. */
function LoadingSpinner() {
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
      <div
        style={{
          width: '40px',
          height: '40px',
          border: '3px solid #1E293B',
          borderTop: '3px solid #2563EB',
          borderRadius: '50%',
          animation: 'spin 0.8s linear infinite',
        }}
      />
    </div>
  );
}

function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <ErrorBoundary>
          <BrowserRouter>
            <Suspense fallback={<LoadingSpinner />}>
              <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/auth/callback" element={<AuthCallback />} />
            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <Dashboard />
                </ProtectedRoute>
              }
            />
            <Route
              path="/repos/:repoId"
              element={
                <ProtectedRoute>
                  <CommitTimeline />
                </ProtectedRoute>
              }
            />
            <Route
              path="/repos/:repoId/audits/:auditId"
              element={
                <ProtectedRoute>
                  <AuditDetailPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/projects/:projectId"
              element={
                <ProtectedRoute>
                  <ProjectDetail />
                </ProtectedRoute>
              }
            />
            <Route
              path="/projects/:projectId/build"
              element={
                <ProtectedRoute>
                  <BuildIDE />
                </ProtectedRoute>
              }
            />
            <Route
              path="/projects/:projectId/build/legacy"
              element={
                <ProtectedRoute>
                  <BuildProgress />
                </ProtectedRoute>
              }
            />
            <Route
              path="/projects/:projectId/build/complete"
              element={
                <ProtectedRoute>
                  <BuildComplete />
                </ProtectedRoute>
              }
            />
            <Route
              path="/settings"
              element={
                <ProtectedRoute>
                  <Settings />
                </ProtectedRoute>
              }
            />
            <Route
              path="/scout"
              element={
                <ProtectedRoute>
                  <Scout />
                </ProtectedRoute>
              }
            />
            <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Suspense>
          </BrowserRouter>
        </ErrorBoundary>
      </ToastProvider>
    </AuthProvider>
  );
}

export default App;
