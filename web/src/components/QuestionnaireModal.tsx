/**
 * QuestionnaireModal -- voice-first chat modal for the Forge intake questionnaire.
 *
 * Walks the user through 8 sections to collect all information needed
 * to generate Forge contract files. Supports both voice (MediaRecorder +
 * server-side Whisper transcription) and text input.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { useVoiceRecorder } from '../hooks/useVoiceRecorder';
import ContractProgress from './ContractProgress';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

/* ------------------------------------------------------------------ */
/*  Section labels for progress display                               */
/* ------------------------------------------------------------------ */

const SECTION_LABELS: Record<string, string> = {
  product_intent: 'Product Intent',
  tech_stack: 'Tech Stack',
  database_schema: 'Database Schema',
  api_endpoints: 'API Endpoints',
  ui_requirements: 'UI Requirements',
  architectural_boundaries: 'Boundaries',
  deployment_target: 'Deployment',
};

const MINI_SECTION_LABELS: Record<string, string> = {
  product_intent: 'Product Intent',
  ui_requirements: 'UI & Flow',
};

const ALL_SECTIONS = Object.keys(SECTION_LABELS);
const MINI_SECTIONS = Object.keys(MINI_SECTION_LABELS);

/* ------------------------------------------------------------------ */
/*  Types                                                             */
/* ------------------------------------------------------------------ */

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  section?: string;
}

interface QuestionnaireState {
  current_section: string | null;
  completed_sections: string[];
  remaining_sections: string[];
  is_complete: boolean;
}

interface Props {
  projectId: string;
  projectName: string;
  buildMode?: 'mini' | 'full';
  onClose: () => void;
  onContractsGenerated: () => void;
  onDismissDuringGeneration?: () => void;
  /** When true, open directly into contract-generation view */
  initialGenerating?: boolean;
  /** Contracts already completed during background generation */
  initialDoneContracts?: string[];
}

/* ------------------------------------------------------------------ */
/*  Styles                                                            */
/* ------------------------------------------------------------------ */

const overlayStyle: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  backgroundColor: 'rgba(0,0,0,0.65)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 100,
};

const modalStyle: React.CSSProperties = {
  background: '#1E293B',
  borderRadius: '10px',
  display: 'flex',
  flexDirection: 'column',
  maxWidth: '640px',
  width: '95%',
  height: '80vh',
  maxHeight: '720px',
  overflow: 'hidden',
};

const headerStyle: React.CSSProperties = {
  padding: '16px 20px',
  borderBottom: '1px solid #334155',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  flexShrink: 0,
};

const messagesStyle: React.CSSProperties = {
  flex: 1,
  overflowY: 'auto',
  padding: '16px 20px',
  display: 'flex',
  flexDirection: 'column',
  gap: '12px',
};

const inputBarStyle: React.CSSProperties = {
  display: 'flex',
  gap: '8px',
  padding: '12px 20px',
  borderTop: '1px solid #334155',
  flexShrink: 0,
  alignItems: 'flex-end',
};

const textareaStyle: React.CSSProperties = {
  flex: 1,
  padding: '10px 14px',
  background: '#0F172A',
  border: '1px solid #334155',
  borderRadius: '8px',
  color: '#F8FAFC',
  fontSize: '0.875rem',
  resize: 'none',
  fontFamily: 'inherit',
  lineHeight: '1.4',
  minHeight: '44px',
  maxHeight: '120px',
};

const btnPrimary: React.CSSProperties = {
  background: '#2563EB',
  color: '#fff',
  border: 'none',
  borderRadius: '8px',
  padding: '10px 16px',
  cursor: 'pointer',
  fontSize: '0.8rem',
  fontWeight: 600,
  whiteSpace: 'nowrap',
};

const btnGhost: React.CSSProperties = {
  background: 'transparent',
  color: '#94A3B8',
  border: '1px solid #334155',
  borderRadius: '8px',
  padding: '10px 14px',
  cursor: 'pointer',
  fontSize: '0.8rem',
};

/* ------------------------------------------------------------------ */
/*  Progress bar                                                      */
/* ------------------------------------------------------------------ */

function ProgressBar({ completed, current, skipped, sections, labels }: { completed: string[]; current: string | null; skipped: string[]; sections: string[]; labels: Record<string, string> }) {
  return (
    <div style={{ display: 'flex', gap: '3px', padding: '0 20px 10px' }} data-testid="questionnaire-progress">
      {sections.map((s) => {
        const isSkipped = skipped.includes(s);
        const done = completed.includes(s);
        const active = s === current;
        return (
          <div
            key={s}
            title={`${labels[s] ?? s}${isSkipped ? ' (auto)' : ''}`}
            style={{
              flex: 1,
              height: '4px',
              borderRadius: '2px',
              background: isSkipped ? '#334155' : done ? '#22C55E' : active ? '#2563EB' : '#334155',
              opacity: isSkipped ? 0.25 : 1,
              transition: 'background 0.3s, opacity 0.3s',
            }}
          />
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Message formatting helpers                                        */
/* ------------------------------------------------------------------ */

/**
 * Insert paragraph breaks into LLM text that arrives as a single block.
 * Looks for sentence-ending punctuation followed by transition phrases
 * that signal a new topic (e.g. "Now let's", "Moving on", "Next,").
 * Also breaks before the final question sentence(s).
 */
function addParagraphBreaks(text: string): string {
  // If it already has paragraph breaks, leave it alone
  if (/\n\n/.test(text)) return text;

  // Transition phrases that signal a new section mid-text
  const transitions = [
    /([.!])\s+(Now let['']?s\b)/gi,
    /([.!])\s+(Let['']?s (?:talk|move|discuss|define|look|think|design|set up)\b)/gi,
    /([.!])\s+(Moving (?:on|forward)\b)/gi,
    /([.!])\s+(Next[,:]?\s)/gi,
    /([.!])\s+(For (?:the|your|this)\b)/gi,
    /([.!])\s+(What (?:REST|API|UI|deployment|specific)\b)/gi,
  ];

  let result = text;
  for (const re of transitions) {
    result = result.replace(re, '$1\n\n$2');
  }

  // Break before the last question if there's a sentence boundary before it
  // Find the last ". <sentence with ?>" pattern
  const lastQ = result.lastIndexOf('?');
  if (lastQ > 0) {
    // Walk backwards from lastQ to find the start of the question sentence
    const before = result.slice(0, lastQ);
    // Find the last ". " that precedes the question — but only if it's
    // not already after a paragraph break
    const sentenceEnd = before.lastIndexOf('. ');
    if (sentenceEnd > 0) {
      const afterDot = result.slice(sentenceEnd + 2);
      const beforeDot = result.slice(0, sentenceEnd + 1);
      // Only break if the question sentence actually contains '?'
      // and we're not already at a paragraph break
      if (afterDot.includes('?') && !beforeDot.endsWith('\n\n')) {
        result = beforeDot + '\n\n' + afterDot;
      }
    }
  }

  return result;
}

/**
 * Split assistant text into paragraphs + a trailing question.
 * Each double-newline-separated block becomes its own visual paragraph.
 * The last paragraph containing '?' is pulled out as the question.
 */
function splitMessage(text: string): { paragraphs: string[]; question: string | null } {
  const expanded = addParagraphBreaks(text);
  const parts = expanded.split(/\n\n+/).map((p) => p.trim()).filter(Boolean);
  if (parts.length === 0) return { paragraphs: [], question: null };
  if (parts.length === 1) {
    return parts[0].includes('?')
      ? { paragraphs: [], question: parts[0] }
      : { paragraphs: [parts[0]], question: null };
  }
  // Walk backwards to find the last paragraph with a '?'
  for (let i = parts.length - 1; i >= 0; i--) {
    if (parts[i].includes('?')) {
      return {
        paragraphs: parts.slice(0, i),
        question: parts.slice(i).join('\n\n'),
      };
    }
  }
  return { paragraphs: parts, question: null };
}

const bodyBubbleStyle: React.CSSProperties = {
  padding: '10px 14px',
  borderRadius: '10px',
  background: '#0F172A',
  color: '#CBD5E1',
  fontSize: '0.82rem',
  lineHeight: '1.5',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
};

const questionBubbleStyle: React.CSSProperties = {
  padding: '12px 14px',
  borderRadius: '10px',
  background: 'rgba(37,99,235,0.4)',
  color: '#CBD5E1',
  fontSize: '0.85rem',
  lineHeight: '1.5',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  fontWeight: 500,
};

/** Render an assistant message with paragraphs separated and question highlighted. */
function AssistantBubble({ content }: { content: string }) {
  const { paragraphs, question } = splitMessage(content);
  return (
    <div
      style={{
        alignSelf: 'flex-start',
        maxWidth: '88%',
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
      }}
    >
      {paragraphs.map((p, i) => (
        <div key={i} style={bodyBubbleStyle}>{p}</div>
      ))}
      {question && (
        <div style={questionBubbleStyle}>{question}</div>
      )}
    </div>
  );
}

/** Compact user "sent" indicator instead of a full chat bubble. */
function UserBubble({ content }: { content: string }) {
  const preview = content.length > 80 ? content.slice(0, 77) + '…' : content;
  return (
    <div
      style={{
        alignSelf: 'flex-end',
        display: 'flex',
        alignItems: 'center',
        gap: '6px',
        padding: '6px 12px',
        borderRadius: '12px',
        background: 'rgba(37,99,235,0.12)',
        maxWidth: '85%',
      }}
      title={content}
    >
      <span
        style={{
          color: '#94A3B8',
          fontSize: '0.78rem',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {preview}
      </span>
      <span
        style={{
          color: '#22C55E',
          fontSize: '0.75rem',
          flexShrink: 0,
          fontWeight: 600,
        }}
      >
        ✓
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Component                                                         */
/* ------------------------------------------------------------------ */

function QuestionnaireModal({ projectId, projectName, buildMode = 'full', onClose, onContractsGenerated, onDismissDuringGeneration, initialGenerating, initialDoneContracts }: Props) {
  const { token } = useAuth();
  const activeSections = buildMode === 'mini' ? MINI_SECTIONS : ALL_SECTIONS;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [qState, setQState] = useState<QuestionnaireState>({
    current_section: 'product_intent',
    completed_sections: [],
    remaining_sections: [...activeSections],
    is_complete: false,
  });
  const [error, setError] = useState('');
  const [resetting, setResetting] = useState(false);
  const [tokenUsage, setTokenUsage] = useState({ input_tokens: 0, output_tokens: 0 });
  const [generatingContracts, setGeneratingContracts] = useState(initialGenerating ?? false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const loadedRef = useRef(false);

  /* auto-scroll on new messages */
  useEffect(() => {
    if (typeof messagesEndRef.current?.scrollIntoView === 'function') {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages]);

  /* voice recording — MediaRecorder + Whisper transcription */
  const handleVoiceResult = useCallback((text: string) => {
    setInput((prev) => (prev ? `${prev} ${text}` : text));
    textareaRef.current?.focus();
  }, []);

  const [voiceError, setVoiceError] = useState<string | null>(null);

  const {
    recording: listening,
    transcribing,
    toggle: toggleMic,
    supported: micSupported,
  } = useVoiceRecorder({
    apiBase: API_BASE,
    token: token ?? '',
    onTranscript: handleVoiceResult,
    onError: (err) => {
      setVoiceError(err);
      setTimeout(() => setVoiceError(null), 4000);
    },
  });

  /* ---- Load existing state on mount ---- */
  useEffect(() => {
    if (loadedRef.current) return;   // StrictMode double-mount guard
    loadedRef.current = true;

    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/projects/${projectId}/questionnaire/state`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const state = await res.json();
          setQState({
            current_section: state.current_section,
            completed_sections: state.completed_sections,
            remaining_sections: state.remaining_sections,
            is_complete: state.is_complete,
          });
          /* Restore prior conversation — only messages from the current section */
          const currentSec = state.current_section;
          const history: ChatMessage[] = (state.conversation_history ?? [])
            .filter((m: { section?: string }) => !currentSec || m.section === currentSec)
            .map(
              (m: { role: string; content: string; section?: string }) => ({
                role: m.role as 'user' | 'assistant',
                content: m.content,
                section: m.section,
              }),
            );
          /* Restore token usage */
          if (state.token_usage) {
            setTokenUsage(state.token_usage);
          }
          if (history.length > 0) {
            setMessages(history);
          } else if (!state.is_complete) {
            sendMessage("Let's get started. What would you like to build?", true);
          }
          if (state.is_complete && history.length === 0) {
            setMessages([
              {
                role: 'assistant',
                content:
                  'The questionnaire is already complete! You can generate your contracts now.',
              },
            ]);
          }
        }
      } catch {
        /* ignore — we'll start fresh */
      }
    };
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, token]);

  /* ---- Send message ---- */
  const sendMessage = async (text?: string, isInitial?: boolean) => {
    const content = text ?? input.trim();
    if (!content) return;

    if (!isInitial) {
      setMessages((prev) => [...prev, { role: 'user', content }]);
    }
    setInput('');
    setSending(true);
    setError('');

    try {
      const res = await fetch(`${API_BASE}/projects/${projectId}/questionnaire`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: content }),
      });

      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        setError(d.detail || 'Failed to send message');
        setSending(false);
        return;
      }

      const data = await res.json();

      /* Detect section transition — clear visible messages for a fresh screen */
      const newCurrentSection = data.remaining_sections[0] ?? null;
      const prevSection = qState.current_section;
      const sectionChanged = prevSection && newCurrentSection && prevSection !== newCurrentSection;

      if (sectionChanged) {
        /* Section just completed — start fresh with only the transition reply */
        setMessages([{ role: 'assistant', content: data.reply, section: newCurrentSection }]);
      } else {
        setMessages((prev) => [...prev, { role: 'assistant', content: data.reply, section: newCurrentSection ?? prevSection ?? undefined }]);
      }

      setQState({
        current_section: newCurrentSection,
        completed_sections: data.completed_sections,
        remaining_sections: data.remaining_sections,
        is_complete: data.is_complete,
      });

      /* Update token usage */
      if (data.token_usage) {
        setTokenUsage(data.token_usage);
      }

    } catch {
      setError('Network error');
    } finally {
      setSending(false);
    }
  };

  /* ---- Generate contracts ---- */
  const handleStartGenerate = () => {
    setGeneratingContracts(true);
  };

  /* ---- Textarea auto-grow + Ctrl/Cmd+Enter submit ---- */
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      sendMessage();
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const autoGrow = (el: HTMLTextAreaElement) => {
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  };

  /* ---- Render ---- */
  return (
    <div style={overlayStyle} onClick={onClose} data-testid="questionnaire-overlay">
      <div style={modalStyle} onClick={(e) => e.stopPropagation()} data-testid="questionnaire-modal">
        {/* Header */}
        <div style={headerStyle}>
          <div>
            <h3 style={{ margin: 0, fontSize: '0.95rem', color: '#F8FAFC' }}>
              {generatingContracts ? `Generating Contracts — ${projectName}` : `Project Intake — ${projectName}`}
            </h3>
            {!generatingContracts && (
              <p style={{ margin: '2px 0 0', fontSize: '0.7rem', color: '#64748B' }}>
                {qState.is_complete
                  ? 'All sections complete ✓'
                  : qState.current_section
                    ? `Section: ${(buildMode === 'mini' ? MINI_SECTION_LABELS : SECTION_LABELS)[qState.current_section] ?? qState.current_section}`
                    : 'Starting...'}
              </p>
            )}
            <p style={{ margin: '2px 0 0', fontSize: '0.6rem', color: '#475569', letterSpacing: '0.3px' }}>
              Model: claude-sonnet-4-5
            </p>
            {/* Context window meter */}
            {(tokenUsage.input_tokens > 0 || tokenUsage.output_tokens > 0) && (() => {
              const totalTokens = tokenUsage.input_tokens + tokenUsage.output_tokens;
              const contextWindow = 200_000;
              const pct = Math.min((totalTokens / contextWindow) * 100, 100);
              const barColor = pct < 50 ? '#22C55E' : pct < 80 ? '#F59E0B' : '#EF4444';
              return (
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '4px' }}>
                  <div style={{
                    flex: 1,
                    height: '4px',
                    background: '#1E293B',
                    borderRadius: '2px',
                    overflow: 'hidden',
                    maxWidth: '120px',
                  }}>
                    <div style={{
                      width: `${pct}%`,
                      height: '100%',
                      background: barColor,
                      borderRadius: '2px',
                      transition: 'width 0.3s',
                    }} />
                  </div>
                  <span style={{ fontSize: '0.55rem', color: '#64748B', whiteSpace: 'nowrap' }}>
                    {totalTokens.toLocaleString()} / 200K
                  </span>
                </div>
              );
            })()}
          </div>
          <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
            {/* Restart questionnaire */}
            {!generatingContracts && <button
              onClick={async () => {
                if (!confirm('Restart the questionnaire? All answers will be cleared.')) return;
                setResetting(true);
                try {
                  const res = await fetch(`${API_BASE}/projects/${projectId}/questionnaire`, {
                    method: 'DELETE',
                    headers: { Authorization: `Bearer ${token}` },
                  });
                  if (res.ok) {
                    setMessages([]);
                    setQState({
                      current_section: 'product_intent',
                      completed_sections: [],
                      remaining_sections: [...activeSections],
                      is_complete: false,
                    });
                    setInput('');
                    setError('');
                    // Kick off the LLM again so the user sees the opening question
                    sendMessage("Let's get started. What would you like to build?", true);
                  }
                } catch { /* ignore */ }
                setResetting(false);
              }}
              disabled={resetting}
              title="Restart questionnaire"
              data-testid="restart-btn"
              style={{
                ...btnGhost,
                padding: '6px 10px',
                fontSize: '0.7rem',
                fontWeight: 600,
                color: '#F59E0B',
                borderColor: '#F59E0B33',
                opacity: resetting ? 0.5 : 1,
              }}
            >
              ↻ Restart
            </button>}
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              {generatingContracts && (
                <span style={{ color: '#64748B', fontSize: '0.65rem', whiteSpace: 'nowrap', display: 'flex', alignItems: 'center', gap: '4px' }}>
                  Continues in background <span style={{ fontSize: '0.8rem' }}>→</span>
                </span>
              )}
              <button
                onClick={() => {
                  if (generatingContracts) onDismissDuringGeneration?.();
                  onClose();
                }}
                style={{ ...btnGhost, padding: '6px 10px' }}
                data-testid="questionnaire-close"
              >
                ✕
              </button>
            </div>
          </div>
        </div>

        {/* Progress bar — hidden during contract generation */}
        {!generatingContracts && (
          <ProgressBar completed={qState.completed_sections} current={qState.current_section} skipped={[]} sections={activeSections} labels={buildMode === 'mini' ? MINI_SECTION_LABELS : SECTION_LABELS} />
        )}

        {/* Messages — hidden during contract generation */}
        {!generatingContracts && <div style={messagesStyle} data-testid="questionnaire-messages">
          {messages.map((msg, i) =>
            msg.role === 'assistant' ? (
              <AssistantBubble key={i} content={msg.content} />
            ) : (
              <UserBubble key={i} content={msg.content} />
            )
          )}

          {sending && (
            <div
              style={{
                alignSelf: 'flex-start',
                padding: '10px 14px',
                borderRadius: '14px 14px 14px 4px',
                background: '#0F172A',
                color: '#64748B',
                fontSize: '0.85rem',
              }}
            >
              <span className="typing-dots">Thinking…</span>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>}

        {/* Error */}
        {!generatingContracts && error && (
          <div style={{ padding: '0 20px 8px', color: '#EF4444', fontSize: '0.75rem' }} data-testid="questionnaire-error">
            {error}
          </div>
        )}

        {/* Contract generation progress — takes over the full body */}
        {generatingContracts && (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column' as const, minHeight: 0, overflow: 'auto' }}>
            <ContractProgress
              projectId={projectId}
              tokenUsage={tokenUsage}
              model="claude-sonnet-4-5"
              onComplete={onContractsGenerated}
              initialDone={initialDoneContracts}
              buildMode={buildMode}
            />
          </div>
        )}

        {/* Generate contracts banner */}
        {qState.is_complete && !generatingContracts && (
          <div
            style={{
              padding: '12px 20px',
              borderTop: '1px solid #334155',
              background: 'rgba(34,197,94,0.08)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: '12px',
              flexShrink: 0,
            }}
            data-testid="generate-banner"
          >
            <span style={{ color: '#22C55E', fontSize: '0.8rem', fontWeight: 600 }}>
              ✓ All sections complete — ready to generate contracts
            </span>
            <button
              onClick={handleStartGenerate}
              data-testid="generate-contracts-btn"
              style={{
                ...btnPrimary,
                background: '#16A34A',
                cursor: 'pointer',
              }}
            >
              Generate Contracts
            </button>
          </div>
        )}

        {/* Input bar — hidden during contract generation */}
        {!generatingContracts && <div style={inputBarStyle}>
          {/* Mic button */}
          {micSupported && (
            <button
              onClick={toggleMic}
              title={transcribing ? 'Transcribing...' : listening ? 'Stop recording' : 'Start voice input'}
              data-testid="mic-btn"
              disabled={transcribing}
              style={{
                width: '44px',
                height: '44px',
                borderRadius: '50%',
                border: 'none',
                background: transcribing ? '#F59E0B' : listening ? '#EF4444' : '#334155',
                color: '#fff',
                fontSize: '1.1rem',
                cursor: transcribing ? 'wait' : 'pointer',
                flexShrink: 0,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'background 0.2s',
                animation: listening ? 'pulse 1.5s infinite'
                  : transcribing ? 'pulse-amber 1.5s infinite' : 'none',
                opacity: transcribing ? 0.8 : 1,
              }}
            >
              {transcribing ? '⏳' : '🎙️'}
            </button>
          )}
          {voiceError && (
            <span style={{ color: '#EF4444', fontSize: '0.75rem', position: 'absolute', bottom: '-18px', left: '56px' }}>
              {voiceError}
            </span>
          )}

          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              autoGrow(e.target);
            }}
            onKeyDown={handleKeyDown}
            placeholder={transcribing ? 'Transcribing...' : listening ? 'Recording...' : 'Type or tap the mic to speak...'}
            rows={1}
            disabled={sending}
            data-testid="questionnaire-input"
            style={{
              ...textareaStyle,
              opacity: sending ? 0.5 : 1,
            }}
          />

          <button
            onClick={() => sendMessage()}
            disabled={sending || !input.trim()}
            data-testid="questionnaire-send"
            style={{
              ...btnPrimary,
              opacity: sending || !input.trim() ? 0.4 : 1,
              cursor: sending || !input.trim() ? 'default' : 'pointer',
              height: '44px',
            }}
          >
            Send
          </button>
        </div>}
      </div>

      {/* Pulse animation for mic */}
      <style>{`
        @keyframes pulse {
          0% { box-shadow: 0 0 0 0 rgba(239,68,68,0.5); }
          70% { box-shadow: 0 0 0 10px rgba(239,68,68,0); }
          100% { box-shadow: 0 0 0 0 rgba(239,68,68,0); }
        }
        @keyframes pulse-amber {
          0% { box-shadow: 0 0 0 0 rgba(245,158,11,0.5); }
          70% { box-shadow: 0 0 0 10px rgba(245,158,11,0); }
          100% { box-shadow: 0 0 0 0 rgba(245,158,11,0); }
        }
      `}</style>
    </div>
  );
}

export default QuestionnaireModal;
