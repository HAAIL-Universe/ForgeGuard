/**
 * CertificateModal -- Forge Seal build certificate viewer.
 * Shows verdict badge, dimension score gauges, build summary, and download options.
 */
import { useState, useEffect } from 'react';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

interface Dimension {
  score: number;
  weight: number;
  details: Record<string, unknown>;
}

interface CertificateData {
  forge_seal: {
    version: string;
    generated_at: string;
    integrity_hash: string;
  };
  certificate: {
    project: { id: string; name: string; description: string | null };
    verdict: string;
    overall_score: number;
    dimensions: Record<string, Dimension>;
    build_summary: Record<string, unknown> | null;
    builds_total: number;
    contracts_count: number;
    generated_at: string;
  };
}

interface CertificateModalProps {
  projectId: string;
  token: string;
  onClose: () => void;
}

const VERDICT_STYLES: Record<string, { bg: string; border: string; color: string; icon: string }> = {
  CERTIFIED:   { bg: '#052E16', border: '#22C55E', color: '#4ADE80', icon: '🛡️' },
  CONDITIONAL: { bg: '#1C1917', border: '#F59E0B', color: '#FBBF24', icon: '⚠️' },
  FLAGGED:     { bg: '#1C0A0A', border: '#EF4444', color: '#F87171', icon: '🚩' },
};

function ScoreGauge({ label, score, weight }: { label: string; score: number; weight: number }) {
  const pct = Math.round(score);
  const color = pct >= 90 ? '#22C55E' : pct >= 70 ? '#F59E0B' : '#EF4444';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span style={{ fontSize: '0.75rem', color: '#CBD5E1', textTransform: 'capitalize' }}>
          {label.replace(/_/g, ' ')}
        </span>
        <span style={{ fontSize: '0.7rem', color: '#64748B' }}>{weight}%</span>
      </div>
      <div style={{ height: '8px', background: '#0F172A', borderRadius: '4px', overflow: 'hidden' }}>
        <div
          style={{
            width: `${pct}%`,
            height: '100%',
            background: color,
            borderRadius: '4px',
            transition: 'width 0.6s ease',
          }}
        />
      </div>
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <span style={{ fontSize: '0.72rem', fontWeight: 700, color }}>{pct}</span>
      </div>
    </div>
  );
}

export default function CertificateModal({ projectId, token, onClose }: CertificateModalProps) {
  const [data, setData] = useState<CertificateData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/projects/${projectId}/certificate`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: 'Failed to load certificate' }));
          if (!cancelled) setError(err.detail || 'Failed to load certificate');
          return;
        }
        const json = await res.json();
        if (!cancelled) setData(json);
      } catch {
        if (!cancelled) setError('Network error loading certificate');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [projectId, token]);

  const handleDownloadJSON = async () => {
    const res = await fetch(`${API_BASE}/projects/${projectId}/certificate`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `forge-seal-${projectId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleDownloadHTML = async () => {
    const res = await fetch(`${API_BASE}/projects/${projectId}/certificate/html`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `forge-seal-${projectId}.html`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleDownloadText = async () => {
    const res = await fetch(`${API_BASE}/projects/${projectId}/certificate/text`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return;
    const json = await res.json();
    const blob = new Blob([json.certificate], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `forge-seal-${projectId}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const cert = data?.certificate;
  const seal = data?.forge_seal;
  const verdict = cert?.verdict ?? 'FLAGGED';
  const vs = VERDICT_STYLES[verdict] || VERDICT_STYLES.FLAGGED;

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0,0,0,0.7)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 100,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: '#1E293B',
          borderRadius: '12px',
          maxWidth: '600px',
          width: '95%',
          maxHeight: '85vh',
          overflow: 'auto',
          border: `1px solid ${loading || error ? '#334155' : vs.border}`,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          style={{
            padding: '16px 20px',
            borderBottom: '1px solid #334155',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <h3 style={{ margin: 0, fontSize: '1rem', display: 'flex', alignItems: 'center', gap: '8px' }}>
            🛡️ Forge Seal Certificate
          </h3>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              color: '#94A3B8',
              fontSize: '1.2rem',
              cursor: 'pointer',
            }}
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: '20px' }}>
          {loading && (
            <div style={{ textAlign: 'center', padding: '40px 0', color: '#64748B' }}>
              <div
                style={{
                  width: '32px',
                  height: '32px',
                  border: '3px solid #334155',
                  borderTopColor: '#3B82F6',
                  borderRadius: '50%',
                  animation: 'spin 0.8s linear infinite',
                  margin: '0 auto 12px',
                }}
              />
              Generating certificate…
            </div>
          )}

          {error && (
            <div style={{ textAlign: 'center', padding: '40px 0', color: '#F87171' }}>
              {error}
            </div>
          )}

          {cert && seal && (
            <>
              {/* Verdict Badge */}
              <div
                style={{
                  background: vs.bg,
                  border: `2px solid ${vs.border}`,
                  borderRadius: '10px',
                  padding: '16px 20px',
                  textAlign: 'center',
                  marginBottom: '20px',
                }}
              >
                <div style={{ fontSize: '2rem', marginBottom: '4px' }}>{vs.icon}</div>
                <div style={{ fontSize: '1.4rem', fontWeight: 800, color: vs.color, letterSpacing: '0.05em' }}>
                  {verdict}
                </div>
                <div style={{ fontSize: '2rem', fontWeight: 800, color: vs.color, marginTop: '4px' }}>
                  {Math.round(cert.overall_score)}
                </div>
                <div style={{ fontSize: '0.7rem', color: '#64748B', marginTop: '4px' }}>
                  Overall Score
                </div>
              </div>

              {/* Dimension Scores */}
              <div style={{ marginBottom: '20px' }}>
                <h4 style={{ margin: '0 0 12px', fontSize: '0.85rem', color: '#94A3B8' }}>
                  Dimensions
                </h4>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  {Object.entries(cert.dimensions).map(([key, dim]) => (
                    <ScoreGauge key={key} label={key} score={dim.score} weight={dim.weight * 100} />
                  ))}
                </div>
              </div>

              {/* Build Summary */}
              {cert.build_summary && (
                <div
                  style={{
                    background: '#0F172A',
                    borderRadius: '8px',
                    padding: '12px 16px',
                    marginBottom: '20px',
                    fontSize: '0.78rem',
                  }}
                >
                  <h4 style={{ margin: '0 0 8px', fontSize: '0.85rem', color: '#94A3B8' }}>Build Summary</h4>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
                    {Object.entries(cert.build_summary).map(([key, val]) => (
                      <div key={key} style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span style={{ color: '#64748B', textTransform: 'capitalize' }}>
                          {key.replace(/_/g, ' ')}
                        </span>
                        <span style={{ color: '#CBD5E1' }}>{String(val)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Stats Row */}
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 1fr 1fr',
                  gap: '8px',
                  marginBottom: '20px',
                }}
              >
                <div style={{ textAlign: 'center', background: '#0F172A', borderRadius: '6px', padding: '10px' }}>
                  <div style={{ fontSize: '1.1rem', fontWeight: 700, color: '#F8FAFC' }}>{cert.builds_total}</div>
                  <div style={{ fontSize: '0.68rem', color: '#64748B' }}>Total Builds</div>
                </div>
                <div style={{ textAlign: 'center', background: '#0F172A', borderRadius: '6px', padding: '10px' }}>
                  <div style={{ fontSize: '1.1rem', fontWeight: 700, color: '#F8FAFC' }}>{cert.contracts_count}</div>
                  <div style={{ fontSize: '0.68rem', color: '#64748B' }}>Contracts</div>
                </div>
                <div style={{ textAlign: 'center', background: '#0F172A', borderRadius: '6px', padding: '10px' }}>
                  <div style={{ fontSize: '1.1rem', fontWeight: 700, color: '#F8FAFC' }}>
                    {new Date(cert.generated_at).toLocaleDateString()}
                  </div>
                  <div style={{ fontSize: '0.68rem', color: '#64748B' }}>Generated</div>
                </div>
              </div>

              {/* Integrity Hash */}
              <div
                style={{
                  background: '#0F172A',
                  borderRadius: '6px',
                  padding: '10px 14px',
                  marginBottom: '20px',
                  fontSize: '0.7rem',
                  color: '#64748B',
                  wordBreak: 'break-all',
                }}
              >
                <span style={{ color: '#94A3B8', fontWeight: 600 }}>Integrity: </span>
                {seal.integrity_hash}
              </div>

              {/* Download Buttons */}
              <div style={{ display: 'flex', gap: '8px', justifyContent: 'center' }}>
                <button
                  onClick={handleDownloadJSON}
                  style={{
                    background: '#1E3A5F',
                    border: '1px solid #3B82F6',
                    borderRadius: '6px',
                    color: '#93C5FD',
                    padding: '8px 16px',
                    cursor: 'pointer',
                    fontSize: '0.78rem',
                    fontWeight: 600,
                  }}
                >
                  ⬇ JSON
                </button>
                <button
                  onClick={handleDownloadHTML}
                  style={{
                    background: '#1E3A5F',
                    border: '1px solid #3B82F6',
                    borderRadius: '6px',
                    color: '#93C5FD',
                    padding: '8px 16px',
                    cursor: 'pointer',
                    fontSize: '0.78rem',
                    fontWeight: 600,
                  }}
                >
                  ⬇ HTML
                </button>
                <button
                  onClick={handleDownloadText}
                  style={{
                    background: '#1E3A5F',
                    border: '1px solid #3B82F6',
                    borderRadius: '6px',
                    color: '#93C5FD',
                    padding: '8px 16px',
                    cursor: 'pointer',
                    fontSize: '0.78rem',
                    fontWeight: 600,
                  }}
                >
                  ⬇ Text
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
