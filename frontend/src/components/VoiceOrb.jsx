/**
 * VoiceOrb.jsx — Animated visual indicator for the voice agent state
 * idle → static circle with soft pulse
 * connecting → spinning ring
 * active → dynamic wave animation
 * error → red tint
 */

import React from 'react';

const styles = {
  wrapper: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: '24px',
  },
  orbContainer: {
    position: 'relative',
    width: '160px',
    height: '160px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  // Pulsing background rings
  ring: (delay, active, color) => ({
    position: 'absolute',
    inset: 0,
    borderRadius: '50%',
    border: `2px solid ${color}`,
    opacity: active ? 0.6 : 0,
    animation: active ? `pulse-ring 1.8s ${delay}s ease-out infinite` : 'none',
  }),
  orb: (status) => ({
    width: '110px',
    height: '110px',
    borderRadius: '50%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    cursor: 'default',
    position: 'relative',
    zIndex: 2,
    transition: 'all 0.4s ease',
    background: status === 'error'
      ? 'radial-gradient(circle at 35% 35%, #f87171, #dc2626)'
      : status === 'active'
        ? 'radial-gradient(circle at 35% 35%, #818cf8, #4f46e5)'
        : status === 'connecting'
          ? 'radial-gradient(circle at 35% 35%, #38bdf8, #0284c7)'
          : 'radial-gradient(circle at 35% 35%, #374151, #1f2937)',
    boxShadow: status === 'error'
      ? '0 0 40px rgba(248,113,113,0.45), 0 0 80px rgba(248,113,113,0.2)'
      : status === 'active'
        ? '0 0 50px rgba(99,102,241,0.6), 0 0 100px rgba(99,102,241,0.25)'
        : status === 'connecting'
          ? '0 0 40px rgba(56,189,248,0.5), 0 0 80px rgba(56,189,248,0.2)'
          : '0 0 20px rgba(55,65,81,0.3)',
    animation: status === 'active'
      ? 'orb-speaking 0.8s ease-in-out infinite'
      : status === 'connecting'
        ? 'none'
        : 'orb-idle 3s ease-in-out infinite',
  }),
  icon: {
    fontSize: '2.4rem',
    userSelect: 'none',
  },
  spinner: {
    position: 'absolute',
    inset: '-6px',
    borderRadius: '50%',
    border: '3px solid transparent',
    borderTopColor: '#38bdf8',
    animation: 'spin 0.9s linear infinite',
  },
  waves: {
    display: 'flex',
    alignItems: 'center',
    gap: '5px',
    height: '28px',
  },
  wave: (i, active) => ({
    width: '4px',
    borderRadius: '4px',
    background: 'var(--primary-glow)',
    height: active ? '4px' : '4px',
    animation: active ? `wave 0.7s ${i * 0.12}s ease-in-out infinite` : 'none',
  }),
};

export default function VoiceOrb({ status }) {
  const isActive     = status === 'active';
  const isConnecting = status === 'connecting';
  const isError      = status === 'error';

  const ringColor = isError ? '#f87171' : isActive ? '#818cf8' : '#38bdf8';

  const icon = isError ? '⚠️' : isActive ? '🎙️' : isConnecting ? '⟳' : '🎤';

  return (
    <div style={styles.wrapper}>
      <div style={styles.orbContainer}>
        {/* Pulse rings (show when active) */}
        {(isActive || isConnecting) && (
          <>
            <div style={styles.ring('0s',   true, ringColor)} />
            <div style={styles.ring('0.5s', true, ringColor)} />
            <div style={styles.ring('1s',   true, ringColor)} />
          </>
        )}

        {/* Main orb */}
        <div style={styles.orb(status)}>
          {isConnecting && <div style={styles.spinner} />}
          <span style={styles.icon}>{icon}</span>
        </div>
      </div>

      {/* Wave bars shown when active */}
      <div style={styles.waves}>
        {[0,1,2,3,4].map(i => (
          <div key={i} style={styles.wave(i, isActive)} />
        ))}
      </div>
    </div>
  );
}
