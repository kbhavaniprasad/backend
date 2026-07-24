/**
 * api.js — Backend fetch wrapper
 * All calls to our FastAPI backend go through here.
 * Consistent error handling, no duplicated fetch logic.
 */

const BASE_URL = '/api'; // Proxied to http://localhost:8000 by Vite

async function request(method, path, body = null) {
  const options = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body) options.body = JSON.stringify(body);

  const res = await fetch(`${BASE_URL}${path}`, options);
  const json = await res.json();

  // FastAPI raises with a `detail` key on errors
  if (!res.ok) {
    const msg = json?.detail?.message || json?.detail || 'Request failed';
    throw new Error(msg);
  }
  return json;
}

/**
 * Ask the backend to create a Retell web call.
 * Returns { access_token, call_id, agent_id }.
 */
export async function startCall(agentId = null) {
  return request('POST', '/voice/start', { agent_id: agentId });
}

/**
 * Tell the backend the call has ended.
 */
export async function stopCall(callId, durationSeconds = null, transcript = null) {
  return request('POST', '/voice/stop', {
    call_id: callId,
    duration_seconds: durationSeconds,
    transcript,
  });
}

/**
 * Fetch the last 20 call sessions.
 */
export async function getSessions() {
  return request('GET', '/sessions');
}

/**
 * Delete a session by its database ID.
 */
export async function deleteSession(id) {
  return request('DELETE', `/sessions/${id}`);
}

/**
 * Fetch agent metadata from Retell.
 */
export async function getAgentInfo() {
  return request('GET', '/agent');
}

/**
 * Save lead from registration form or instant CTA.
 */
export async function createLead(leadData) {
  return request('POST', '/leads', leadData);
}

/**
 * List all leads for dashboard.
 */
export async function getLeads() {
  return request('GET', '/leads');
}

/**
 * Trigger Voice or Chat Agent for a lead or instant engagement.
 */
export async function triggerAgent(leadId = null, agentType = 'voice') {
  return request('POST', '/trigger-agent', { lead_id: leadId, agent_type: agentType });
}

/**
 * Send live chat message to Sales AI & get Supervisor evaluated response.
 */
export async function sendChatMessage(sessionId, message) {
  return request('POST', '/chat/message', { session_id: sessionId, message });
}

/**
 * Get chat history for a session.
 */
export async function getChatHistory(sessionId) {
  return request('GET', `/chat/${sessionId}/history`);
}

/**
 * Fetch stats & metrics for admin dashboard.
 */
export async function getDashboardStats() {
  return request('GET', '/dashboard/stats');
}
