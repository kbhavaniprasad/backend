/**
 * useVoiceAgent.js — Voice session lifecycle hook
 * Manages: connecting → active → idle states,
 * live transcript, duration timer, and auto-cleanup.
 */

import { useState, useCallback, useRef } from 'react';
import { startCall, stopCall } from '../services/api';
import { createRetellClient, startRetellCall, stopRetellCall } from '../services/retell';

/**
 * Status values:
 *  'idle'       — no call running
 *  'connecting' — waiting for Retell session to open
 *  'active'     — microphone open, agent responding
 *  'error'      — something went wrong (see `error`)
 */
export function useVoiceAgent() {
  const [status,     setStatus]     = useState('idle');
  const [callId,     setCallId]     = useState(null);
  const [transcript, setTranscript] = useState([]);
  const [error,      setError]      = useState(null);
  const [duration,   setDuration]   = useState(0);

  const clientRef    = useRef(null);
  const callIdRef    = useRef(null);
  const startTimeRef = useRef(null);
  const timerRef     = useRef(null);
  const transcriptRef = useRef([]);

  // ── Stop helper (shared by user action and SDK event) ──────────────────────
  const handleCallEnded = useCallback(async () => {
    clearInterval(timerRef.current);
    const dur = startTimeRef.current
      ? Math.floor((Date.now() - startTimeRef.current) / 1000)
      : 0;

    setStatus('idle');
    setDuration(0);

    // Format transcript list into timestamped text for lead extraction
    const formattedTranscript = transcriptRef.current.map(item => {
      const role = item.role === 'user' ? 'User' : 'Agent';
      return `[${new Date().toLocaleTimeString()}] ${role}: ${item.content}`;
    }).join('\n');

    // Persist session + transcript to backend for lead collection
    if (callIdRef.current) {
      try {
        await stopCall(callIdRef.current, dur, formattedTranscript);
      } catch (e) {
        console.warn('Could not save session:', e.message);
      }
    }

    clientRef.current = null;
    callIdRef.current = null;
    startTimeRef.current = null;
  }, []);

  // ── Start call ─────────────────────────────────────────────────────────────
  const start = useCallback(async () => {
    setError(null);
    setTranscript([]);
    transcriptRef.current = [];
    setStatus('connecting');

    try {
      // 1. Get access token from our FastAPI backend
      const result = await startCall();
      const { access_token, call_id } = result.data;

      setCallId(call_id);
      callIdRef.current = call_id;

      // 2. Create the Retell SDK client
      const client = createRetellClient({
        onCallStarted: () => {
          setStatus('active');
          startTimeRef.current = Date.now();

          // Tick duration every second
          timerRef.current = setInterval(() => {
            setDuration(Math.floor((Date.now() - startTimeRef.current) / 1000));
          }, 1000);
        },

        onCallEnded: handleCallEnded,

        onTranscriptUpdate: (update) => {
          // Retell sends the full transcript array on every update
          if (update?.transcript) {
            setTranscript([...update.transcript]);
            transcriptRef.current = [...update.transcript];
          }
        },

        onError: (err) => {
          console.error('Retell SDK error:', err);
          setError('Voice connection error — please try again.');
          setStatus('error');
          clearInterval(timerRef.current);
        },
      });

      clientRef.current = client;

      // 3. Open the WebRTC voice session
      await startRetellCall(client, access_token);

    } catch (err) {
      console.error('Failed to start call:', err);
      setError(err.message || 'Failed to connect. Is the backend running?');
      setStatus('error');
      clientRef.current = null;
    }
  }, [handleCallEnded]);

  // ── Stop call (user-initiated) ─────────────────────────────────────────────
  const stop = useCallback(() => {
    stopRetellCall(clientRef.current);
    handleCallEnded();
  }, [handleCallEnded]);

  return { status, callId, transcript, error, duration, start, stop };
}
