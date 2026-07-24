/**
 * StatusBar.jsx — Shows current connection status, duration, and errors
 */

import React from 'react';

const statusConfig = {
  idle:       { label: 'Ready to connect',       color: '#6b7280', dot: '#4b5563' },
  connecting: { label: 'Connecting to agent…',   color: '#38bdf8', dot: '#38bdf8' },
  active:     { label: 'Live — Agent listening', color: '#34d399', dot: '#34d399' },
  error:      { label: 'Connection failed',       color: '#f87171', dot: '#f87171' },
};

function formatDuration(seconds) {
  const m = Math.floor(seconds / 60).toString().padStart(2, '0');
  const s = (seconds % 60).toString().padStart(2, '0');
  return `${m}:${s}`;
}

const styles = {
  wrapper: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: '8px',
    minHeight: '52px',
  },
  row: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
  },
  dot: (color) => ({
    width: '9px',
    height: '9px',
    borderRadius: '50%',
    background: color,
    boxShadow: `0 0 8px ${color}`,
    flexShrink: 0,
  }),
  label: (color) => ({
    fontSize: '0.92rem',
    fontWeight: 500,
    color,
    letterSpacing: '0.01em',
  }),
  duration: {
    fontSize: '1.5rem',
    fontWeight: 700,
    fontVariantNumeric: 'tabular-nums',
    color: '#34d399',
    letterSpacing: '0.04em',
  },
  error: {
    fontSize: '0.85rem',
    color: '#f87171',
    background: 'rgba(248,113,113,0.1)',
    border: '1px solid rgba(248,113,113,0.25)',
    borderRadius: '8px',
    padding: '6px 14px',
    textAlign: 'center',
    maxWidth: '340px',
  },
};

export default function StatusBar({ status, duration, error }) {
  const cfg = statusConfig[status] || statusConfig.idle;

  return (
    <div style={styles.wrapper}>
      <div style={styles.row}>
        <div style={styles.dot(cfg.dot)} />
        <span style={styles.label(cfg.color)}>{cfg.label}</span>
      </div>

      {status === 'active' && (
        <div style={styles.duration}>{formatDuration(duration)}</div>
      )}

      {error && <div style={styles.error}>{error}</div>}
    </div>
  );
}
