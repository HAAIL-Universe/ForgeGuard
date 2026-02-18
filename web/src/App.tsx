import React, { Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './context/AuthContext';
import { ToastProvider } from './context/ToastContext';
import ErrorBoundary from './components/ErrorBoundary';

// Lazy-loaded pages — each chunk loaded on demand
const Login = React.lazy(() => import('./pages/Login'));
const AuthCallback = React.lazy(() => import('./pages/AuthCallback'));
const Dashboard = React.lazy(() => import('./pages/Dashboard'));
const CommitTimeline = React.lazy(() => import('./pages/CommitTimeline'));
const AuditDetailPage = React.lazy(() => import('./pages/AuditDetail'));
const ProjectDetail = React.lazy(() => import('./pages/ProjectDetail'));
const BuildProgress = React.lazy(() => import('./pages/BuildProgress'));
const BuildComplete = React.lazy(() => import('./pages/BuildComplete'));
const Settings = React.lazy(() => import('./pages/Settings'));
const Scout = React.lazy(() => import('./pages/Scout'));

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
