/**
 * Settings -- user settings page with BYOK API key management.
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import AppShell from '../components/AppShell';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

function Settings() {
  const { user, token, updateUser } = useAuth();
  const { addToast } = useToast();
  const navigate = useNavigate();

  const [apiKey, setApiKey] = useState('');
  const [apiKey2, setApiKey2] = useState('');
  const [saving, setSaving] = useState(false);
  const [saving2, setSaving2] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [removing2, setRemoving2] = useState(false);
  const [togglingAudit, setTogglingAudit] = useState(false);

  const hasKey = user?.has_anthropic_key ?? false;
  const hasKey2 = user?.has_anthropic_key_2 ?? false;
  const auditEnabled = user?.audit_llm_enabled ?? true;

  const handleSaveKey = async () => {
    const trimmed = apiKey.trim();
    if (!trimmed) return;
    setSaving(true);
    try {
      const res = await fetch(`${API_BASE}/auth/api-key`, {
        method: 'PUT',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ api_key: trimmed }),
      });
      if (res.ok) {
        addToast('API key saved', 'success');
        setApiKey('');
        updateUser({ has_anthropic_key: true });
      } else {
        const data = await res.json().catch(() => ({ detail: 'Failed to save key' }));
        addToast(data.detail || 'Failed to save key');
      }
    } catch {
      addToast('Network error saving key');
    } finally {
      setSaving(false);
    }
  };

  const handleRemoveKey = async () => {
    setRemoving(true);
    try {
      const res = await fetch(`${API_BASE}/auth/api-key`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        addToast('API key removed', 'info');
        updateUser({ has_anthropic_key: false });
      } else {
        addToast('Failed to remove key');
      }
    } catch {
      addToast('Network error removing key');
    } finally {
      setRemoving(false);
    }
  };

  const handleSaveKey2 = async () => {
    const trimmed = apiKey2.trim();
    if (!trimmed) return;
    setSaving2(true);
    try {
      const res = await fetch(`${API_BASE}/auth/api-key-2`, {
        method: 'PUT',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ api_key: trimmed }),
      });
      if (res.ok) {
        addToast('Second API key saved', 'success');
        setApiKey2('');
        updateUser({ has_anthropic_key_2: true });
      } else {
        const data = await res.json().catch(() => ({ detail: 'Failed to save key' }));
        addToast(data.detail || 'Failed to save key');
      }
    } catch {
      addToast('Network error saving key');
    } finally {
      setSaving2(false);
    }
  };

  const handleRemoveKey2 = async () => {
    setRemoving2(true);
    try {
      const res = await fetch(`${API_BASE}/auth/api-key-2`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        addToast('Second API key removed', 'info');
        updateUser({ has_anthropic_key_2: false });
      } else {
        addToast('Failed to remove key');
      }
    } catch {
      addToast('Network error removing key');
    } finally {
      setRemoving2(false);
    }
  };

  return (
    <AppShell>
      <div style={{ padding: '24px', maxWidth: '720px', margin: '0 auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px' }}>
          <button
            onClick={() => navigate('/')}
            style={{
              background: 'transparent',
              color: '#94A3B8',
              border: '1px solid #334155',
              borderRadius: '6px',
              padding: '6px 12px',
              cursor: 'pointer',
              fontSize: '0.8rem',
            }}
          >
            Back
          </button>
          <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Settings</h2>
        </div>

        {/* Profile Section */}
        <div
          style={{
            background: '#1E293B',
            borderRadius: '8px',
            padding: '20px',
            marginBottom: '16px',
          }}
        >
          <h3 style={{ margin: '0 0 16px', fontSize: '0.9rem', color: '#F8FAFC' }}>Profile</h3>
          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
            {user?.avatar_url && (
              <img
                src={user.avatar_url}
                alt={user.github_login}
                style={{ width: 48, height: 48, borderRadius: '50%' }}
              />
            )}
            <div>
              <div style={{ fontWeight: 600, fontSize: '0.95rem' }}>{user?.github_login}</div>
              <div style={{ color: '#64748B', fontSize: '0.8rem', marginTop: '2px' }}>
                Authenticated via GitHub
              </div>
            </div>
          </div>
        </div>

        {/* BYOK API Keys Section */}
        <div
          style={{
            background: '#1E293B',
            borderRadius: '8px',
            padding: '20px',
            marginBottom: '16px',
          }}
          data-testid="byok-section"
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '4px' }}>
            <h3 style={{ margin: 0, fontSize: '0.9rem', color: '#F8FAFC' }}>
              Anthropic API Keys
            </h3>
            {hasKey && (
              <span style={{
                fontSize: '0.65rem',
                color: hasKey2 ? '#22C55E' : '#64748B',
                padding: '2px 8px',
                background: hasKey2 ? '#052e16' : '#0F172A',
                borderRadius: '999px',
                border: `1px solid ${hasKey2 ? '#166534' : '#334155'}`,
              }}>
                {hasKey2 ? '2× throughput' : '1× throughput'}
              </span>
            )}
          </div>
          <p style={{ margin: '0 0 14px', fontSize: '0.75rem', color: '#64748B', lineHeight: 1.5 }}>
            Builds use Claude Opus on your own API keys. Add a second key from a separate
            Anthropic org to double build throughput.
          </p>

          {/* Primary Key */}
          <div style={{ marginBottom: '12px' }}>
            <div style={{ fontSize: '0.7rem', color: '#94A3B8', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Primary Key {hasKey && <span style={{ color: '#22C55E' }}>●</span>}
            </div>
            {hasKey ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <div style={{
                  flex: 1,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  padding: '8px 12px',
                  background: '#0F172A',
                  borderRadius: '6px',
                  fontSize: '0.8rem',
                }}>
                  <span style={{ color: '#94A3B8' }}>Configured</span>
                  <span style={{ color: '#64748B', fontFamily: 'monospace' }}>sk-ant-•••••••</span>
                </div>
                <button
                  onClick={handleRemoveKey}
                  disabled={removing}
                  data-testid="remove-api-key-btn"
                  style={{
                    background: 'transparent',
                    color: '#EF4444',
                    border: '1px solid #7F1D1D',
                    borderRadius: '6px',
                    padding: '6px 14px',
                    cursor: removing ? 'not-allowed' : 'pointer',
                    fontSize: '0.75rem',
                    opacity: removing ? 0.6 : 1,
                  }}
                >
                  {removing ? '...' : 'Remove'}
                </button>
              </div>
            ) : (
              <div>
                <div style={{ display: 'flex', gap: '8px' }}>
                  <input
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="sk-ant-api03-..."
                    data-testid="api-key-input"
                    style={{
                      flex: 1,
                      background: '#0F172A',
                      border: '1px solid #334155',
                      borderRadius: '6px',
                      padding: '8px 12px',
                      color: '#F8FAFC',
                      fontSize: '0.8rem',
                      fontFamily: 'monospace',
                      outline: 'none',
                    }}
                    onKeyDown={(e) => { if (e.key === 'Enter') handleSaveKey(); }}
                  />
                  <button
                    onClick={handleSaveKey}
                    disabled={saving || !apiKey.trim()}
                    data-testid="save-api-key-btn"
                    style={{
                      background: saving ? '#1E293B' : '#2563EB',
                      color: '#fff',
                      border: 'none',
                      borderRadius: '6px',
                      padding: '8px 18px',
                      cursor: saving || !apiKey.trim() ? 'not-allowed' : 'pointer',
                      fontSize: '0.8rem',
                      opacity: saving || !apiKey.trim() ? 0.6 : 1,
                    }}
                  >
                    {saving ? '...' : 'Save'}
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Secondary Key */}
          <div>
            <div style={{ fontSize: '0.7rem', color: '#94A3B8', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Secondary Key {hasKey2 && <span style={{ color: '#22C55E' }}>●</span>}
              <span style={{ color: '#64748B', fontStyle: 'italic', textTransform: 'none', letterSpacing: 'normal', marginLeft: '6px' }}>optional — different org for 2× speed</span>
            </div>
            {hasKey2 ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <div style={{
                  flex: 1,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  padding: '8px 12px',
                  background: '#0F172A',
                  borderRadius: '6px',
                  fontSize: '0.8rem',
                }}>
                  <span style={{ color: '#94A3B8' }}>Configured</span>
                  <span style={{ color: '#64748B', fontFamily: 'monospace' }}>sk-ant-•••••••</span>
                </div>
                <button
                  onClick={handleRemoveKey2}
                  disabled={removing2}
                  data-testid="remove-api-key-2-btn"
                  style={{
                    background: 'transparent',
                    color: '#EF4444',
                    border: '1px solid #7F1D1D',
                    borderRadius: '6px',
                    padding: '6px 14px',
                    cursor: removing2 ? 'not-allowed' : 'pointer',
                    fontSize: '0.75rem',
                    opacity: removing2 ? 0.6 : 1,
                  }}
                >
                  {removing2 ? '...' : 'Remove'}
                </button>
              </div>
            ) : (
              <div style={{ display: 'flex', gap: '8px' }}>
                <input
                  type="password"
                  value={apiKey2}
                  onChange={(e) => setApiKey2(e.target.value)}
                  placeholder="sk-ant-api03-... (from second org)"
                  data-testid="api-key-2-input"
                  style={{
                    flex: 1,
                    background: '#0F172A',
                    border: '1px solid #334155',
                    borderRadius: '6px',
                    padding: '8px 12px',
                    color: '#F8FAFC',
                    fontSize: '0.8rem',
                    fontFamily: 'monospace',
                    outline: 'none',
                  }}
                  onKeyDown={(e) => { if (e.key === 'Enter') handleSaveKey2(); }}
                />
                <button
                  onClick={handleSaveKey2}
                  disabled={saving2 || !apiKey2.trim()}
                  data-testid="save-api-key-2-btn"
                  style={{
                    background: saving2 ? '#1E293B' : '#2563EB',
                    color: '#fff',
                    border: 'none',
                    borderRadius: '6px',
                    padding: '8px 18px',
                    cursor: saving2 || !apiKey2.trim() ? 'not-allowed' : 'pointer',
                    fontSize: '0.8rem',
                    opacity: saving2 || !apiKey2.trim() ? 0.6 : 1,
                  }}
                >
                  {saving2 ? '...' : 'Save'}
                </button>
              </div>
            )}
          </div>

          {!hasKey && (
            <p style={{ margin: '10px 0 0', fontSize: '0.7rem', color: '#64748B' }}>
              Get your API key at{' '}
              <a
                href="https://console.anthropic.com/settings/keys"
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: '#60A5FA' }}
              >
                console.anthropic.com
              </a>
            </p>
          )}
        </div>

        {/* Audit LLM Toggle */}
        <div
          style={{
            background: '#1E293B',
            borderRadius: '8px',
            padding: '20px',
            marginBottom: '16px',
          }}
          data-testid="audit-toggle-section"
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div>
              <h3 style={{ margin: '0 0 4px', fontSize: '0.9rem', color: '#F8FAFC' }}>
                LLM Auditor
              </h3>
              <p style={{ margin: 0, fontSize: '0.75rem', color: '#64748B', lineHeight: 1.5, maxWidth: 420 }}>
                After each build phase a separate LLM reviews the output for contract
                compliance, architectural drift, and semantic errors. Powered by Sonnet.
              </p>
            </div>
            <button
              onClick={async () => {
                setTogglingAudit(true);
                try {
                  const res = await fetch(`${API_BASE}/auth/audit-toggle`, {
                    method: 'PUT',
                    headers: {
                      Authorization: `Bearer ${token}`,
                      'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ enabled: !auditEnabled }),
                  });
                  if (res.ok) {
                    updateUser({ audit_llm_enabled: !auditEnabled });
                    addToast(
                      `LLM auditor ${!auditEnabled ? 'enabled' : 'disabled'}`,
                      !auditEnabled ? 'success' : 'info',
                    );
                  } else {
                    addToast('Failed to update audit setting');
                  }
                } catch {
                  addToast('Network error');
                } finally {
                  setTogglingAudit(false);
                }
              }}
              disabled={togglingAudit}
              data-testid="audit-toggle-btn"
              style={{
                position: 'relative',
                width: 44,
                height: 24,
                borderRadius: 12,
                border: 'none',
                background: auditEnabled ? '#22C55E' : '#334155',
                cursor: togglingAudit ? 'not-allowed' : 'pointer',
                transition: 'background 0.2s',
                flexShrink: 0,
                marginTop: 2,
                opacity: togglingAudit ? 0.6 : 1,
              }}
              aria-label={`LLM auditor ${auditEnabled ? 'enabled' : 'disabled'}`}
            >
              <div
                style={{
                  position: 'absolute',
                  top: 3,
                  left: auditEnabled ? 23 : 3,
                  width: 18,
                  height: 18,
                  borderRadius: '50%',
                  background: '#F8FAFC',
                  transition: 'left 0.2s',
                }}
              />
            </button>
          </div>
        </div>

        {/* AI Models info */}
        <div
          style={{
            background: '#1E293B',
            borderRadius: '8px',
            padding: '20px',
            marginBottom: '16px',
          }}
        >
          <h3 style={{ margin: '0 0 12px', fontSize: '0.9rem', color: '#F8FAFC' }}>AI Models</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', fontSize: '0.8rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: '#0F172A', borderRadius: '6px' }}>
              <span style={{ color: '#94A3B8' }}>Questionnaire</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ color: '#3B82F6', fontWeight: 600 }}>claude-sonnet-4-5</span>
                <span style={{ color: '#64748B', fontSize: '0.65rem' }}>BYOK</span>
              </div>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: '#0F172A', borderRadius: '6px' }}>
              <span style={{ color: '#94A3B8' }}>Builder</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ color: '#A78BFA', fontWeight: 600 }}>claude-opus-4-6</span>
                <span style={{ color: '#64748B', fontSize: '0.65rem' }}>BYOK</span>
              </div>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: '#0F172A', borderRadius: '6px' }}>
              <span style={{ color: '#94A3B8' }}>Planner</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ color: '#3B82F6', fontWeight: 600 }}>claude-sonnet-4-5</span>
                <span style={{ color: '#64748B', fontSize: '0.65rem' }}>BYOK</span>
              </div>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: '#0F172A', borderRadius: '6px' }}>
              <span style={{ color: '#94A3B8' }}>Auditor</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ color: '#3B82F6', fontWeight: 600 }}>claude-sonnet-4-5</span>
                <span style={{ color: '#64748B', fontSize: '0.65rem' }}>BYOK</span>
              </div>
            </div>
          </div>
        </div>

        {/* About Section */}
        <div
          style={{
            background: '#1E293B',
            borderRadius: '8px',
            padding: '20px',
          }}
        >
          <h3 style={{ margin: '0 0 12px', fontSize: '0.9rem', color: '#F8FAFC' }}>About</h3>
          <div style={{ fontSize: '0.8rem', color: '#94A3B8', display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>Version</span>
              <span style={{ color: '#F8FAFC' }}>v0.1.0</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>Framework</span>
              <span style={{ color: '#F8FAFC' }}>Forge Governance</span>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

export default Settings;
