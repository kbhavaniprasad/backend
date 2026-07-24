/**
 * Home.jsx — Main page
 * Voice agent controls + session history in one clean view.
 */

import React, { useState, useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import VoiceOrb from '../components/VoiceOrb';
import StatusBar from '../components/StatusBar';
import TranscriptBox from '../components/TranscriptBox';
import { useVoiceAgent } from '../hooks/useVoiceAgent';
import { getSessions, deleteSession } from '../services/api';

// ── Helpers ───────────────────────────────────────────────────────────────────
function formatDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso + 'Z');
  return d.toLocaleString();
}

function formatSecs(secs) {
  if (!secs) return '—';
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

// ── Styles ────────────────────────────────────────────────────────────────────
const S = {
  page: {
    minHeight: '100vh',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    padding: '48px 20px 80px',
    gap: '56px',
  },
  header: {
    textAlign: 'center',
  },
  badge: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '7px',
    padding: '5px 14px',
    borderRadius: '999px',
    border: '1px solid rgba(99,102,241,0.35)',
    background: 'rgba(99,102,241,0.1)',
    fontSize: '0.78rem',
    fontWeight: 600,
    color: '#818cf8',
    letterSpacing: '0.08em',
    textTransform: 'uppercase',
    marginBottom: '18px',
  },
  title: {
    fontSize: 'clamp(2rem, 5vw, 3.2rem)',
    fontWeight: 800,
    letterSpacing: '-0.02em',
    lineHeight: 1.15,
    marginBottom: '14px',
    background: 'linear-gradient(135deg, #f0f0ff 30%, #818cf8)',
    WebkitBackgroundClip: 'text',
    WebkitTextFillColor: 'transparent',
  },
  subtitle: {
    fontSize: '1.05rem',
    color: 'var(--text-muted)',
    maxWidth: '460px',
    margin: '0 auto',
  },
  card: {
    width: '100%',
    maxWidth: '560px',
    background: 'rgba(255,255,255,0.035)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-xl)',
    padding: '48px 36px',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: '36px',
    backdropFilter: 'blur(20px)',
    WebkitBackdropFilter: 'blur(20px)',
  },
  actions: {
    display: 'flex',
    gap: '12px',
  },
  historySection: {
    width: '100%',
    maxWidth: '700px',
  },
  sectionTitle: {
    fontSize: '1rem',
    fontWeight: 700,
    color: 'var(--text-primary)',
    marginBottom: '16px',
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
  },
  table: {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: '0.88rem',
  },
  th: {
    textAlign: 'left',
    padding: '10px 14px',
    color: 'var(--text-muted)',
    fontWeight: 600,
    fontSize: '0.78rem',
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    borderBottom: '1px solid var(--border)',
  },
  td: {
    padding: '12px 14px',
    borderBottom: '1px solid rgba(255,255,255,0.04)',
    color: 'var(--text-primary)',
    verticalAlign: 'middle',
  },
  statusBadge: (status) => ({
    display: 'inline-block',
    padding: '2px 10px',
    borderRadius: '999px',
    fontSize: '0.75rem',
    fontWeight: 600,
    background: status === 'active'
      ? 'rgba(52,211,153,0.15)'
      : 'rgba(107,114,128,0.15)',
    color: status === 'active' ? '#34d399' : '#9ca3af',
    border: `1px solid ${status === 'active' ? 'rgba(52,211,153,0.3)' : 'rgba(107,114,128,0.2)'}`,
  }),
  emptyHistory: {
    textAlign: 'center',
    padding: '32px',
    color: 'var(--text-muted)',
    fontSize: '0.9rem',
    background: 'rgba(255,255,255,0.025)',
    borderRadius: 'var(--radius-md)',
    border: '1px solid var(--border)',
  },
  deleteBtn: {
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    color: 'var(--text-muted)',
    fontSize: '1.1rem',
    transition: 'color 0.2s',
    padding: '2px 6px',
    borderRadius: '4px',
  },
};

// ── Component ─────────────────────────────────────────────────────────────────
export default function Home() {
  const { status, transcript, error, duration, start, stop } = useVoiceAgent();
  const [searchParams] = useSearchParams();
  const autoStartedRef = useRef(false);

  const [sessions,        setSessions]        = useState([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);

  // Auto-start call if URL has ?autostart=true
  useEffect(() => {
    if (searchParams.get('autostart') === 'true' && status === 'idle' && !autoStartedRef.current) {
      autoStartedRef.current = true;
      start();
    }
  }, [searchParams, status, start]);

  // Load sessions on mount and after a call ends
  const loadSessions = async () => {
    setSessionsLoading(true);
    try {
      const res = await getSessions();
      setSessions(res.data || []);
    } catch (e) {
      console.warn('Could not load sessions:', e.message);
    } finally {
      setSessionsLoading(false);
    }
  };

  useEffect(() => { loadSessions(); }, []);

  // Refresh sessions when call ends
  useEffect(() => {
    if (status === 'idle') loadSessions();
  }, [status]);

  const handleDelete = async (id) => {
    try {
      await deleteSession(id);
      setSessions(prev => prev.filter(s => s.id !== id));
    } catch (e) {
      console.warn('Delete failed:', e.message);
    }
  };

  const isActive     = status === 'active';
  const isConnecting = status === 'connecting';
  const isBusy       = isActive || isConnecting;

  return (
    <div style={S.page}>

      {/* ── Header ── */}
      <div style={S.header}>
        <div style={S.badge}>
          <span>🎙️</span> Retell AI Powered
        </div>
        <h1 style={S.title}>Nova Voice Agent</h1>
        <p style={S.subtitle}>
          Real-time AI voice conversations. Click start, speak naturally,
          and let the agent respond instantly.
        </p>
      </div>

      {/* ── Voice card ── */}
      <div style={S.card}>
        <VoiceOrb status={status} />
        <StatusBar status={status} duration={duration} error={error} />
        <TranscriptBox transcript={transcript} isActive={isActive} />

        <div style={S.actions}>
          {!isBusy ? (
            <button className="btn btn-primary" onClick={start} id="start-call-btn">
              🎤 Start Voice Call
            </button>
          ) : (
            <button className="btn btn-danger" onClick={stop} id="stop-call-btn">
              ⏹ End Call
            </button>
          )}
        </div>
      </div>

      {/* ── Session history ── */}
      <section style={S.historySection}>
        <div style={S.sectionTitle}>
          <span>📋</span> Call History
          <button
            className="btn btn-ghost"
            style={{ marginLeft: 'auto', padding: '6px 14px', fontSize: '0.8rem' }}
            onClick={loadSessions}
            disabled={sessionsLoading}
          >
            {sessionsLoading ? '↺ Loading…' : '↺ Refresh'}
          </button>
        </div>

        {sessions.length === 0 ? (
          <div style={S.emptyHistory}>
            No calls yet. Start a voice session above!
          </div>
        ) : (
          <table style={S.table}>
            <thead>
              <tr>
                <th style={S.th}>Started</th>
                <th style={S.th}>Duration</th>
                <th style={S.th}>Status</th>
                <th style={S.th}>Call ID</th>
                <th style={S.th}></th>
              </tr>
            </thead>
            <tbody>
              {sessions.map(s => (
                <tr key={s.id}>
                  <td style={S.td}>{formatDate(s.started_at)}</td>
                  <td style={S.td}>{formatSecs(s.duration_seconds)}</td>
                  <td style={S.td}>
                    <span style={S.statusBadge(s.status)}>{s.status}</span>
                  </td>
                  <td style={{ ...S.td, fontFamily: 'monospace', fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                    {s.call_id.slice(0, 18)}…
                  </td>
                  <td style={S.td}>
                    <button
                      style={S.deleteBtn}
                      onClick={() => handleDelete(s.id)}
                      title="Delete session"
                    >
                      🗑
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

    </div>
  );
}
