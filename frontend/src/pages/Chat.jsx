import React, { useState, useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { sendChatMessage, getChatHistory } from '../services/api';

const S = {
  container: {
    maxWidth: '850px',
    margin: '0 auto',
    padding: '30px 20px 60px',
    display: 'flex',
    flexDirection: 'column',
    gap: '24px',
    height: 'calc(100vh - 100px)',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingBottom: '16px',
    borderBottom: '1px solid var(--border)',
  },
  statusBadge: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '6px',
    padding: '4px 12px',
    borderRadius: '999px',
    background: 'rgba(52,211,153,0.15)',
    color: '#34d399',
    fontSize: '0.8rem',
    fontWeight: 600,
  },
  chatWindow: {
    flex: 1,
    overflowY: 'auto',
    display: 'flex',
    flexDirection: 'column',
    gap: '16px',
    paddingRight: '8px',
  },
  bubble: (isUser) => ({
    maxWidth: '78%',
    alignSelf: isUser ? 'flex-end' : 'flex-start',
    background: isUser ? 'linear-gradient(135deg, var(--primary), #4f46e5)' : 'rgba(255,255,255,0.05)',
    border: isUser ? 'none' : '1px solid var(--border)',
    borderRadius: isUser ? '20px 20px 4px 20px' : '20px 20px 20px 4px',
    padding: '14px 18px',
    fontSize: '0.95rem',
    color: '#fff',
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
  }),
  correctionBadge: {
    fontSize: '0.75rem',
    background: 'rgba(245, 158, 11, 0.15)',
    border: '1px solid rgba(245, 158, 11, 0.4)',
    color: '#fbbf24',
    padding: '4px 10px',
    borderRadius: '6px',
    display: 'inline-flex',
    alignItems: 'center',
    gap: '6px',
    marginTop: '4px',
    fontWeight: 600,
  },
  inputBox: {
    display: 'flex',
    gap: '12px',
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-lg)',
    padding: '8px 12px',
  },
  input: {
    flex: 1,
    background: 'transparent',
    border: 'none',
    color: '#fff',
    fontSize: '0.95rem',
    outline: 'none',
    padding: '8px',
  },
  quickPromptBox: {
    display: 'flex',
    gap: '8px',
    overflowX: 'auto',
    paddingBottom: '4px',
  },
  chip: {
    padding: '6px 14px',
    borderRadius: '999px',
    background: 'rgba(255,255,255,0.05)',
    border: '1px solid var(--border)',
    color: 'var(--text-muted)',
    fontSize: '0.82rem',
    cursor: 'pointer',
    whiteSpace: 'nowrap',
  },
};

export default function Chat() {
  const [searchParams] = useSearchParams();
  const sessionIdRef = useRef(searchParams.get('session_id') || `chat_${Date.now()}`);

  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef(null);

  // Load existing chat history or set initial greeting
  useEffect(() => {
    async function loadHistory() {
      try {
        const res = await getChatHistory(sessionIdRef.current);
        if (res.data && res.data.length > 0) {
          setMessages(res.data);
        } else {
          // Greeting
          setMessages([
            {
              role: 'agent',
              content: "Hi! I'm your AI sales assistant. How can I help you today?",
              corrected: 0,
            },
          ]);
        }
      } catch (err) {
        console.warn('History load failed:', err);
      }
    }
    loadHistory();
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = async (textToSend) => {
    const text = textToSend || input;
    if (!text.trim() || loading) return;

    const userMsg = { role: 'user', content: text };
    setMessages((prev) => [...prev, userMsg]);
    if (!textToSend) setInput('');
    setLoading(true);

    try {
      const res = await sendChatMessage(sessionIdRef.current, text);
      const agentMsg = res.data.message;
      setMessages((prev) => [...prev, agentMsg]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: 'agent', content: 'Sorry, error processing message: ' + err.message },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={S.container}>
      
      {/* Header */}
      <div style={S.header}>
        <div>
          <h2 style={{ fontSize: '1.25rem', fontWeight: 700 }}>Sales AI Agent + Supervisor AI</h2>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
            Real-time quality scoring & auto-correction active
          </p>
        </div>
        <div style={S.statusBadge}>
          <span>●</span> Agent 2 Monitoring
        </div>
      </div>

      {/* Messages */}
      <div style={S.chatWindow}>
        {messages.map((m, idx) => (
          <div key={idx} style={S.bubble(m.role === 'user')}>
            <div>{m.content}</div>

            {/* Supervisor Correction Pill */}
            {m.role === 'agent' && (m.corrected === 1 || m.corrected === true) && (
              <div style={S.correctionBadge} title={m.correction_reason || 'Corrected by Supervisor'}>
                👁️ Corrected by Supervisor AI
                {m.original_content && (
                  <span style={{ opacity: 0.8, fontSize: '0.7rem' }}>
                    (Original: "{m.original_content}")
                  </span>
                )}
              </div>
            )}
          </div>
        ))}
        {loading && (
          <div style={S.bubble(false)}>
            <span style={{ color: 'var(--text-muted)' }}>Thinking & Evaluating response...</span>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Quick Prompts to Test Supervisor AI */}
      <div style={S.quickPromptBox}>
        <div
          style={S.chip}
          onClick={() => handleSend('Do you support WhatsApp integration?')}
        >
          🧪 Test: WhatsApp Support
        </div>
        <div
          style={S.chip}
          onClick={() => handleSend('Can I cancel anytime?')}
        >
          🧪 Test: Cancellation Policy
        </div>
        <div
          style={S.chip}
          onClick={() => handleSend('What is your pricing?')}
        >
          🧪 Test: Pricing
        </div>
      </div>

      {/* Input controls */}
      <form
        style={S.inputBox}
        onSubmit={(e) => {
          e.preventDefault();
          handleSend();
        }}
      >
        <input
          style={S.input}
          type="text"
          placeholder="Ask a question..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
        <button type="submit" className="btn btn-primary" disabled={loading}>
          Send
        </button>
      </form>
    </div>
  );
}
