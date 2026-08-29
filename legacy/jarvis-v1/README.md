# J.A.R.V.I.S — AI Desktop Assistant

> A fully local, always-listening AI voice assistant powered by **Ollama (LLaMA 3)**, **Faster-Whisper**, **Piper TTS**, and a native desktop UI — inspired by Iron Man's JARVIS.

---

## ✨ Features

| Feature | Description |
|---|---|
| 🎙️ **Always-Listening Voice Mode** | Continuously records from mic, transcribes with Whisper, and responds hands-free |
| 💬 **Text Input Mode** | Type queries directly in the desktop UI |
| 🧠 **LLaMA 3 Brain** | Runs 100% locally via Ollama — no API keys, no internet required |
| 🔊 **Neural Text-to-Speech** | Piper TTS with `en_US-lessac-medium` voice, streamed back as audio |
| 🗂️ **Persistent Memory** | ChromaDB + SentenceTransformers store and retrieve past conversations semantically |
| 📚 **Knowledge RAG** | Drop `.txt` or `.pdf` files into the `knowledge/` folder — Jarvis will answer from them |
| 🛠️ **Tool / Agent Mode** | LLM can autonomously trigger tools (open apps, Google Search, run shell commands, check time) |
| 🖥️ **Native Desktop Window** | Runs as a real macOS app via `pywebview`, no browser tab needed |
| 🔁 **Streaming Responses** | Tokens stream live to the UI over WebSocket for a real-time feel |

---

## 🗂️ Project Structure

```
Jarvis/
├── app.py              # Main desktop app (FastAPI + pywebview + voice loop)
├── voice_jarvis.py     # Standalone terminal voice assistant (no UI)
├── brain.py            # Minimal terminal text chatbot for testing
├── api.py              # Bare FastAPI REST endpoint for quick testing
├── agent.py            # Tool dispatcher / agent executor
├── tools.py            # Tool implementations (open_app, search, run_command, get_time)
├── memory.py           # Semantic memory: store & recall with ChromaDB
├── config.py           # Shared config constants
├── knowledge/
│   └── rag.py          # RAG: index and search .txt/.pdf files in knowledge/
├── static/
│   ├── index.html      # Desktop UI markup
│   ├── app.js          # WebSocket client + UI logic
│   └── styles.css      # Iron Man HUD-inspired styling
└── voices/
    └── en_US-lessac-medium.onnx  # Piper TTS voice model
```

---

## 🛠️ Prerequisites

- **macOS** (tested on macOS 13+)
- **Python 3.10+**
- **[Ollama](https://ollama.com)** installed and running

---

## ⚙️ Setup

### 1. Clone the repo
```bash
git clone https://github.com/your-username/Jarvis.git
cd Jarvis
```

### 2. Create and activate a virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Python dependencies
```bash
pip install \
  fastapi uvicorn pywebview \
  sounddevice scipy numpy \
  faster-whisper \
  ollama \
  chromadb sentence-transformers \
  pypdf piper-tts rich
```

### 4. Pull the LLaMA 3 model via Ollama
```bash
ollama pull llama3
```
Make sure Ollama is running in the background (`ollama serve`).

### 5. Download the Piper TTS voice model
```bash
mkdir -p voices
# Download the ONNX model + config file
curl -L -o voices/en_US-lessac-medium.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx

curl -L -o voices/en_US-lessac-medium.onnx.json \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
```

---

## 🚀 Usage

### Desktop App (recommended)
Launches a native macOS window with voice + text input, streaming responses, and audio playback.

```bash
python app.py
```

- The window opens automatically.
- Jarvis starts **listening immediately** — just speak.
- Use the **mic toggle** in the UI to pause/resume voice listening.
- Type in the text box to send messages without speaking.

### Terminal Voice Mode
A simpler, terminal-only voice assistant with no UI.

```bash
python voice_jarvis.py
```

Press `Ctrl+C` to stop.

### Terminal Text Chat (Testing)
```bash
python brain.py
```
Type `exit` to quit.

### REST API (Testing)
```bash
uvicorn api:app --reload
```
Then `POST` to `http://localhost:8000/chat` with `{"message": "your query"}`.

---

## 🛠️ Available Tools

Jarvis can autonomously use these tools when it detects the user's intent:

| Tool | Trigger | Example |
|---|---|---|
| `open_app` | "open chrome", "open vscode", "open terminal" | *"Open Chrome"* |
| `get_time` | "what time is it", "time" | *"What's the time?"* |
| `search_google` | "search …" | *"Search Python tutorials"* |
| `run_command` | "run …" | *"Run ls -la"* |

---

## 📚 Adding Your Own Knowledge (RAG)

Drop any `.txt` or `.pdf` file into the `knowledge/` folder. Jarvis will automatically index it with vector embeddings and use it to answer relevant questions.

```
knowledge/
├── rag.py          # (do not delete)
├── my_notes.txt    # ← add your files here
└── paper.pdf
```

The knowledge is searched every time you ask a question, and relevant excerpts are injected into the LLM context automatically.

---

## 🧠 How It Works

```
Mic → Whisper (Speech-to-Text) → Tool check → Memory recall + Knowledge RAG
 → LLaMA 3 (Ollama) → Streaming tokens → Piper TTS → Audio playback
                     ↓
               Memory stored (ChromaDB)
```

1. **Voice capture** — `sounddevice` records 4-second audio chunks.
2. **Transcription** — `faster-whisper` (base model, int8) converts speech to text.
3. **Tool routing** — keyword + LLM-based detection decides if a tool should run.
4. **Memory + RAG** — relevant past memories and knowledge docs are retrieved and injected.
5. **LLM inference** — LLaMA 3 streams a response via Ollama.
6. **TTS** — Piper synthesizes the reply; audio is sent to the frontend over WebSocket.

---

## 📦 Key Dependencies

| Package | Purpose |
|---|---|
| `ollama` | Local LLM inference (LLaMA 3) |
| `faster-whisper` | Fast, accurate speech-to-text |
| `piper-tts` | Offline neural text-to-speech |
| `fastapi` + `uvicorn` | WebSocket server backbone |
| `pywebview` | Native desktop window |
| `chromadb` | Vector database for memory & RAG |
| `sentence-transformers` | Text embeddings (`all-MiniLM-L6-v2`) |
| `sounddevice` | Microphone recording |
| `pypdf` | PDF parsing for knowledge base |

---
