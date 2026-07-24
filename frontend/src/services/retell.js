/**
 * retell.js — Thin wrapper around the Retell Web SDK
 * Keeps all SDK specifics in one place so the rest of the
 * app never needs to import the SDK directly.
 */

import { RetellWebClient } from 'retell-client-js-sdk';

/**
 * Create and return a configured RetellWebClient instance.
 * Pass in callbacks for each event you want to handle.
 *
 * @param {Object} handlers
 * @param {Function} handlers.onCallStarted   — call is live
 * @param {Function} handlers.onCallEnded     — call finished
 * @param {Function} handlers.onTranscriptUpdate — { transcript: Array }
 * @param {Function} handlers.onError         — SDK-level error
 */
export function createRetellClient({
  onCallStarted,
  onCallEnded,
  onTranscriptUpdate,
  onError,
}) {
  const client = new RetellWebClient();

  if (onCallStarted)      client.on('call_started',  onCallStarted);
  if (onCallEnded)        client.on('call_ended',    onCallEnded);
  if (onTranscriptUpdate) client.on('update',        onTranscriptUpdate);
  if (onError)            client.on('error',         onError);

  return client;
}

/**
 * Start a call on an existing client instance.
 * @param {RetellWebClient} client
 * @param {string} accessToken — from the backend /api/voice/start
 */
export async function startRetellCall(client, accessToken) {
  await client.startCall({ accessToken });
}

/**
 * Gracefully stop an ongoing call.
 * @param {RetellWebClient} client
 */
export function stopRetellCall(client) {
  if (client) {
    try {
      client.stopCall();
    } catch (_) {
      // Ignore if already stopped
    }
  }
}
