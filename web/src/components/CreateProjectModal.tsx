import { useState } from 'react';
import { useAuth } from '../context/AuthContext';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

interface CreateProjectModalProps {
  onClose: () => void;
  onCreated: (project: { id: string; name: string }) => void;
}

function CreateProjectModal({ onClose, onCreated }: CreateProjectModalProps) {
  const { token } = useAuth();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async () => {
    const trimmed = name.trim();
    if (!trimmed) {
      setError('Project name is required');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${API_BASE}/projects`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name: trimmed, description: description.trim() || undefined }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || 'Failed to create project');
        setLoading(false);
        return;
      }
      const project = await res.json();
      onCreated(project);
    } catch {
      setError('Network error');
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0,0,0,0.6)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 100,
      }}
      onClick={onClose}
      data-testid="create-project-overlay"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: '#1E293B',
          borderRadius: '8px',
          padding: '24px',
          maxWidth: '440px',
          width: '90%',
        }}
        data-testid="create-project-modal"
      >
        <h3 style={{ margin: '0 0 16px 0', fontSize: '1rem', color: '#F8FAFC' }}>
          Create Project
        </h3>

        <label style={{ display: 'block', marginBottom: '12px' }}>
          <span style={{ display: 'block', color: '#94A3B8', fontSize: '0.8rem', marginBottom: '4px' }}>
            Name *
          </span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="My Awesome App"
            maxLength={255}
            autoFocus
            data-testid="project-name-input"
            style={{
              width: '100%',
              padding: '8px 12px',
              background: '#0F172A',
              border: '1px solid #334155',
              borderRadius: '6px',
              color: '#F8FAFC',
              fontSize: '0.875rem',
              boxSizing: 'border-box',
            }}
          />
        </label>

        <label style={{ display: 'block', marginBottom: '16px' }}>
          <span style={{ display: 'block', color: '#94A3B8', fontSize: '0.8rem', marginBottom: '4px' }}>
            Description (optional)
          </span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Brief project description..."
            rows={3}
            data-testid="project-desc-input"
            style={{
              width: '100%',
              padding: '8px 12px',
              background: '#0F172A',
              border: '1px solid #334155',
              borderRadius: '6px',
              color: '#F8FAFC',
              fontSize: '0.875rem',
              resize: 'vertical',
              boxSizing: 'border-box',
              fontFamily: 'inherit',
            }}
          />
        </label>

        {error && (
          <p style={{ color: '#EF4444', fontSize: '0.8rem', margin: '0 0 12px 0' }} data-testid="create-error">
            {error}
          </p>
        )}

        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              color: '#94A3B8',
              border: '1px solid #334155',
              borderRadius: '6px',
              padding: '8px 16px',
              cursor: 'pointer',
              fontSize: '0.875rem',
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading}
            data-testid="create-project-submit"
            style={{
              background: '#2563EB',
              color: '#fff',
              border: 'none',
              borderRadius: '6px',
              padding: '8px 16px',
              cursor: loading ? 'not-allowed' : 'pointer',
              fontSize: '0.875rem',
              opacity: loading ? 0.6 : 1,
            }}
          >
            {loading ? 'Creating...' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default CreateProjectModal;
