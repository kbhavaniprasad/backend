import React from 'react';
import { Link, useLocation } from 'react-router-dom';

const S = {
  nav: {
    width: '100%',
    maxWidth: '1200px',
    margin: '0 auto',
    padding: '20px 24px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderBottom: '1px solid var(--border)',
  },
  logo: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    fontSize: '1.25rem',
    fontWeight: 800,
    color: '#fff',
    textDecoration: 'none',
  },
  links: {
    display: 'flex',
    alignItems: 'center',
    gap: '24px',
  },
  link: (active) => ({
    color: active ? '#818cf8' : 'var(--text-muted)',
    textDecoration: 'none',
    fontWeight: 600,
    fontSize: '0.92rem',
    transition: 'color 0.2s',
    padding: '6px 12px',
    borderRadius: '8px',
    background: active ? 'rgba(99,102,241,0.1)' : 'transparent',
    border: active ? '1px solid rgba(99,102,241,0.3)' : '1px solid transparent',
  }),
};

export default function Navbar() {
  const location = useLocation();

  return (
    <header style={S.nav}>
      <Link to="/" style={S.logo}>
        <span style={{ fontSize: '1.5rem' }}>⚡</span> Nova AI Lead Platform
      </Link>

      <nav style={S.links}>
        <Link to="/" style={S.link(location.pathname === '/')}>
          Lead Registration
        </Link>
        <Link to="/dashboard" style={S.link(location.pathname === '/dashboard')}>
          Admin Dashboard
        </Link>
      </nav>
    </header>
  );
}
