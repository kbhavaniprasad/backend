# 🎙️ Nova Voice Agent

A simple, fast, production-quality AI voice agent powered by **Retell AI** and built with **FastAPI + React**.

No Docker. No Redis. No cloud infra. Just Python and Node.

---

## ✨ Features

- **One-click real-time voice calls** — WebRTC audio directly in the browser
- **Live transcript** — see the conversation as it happens
- **Call history** — stored in a local SQLite database
- **Auto-reconnect** — handles network hiccups gracefully
- **In-memory rate limiting** — no Redis needed
- **Clean logs** — written to `backend/server.log`

---

## 📁 Project Structure

```
backend/
├── main.py          ← FastAPI entry point
├── api.py           ← REST route handlers
├── voice.py         ← Retell AI HTTP integration
├── config.py        ← .env loader
├── models.py        ← Pydantic request/response models
├── database.py      ← SQLite session storage (aiosqlite)
├── utils.py         ← Rate limiter + response helpers
└── requirements.txt

frontend/
├── index.html
├── vite.config.js   ← Proxies /api → localhost:8000
└── src/
    ├── App.jsx
    ├── main.jsx
    ├── index.css
    ├── pages/       Home.jsx
    ├── components/  VoiceOrb · StatusBar · TranscriptBox
    ├── hooks/       useVoiceAgent.js
    └── services/    api.js · retell.js

.env                 ← Secrets (never commit)
README.md
```

---

## ⚙️ Environment Variables

Edit `.env` in the project root:

```env
# Retell AI credentials
RETELL_API_KEY=key_...
RETELL_AGENT_ID=agent_...
RETELL_BASE_URL=https://api.retellai.com

# Backend server
PORT=8000
HOST=0.0.0.0

# Allowed frontend origins (CORS)
ALLOWED_ORIGINS=http://localhost:5173,http://localhost:3000

# SQLite file path (relative to backend/)
DB_PATH=voice_agent.db

# Rate limiting
RATE_LIMIT_REQUESTS=20
RATE_LIMIT_WINDOW=60
```

---

## 🚀 Running Locally

### 1. Backend

```powershell
cd backend

# Create virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS / Linux

# Install dependencies
pip install -r requirements.txt

# Start the server
python main.py
```

Server starts at → **http://localhost:8000**  
API docs at → **http://localhost:8000/api/docs**

### 2. Frontend

```powershell
cd frontend
npm install
npm run dev
```

App opens at → **http://localhost:5173**

---

## 🔌 API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/api/voice/start` | Create Retell web call → returns access token |
| `POST` | `/api/voice/stop` | End call, save to DB |
| `GET` | `/api/sessions` | List last 20 sessions |
| `DELETE` | `/api/sessions/{id}` | Delete a session |
| `GET` | `/api/agent` | Fetch agent metadata |

All responses follow this envelope:
```json
{
  "success": true,
  "message": "...",
  "data": {},
  "error": null
}
```

---

## 🛠 Troubleshooting

| Problem | Fix |
|---------|-----|
| `RETELL_API_KEY is not set` | Check `.env` — make sure the key is correct |
| `502 Could not start call` | Check Retell dashboard — agent ID must be valid |
| Frontend can't reach backend | Make sure `python main.py` is running on port 8000 |
| Microphone not working | Allow microphone access in browser permissions |
| `Port 8000 in use` | Change `PORT=8001` in `.env` and update `vite.config.js` proxy target |

---

## 📦 Dependencies

**Backend** — Python 3.11+
- `fastapi` — web framework
- `uvicorn` — ASGI server
- `httpx` — async HTTP client (Retell API calls)
- `aiosqlite` — async SQLite
- `python-dotenv` — .env loading
- `pydantic` — data validation

**Frontend** — Node 18+
- `react` — UI library
- `vite` — dev server + bundler
- `retell-client-js-sdk` — official Retell Web SDK (WebRTC)
