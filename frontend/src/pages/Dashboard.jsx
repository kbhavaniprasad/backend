import React, { useState, useEffect } from 'react';
import { getDashboardStats } from '../services/api';

const S = {
  container: {
    maxWidth: '1150px',
    margin: '0 auto',
    padding: '40px 20px 80px',
    display: 'flex',
    flexDirection: 'column',
    gap: '36px',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  title: {
    fontSize: '1.8rem',
    fontWeight: 800,
  },
  subtitle: {
    color: 'var(--text-muted)',
    fontSize: '0.95rem',
  },
  metricsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
    gap: '20px',
  },
  metricCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-lg)',
    padding: '24px',
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
  },
  metricVal: {
    fontSize: '2rem',
    fontWeight: 800,
    color: '#fff',
  },
  metricLabel: {
    fontSize: '0.85rem',
    color: 'var(--text-muted)',
    fontWeight: 600,
  },
  section: {
    background: 'rgba(255,255,255,0.025)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-xl)',
    padding: '32px',
    display: 'flex',
    flexDirection: 'column',
    gap: '20px',
  },
  table: {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: '0.9rem',
  },
  th: {
    textAlign: 'left',
    padding: '12px 14px',
    color: 'var(--text-muted)',
    fontSize: '0.78rem',
    textTransform: 'uppercase',
    borderBottom: '1px solid var(--border)',
  },
  td: {
    padding: '14px',
    borderBottom: '1px solid rgba(255,255,255,0.04)',
  },
  progressTrack: {
    height: '8px',
    width: '100%',
    background: 'rgba(255,255,255,0.06)',
    borderRadius: '999px',
    overflow: 'hidden',
    marginTop: '6px',
  },
  progressBar: (pct, color) => ({
    height: '100%',
    width: `${pct}%`,
    background: color || 'var(--primary)',
    borderRadius: '999px',
  }),
};

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadStats = async () => {
    setLoading(true);
    try {
      const res = await getDashboardStats();
      setStats(res.data);
    } catch (err) {
      console.error('Failed to load stats:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadStats();
  }, []);

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '100px', color: 'var(--text-muted)' }}>
        Loading Dashboard Metrics...
      </div>
    );
  }

  const scores = stats?.ai_quality_scores || {};

  return (
    <div style={S.container}>

      {/* Header */}
      <div style={S.header}>
        <div>
          <h1 style={S.title}>Admin Control & AI Quality Dashboard</h1>
          <p style={S.subtitle}>Live lead engagement telemetry and Supervisor AI evaluations</p>
        </div>
        <button className="btn btn-ghost" onClick={loadStats}>
          🔄 Refresh
        </button>
      </div>

      {/* Top Metrics Cards */}
      <div style={S.metricsGrid}>
        <div style={S.metricCard}>
          <div style={S.metricLabel}>Total Leads Captured</div>
          <div style={S.metricVal}>{stats?.total_leads || 0}</div>
        </div>

        <div style={S.metricCard}>
          <div style={S.metricLabel}>Voice Calls Initiated</div>
          <div style={S.metricVal}>{stats?.total_calls || 0}</div>
        </div>

        <div style={S.metricCard}>
          <div style={S.metricLabel}>Avg Call Duration</div>
          <div style={S.metricVal}>{stats?.avg_call_duration || 0}s</div>
        </div>

        <div style={S.metricCard}>
          <div style={S.metricLabel}>Supervisor Corrections</div>
          <div style={{ ...S.metricVal, color: '#fbbf24' }}>
            {stats?.total_corrections || 0}
          </div>
        </div>
      </div>

      {/* AI Quality Scores Breakdown */}
      <div style={S.section}>
        <h2 style={{ fontSize: '1.2rem', fontWeight: 700 }}>AI Conversation Quality Metrics</h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '24px' }}>
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.88rem' }}>
              <span>Accuracy & Factuality</span>
              <span style={{ fontWeight: 700 }}>{scores.accuracy}%</span>
            </div>
            <div style={S.progressTrack}>
              <div style={S.progressBar(scores.accuracy, '#38bdf8')} />
            </div>
          </div>

          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.88rem' }}>
              <span>Empathy & Tone</span>
              <span style={{ fontWeight: 700 }}>{scores.empathy}%</span>
            </div>
            <div style={S.progressTrack}>
              <div style={S.progressBar(scores.empathy, '#818cf8')} />
            </div>
          </div>

          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.88rem' }}>
              <span>Sales & Qualification Skills</span>
              <span style={{ fontWeight: 700 }}>{scores.sales_skills}%</span>
            </div>
            <div style={S.progressTrack}>
              <div style={S.progressBar(scores.sales_skills, '#34d399')} />
            </div>
          </div>

          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.88rem' }}>
              <span>Policy Compliance</span>
              <span style={{ fontWeight: 700 }}>{scores.compliance}%</span>
            </div>
            <div style={S.progressTrack}>
              <div style={S.progressBar(scores.compliance, '#fbbf24')} />
            </div>
          </div>
        </div>
      </div>

      {/* Mistake & Correction Log */}
      <div style={S.section}>
        <h2 style={{ fontSize: '1.2rem', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span>👁️</span> Real-Time Supervisor AI Correction Log
        </h2>

        {(!stats?.recent_corrections || stats.recent_corrections.length === 0) ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
            No mistake corrections logged yet. Try sending a chat message about WhatsApp or cancellation terms to trigger a correction!
          </div>
        ) : (
          <table style={S.table}>
            <thead>
              <tr>
                <th style={S.th}>Mistake Caught</th>
                <th style={S.th}>Supervisor Correction</th>
                <th style={S.th}>Reason / Audit</th>
                <th style={S.th}>Score</th>
              </tr>
            </thead>
            <tbody>
              {stats.recent_corrections.map((c, i) => (
                <tr key={i}>
                  <td style={{ ...S.td, color: '#f87171', textDecoration: 'line-through' }}>
                    "{c.original_content}"
                  </td>
                  <td style={{ ...S.td, color: '#34d399', fontWeight: 600 }}>
                    "{c.content}"
                  </td>
                  <td style={{ ...S.td, color: 'var(--text-muted)', fontSize: '0.82rem' }}>
                    {c.correction_reason || 'Inaccurate agent response'}
                  </td>
                  <td style={S.td}>
                    <span style={{ padding: '2px 8px', borderRadius: '4px', background: 'rgba(251,191,36,0.2)', color: '#fbbf24', fontSize: '0.8rem' }}>
                      {c.quality_score}/100
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Recent Leads Table */}
      <div style={S.section}>
        <h2 style={{ fontSize: '1.2rem', fontWeight: 700 }}>Captured & Categorized Leads (Agent 2 Evaluated)</h2>
        {(!stats?.recent_leads || stats.recent_leads.length === 0) ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>No leads registered yet.</div>
        ) : (
          <table style={S.table}>
            <thead>
              <tr>
                <th style={S.th}>Name</th>
                <th style={S.th}>Contact</th>
                <th style={S.th}>Company</th>
                <th style={S.th}>Status (Agent 2)</th>
                <th style={S.th}>Lead Score</th>
                <th style={S.th}>Requirement / Summary</th>
              </tr>
            </thead>
            <tbody>
              {stats.recent_leads.map((l, i) => {
                const statusColors = {
                  'Deal Closed': { bg: 'rgba(52,211,153,0.2)', color: '#34d399' },
                  'Interested': { bg: 'rgba(99,102,241,0.2)', color: '#818cf8' },
                  'Review Later': { bg: 'rgba(251,191,36,0.2)', color: '#fbbf24' },
                  'Just Talked': { bg: 'rgba(148,163,184,0.2)', color: '#94a3b8' },
                  'Not Interested': { bg: 'rgba(248,113,113,0.2)', color: '#f87171' },
                };
                const st = statusColors[l.status] || { bg: 'rgba(99,102,241,0.2)', color: '#818cf8' };

                return (
                  <tr key={i}>
                    <td style={{ ...S.td, fontWeight: 600 }}>{l.name}</td>
                    <td style={S.td}>{l.email || l.phone || '—'}</td>
                    <td style={S.td}>{l.company || '—'}</td>
                    <td style={S.td}>
                      <span style={{ fontSize: '0.78rem', fontWeight: 700, padding: '4px 10px', borderRadius: '6px', background: st.bg, color: st.color }}>
                        {l.status || 'Interested'}
                      </span>
                    </td>
                    <td style={S.td}>
                      <span style={{ fontWeight: 700, color: l.lead_score >= 80 ? '#34d399' : '#fbbf24' }}>
                        {l.lead_score}/100
                      </span>
                    </td>
                    <td style={{ ...S.td, fontSize: '0.82rem', color: 'var(--text-muted)', maxWidth: '300px' }}>
                      {l.summary || l.requirement || '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

    </div>
  );
}
