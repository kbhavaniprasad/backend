import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { createLead } from '../services/api';

const S = {
  container: {
    maxWidth: '950px',
    margin: '0 auto',
    padding: '40px 20px 80px',
    display: 'flex',
    flexDirection: 'column',
    gap: '48px',
  },
  hero: {
    textAlign: 'center',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: '20px',
  },
  badge: {
    padding: '6px 16px',
    borderRadius: '999px',
    background: 'rgba(99,102,241,0.12)',
    border: '1px solid rgba(99,102,241,0.3)',
    color: '#818cf8',
    fontSize: '0.85rem',
    fontWeight: 600,
    letterSpacing: '0.05em',
    textTransform: 'uppercase',
  },
  title: {
    fontSize: 'clamp(2.4rem, 5vw, 3.8rem)',
    fontWeight: 800,
    lineHeight: 1.1,
    background: 'linear-gradient(135deg, #ffffff 40%, #818cf8 100%)',
    WebkitBackgroundClip: 'text',
    WebkitTextFillColor: 'transparent',
    maxWidth: '850px',
  },
  subtitle: {
    fontSize: '1.15rem',
    color: 'var(--text-muted)',
    maxWidth: '650px',
  },
  ctaBox: {
    display: 'flex',
    gap: '16px',
    marginTop: '12px',
    flexWrap: 'wrap',
    justifyContent: 'center',
  },
  modalOverlay: {
    position: 'fixed',
    inset: 0,
    zIndex: 1000,
    padding: '24px 16px',
    overflowY: 'auto',
    background: 'rgba(0,0,0,0.72)',
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'flex-start',
  },
  modal: {
    width: '100%',
    maxWidth: '640px',
    margin: '32px 0',
  },
  card: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-xl)',
    padding: '36px',
    display: 'flex',
    flexDirection: 'column',
    gap: '20px',
    boxShadow: '0 20px 40px rgba(0,0,0,0.3)',
  },
  form: {
    display: 'flex',
    flexDirection: 'column',
    gap: '18px',
  },
  input: {
    width: '100%',
    padding: '14px 18px',
    borderRadius: 'var(--radius-md)',
    background: 'rgba(0,0,0,0.3)',
    border: '1px solid var(--border)',
    color: '#fff',
    fontSize: '0.95rem',
    outline: 'none',
  },
  textarea: {
    width: '100%',
    padding: '14px 18px',
    borderRadius: 'var(--radius-md)',
    background: 'rgba(0,0,0,0.3)',
    border: '1px solid var(--border)',
    color: '#fff',
    fontSize: '0.95rem',
    outline: 'none',
    minHeight: '100px',
    resize: 'vertical',
    fontFamily: 'inherit',
  },
  label: {
    fontSize: '0.85rem',
    fontWeight: 600,
    color: 'var(--text-muted)',
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
  },
};

export default function Landing() {
  const navigate = useNavigate();

  const [formData, setFormData] = useState({
    name: '',
    email: '',
    phone: '',
    company: '',
    requirement: '',
  });
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState('');
  const [showRegistrationForm, setShowRegistrationForm] = useState(false);

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handleInstantVoiceCall = async () => {
    try {
      setLoading(true);
      await createLead({
        name: 'Instant Voice Visitor',
        source: 'instant',
        agent_type: 'voice',
      });
      navigate('/voice?autostart=true');
    } catch (err) {
      alert('Error triggering Voice Agent: ' + err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleFormSubmit = async (e) => {
    e.preventDefault();
    if (!formData.name) return alert('Please enter your name');

    try {
      setLoading(true);
      setMsg('Registering lead and analyzing requirements...');
      
      await createLead({
        ...formData,
        source: 'form',
        agent_type: 'registration',
      });

      setMsg('✅ Lead registered successfully! Agent 2 (Supervisor) analyzed and categorized your lead.');
      setTimeout(() => {
        navigate('/dashboard');
      }, 1200);
    } catch (err) {
      setMsg('❌ Error: ' + err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={S.container}>

      {/* Hero Section */}
      <section style={S.hero}>
        <div style={S.badge}>
          🎙️ Retell AI Voice Agent & Agent 2 Supervisor
        </div>
        <h1 style={S.title}>
          Instant AI Voice Calls & Automated Lead Qualification
        </h1>
        <p style={S.subtitle}>
          Connect with an AI voice sales rep instantly via WebRTC audio. Agent 2 (Supervisor AI) monitors and evaluates transcripts after call completion.
        </p>

        {/* Demo CTAs */}
        <div style={S.ctaBox}>
          <button
            className="btn btn-primary"
            style={{ padding: '16px 36px', fontSize: '1.1rem' }}
            onClick={handleInstantVoiceCall}
            disabled={loading}
          >
            Get a Free Demo 1
          </button>
          <button
            className="btn btn-ghost"
            style={{ padding: '16px 36px', fontSize: '1.1rem' }}
            onClick={() => setShowRegistrationForm(true)}
            disabled={loading}
          >
            Get a Free Demo 2
          </button>
        </div>
      </section>

      {/* Registration Form Popup */}
      {showRegistrationForm && <div
        style={S.modalOverlay}
        onClick={() => !loading && setShowRegistrationForm(false)}
      >
        <div
          style={{ ...S.card, ...S.modal }}
          onClick={(event) => event.stopPropagation()}
          role="dialog"
          aria-modal="true"
          aria-labelledby="registration-form-title"
        >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{ fontSize: '2rem' }}>📋</div>
          <div>
            <h2 id="registration-form-title" style={{ fontSize: '1.4rem', fontWeight: 700 }}>Lead Registration Form</h2>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
              Fill in customer details to analyze, score, and store the lead in the Admin Dashboard.
            </p>
          </div>
          <button
            type="button"
            aria-label="Close registration form"
            onClick={() => setShowRegistrationForm(false)}
            disabled={loading}
            style={{ marginLeft: 'auto', background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', fontSize: '1.5rem' }}
          >
            ×
          </button>
        </div>

        <form style={S.form} onSubmit={handleFormSubmit}>
          <label style={S.label}>
            Full Name *
            <input
              style={S.input}
              type="text"
              name="name"
              placeholder="Jane Smith"
              value={formData.name}
              onChange={handleChange}
              required
            />
          </label>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
            <label style={S.label}>
              Email Address
              <input
                style={S.input}
                type="email"
                name="email"
                placeholder="jane@company.com"
                value={formData.email}
                onChange={handleChange}
              />
            </label>

            <label style={S.label}>
              Phone Number
              <input
                style={S.input}
                type="tel"
                name="phone"
                placeholder="+1 (555) 019-2834"
                value={formData.phone}
                onChange={handleChange}
              />
            </label>
          </div>

          <label style={S.label}>
            Company Name
            <input
              style={S.input}
              type="text"
              name="company"
              placeholder="Acme Enterprise Inc."
              value={formData.company}
              onChange={handleChange}
            />
          </label>

          <label style={S.label}>
            Requirement / Project Scope
            <textarea
              style={S.textarea}
              name="requirement"
              placeholder="Describe requirement, pricing inquiry, or integration details..."
              value={formData.requirement}
              onChange={handleChange}
            />
          </label>

          <button
            type="submit"
            className="btn btn-ghost"
            style={{ width: '100%', padding: '16px', fontSize: '1rem', marginTop: '8px' }}
            disabled={loading}
          >
            {loading ? 'Processing Lead...' : 'Submit & Register Lead →'}
          </button>

          {msg && (
            <div style={{
              fontSize: '0.9rem',
              color: msg.includes('❌') ? '#f87171' : '#818cf8',
              textAlign: 'center',
              padding: '10px',
              borderRadius: '8px',
              background: 'rgba(99,102,241,0.1)',
            }}>
              {msg}
            </div>
          )}
        </form>
        </div>
      </div>}

      {/* Agent 2 Supervisor Feature Highlights */}
      <div
        style={{
          ...S.card,
          background: 'linear-gradient(135deg, rgba(99,102,241,0.08) 0%, rgba(56,189,248,0.05) 100%)',
          border: '1px solid rgba(99,102,241,0.25)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <span style={{ fontSize: '2.4rem' }}>👁️</span>
          <div>
            <h3 style={{ fontSize: '1.25rem', fontWeight: 700 }}>Agent 2 (Supervisor AI) Transcript Evaluation</h3>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginTop: '4px' }}>
              When a voice call completes, Agent 2 processes the full Retell AI transcript, categorizes lead status (<code>Deal Closed</code>, <code>Interested</code>, <code>Review Later</code>, <code>Just Talked</code>, <code>Not Interested</code>), and logs it into the Admin Dashboard.
            </p>
          </div>
        </div>
      </div>

    </div>
  );
}
