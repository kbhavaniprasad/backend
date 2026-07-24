/**
 * TranscriptBox.jsx — Real-time conversation transcript
 * Scrolls to the latest message automatically.
 */

import React, { useEffect, useRef } from 'react';

const styles = {
  container: {
    width: '100%',
    maxWidth: '640px',
    borderRadius: 'var(--radius-md)',
    overflow: 'hidden',
    border: '1px solid var(--border)',
    background: 'rgba(0,0,0,0.25)',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '12px 16px',
    borderBottom: '1px solid var(--border)',
    fontSize: '0.8rem',
    fontWeight: 600,
    color: 'var(--text-muted)',
    letterSpacing: '0.08em',
    textTransform: 'uppercase',
  },
  liveTag: {
    display: 'flex',
    alignItems: 'center',
    gap: '5px',
    color: '#34d399',
    fontSize: '0.75rem',
  },
  liveDot: {
    width: '7px',
    height: '7px',
    borderRadius: '50%',
    background: '#34d399',
    animation: 'orb-idle 1.2s ease-in-out infinite',
  },
  messages: {
    height: '220px',
    overflowY: 'auto',
    padding: '12px 16px',
    display: 'flex',
    flexDirection: 'column',
    gap: '10px',
  },
  empty: {
    color: 'var(--text-dim)',
    fontSize: '0.88rem',
    textAlign: 'center',
    marginTop: '70px',
    fontStyle: 'italic',
  },
  message: (role) => ({
    display: 'flex',
    flexDirection: 'column',
    alignItems: role === 'user' ? 'flex-end' : 'flex-start',
    gap: '3px',
    animation: 'fadeIn 0.2s ease',
  }),
  bubble: (role) => ({
    maxWidth: '85%',
    padding: '9px 14px',
    borderRadius: role === 'user' ? '14px 14px 4px 14px' : '14px 14px 14px 4px',
    fontSize: '0.9rem',
    lineHeight: '1.5',
    background: role === 'user'
      ? 'linear-gradient(135deg, #4f46e5, #6366f1)'
      : 'rgba(255,255,255,0.06)',
    color: role === 'user' ? '#fff' : 'var(--text-primary)',
    border: role === 'user' ? 'none' : '1px solid var(--border)',
  }),
  roleName: (role) => ({
    fontSize: '0.72rem',
    color: 'var(--text-muted)',
    paddingLeft: role === 'user' ? 0 : '4px',
    paddingRight: role === 'user' ? '4px' : 0,
  }),
};

export default function TranscriptBox({ transcript, isActive }) {
  const bottomRef = useRef(null);

  // Auto-scroll on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [transcript]);

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span>Transcript</span>
        {isActive && (
          <div style={styles.liveTag}>
            <div style={styles.liveDot} />
            Live
          </div>
        )}
      </div>

      <div style={styles.messages}>
        {transcript.length === 0 ? (
          <div style={styles.empty}>
            {isActive
              ? 'Listening… start speaking!'
              : 'Transcript will appear here once the call starts.'}
          </div>
        ) : (
          transcript.map((entry, i) => (
            <div key={i} style={styles.message(entry.role)}>
              <span style={styles.roleName(entry.role)}>
                {entry.role === 'user' ? 'You' : 'Agent'}
              </span>
              <div style={styles.bubble(entry.role)}>{entry.content}</div>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
