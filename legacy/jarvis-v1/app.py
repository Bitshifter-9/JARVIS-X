"""
J.A.R.V.I.S — Desktop AI Assistant
Run:  python app.py
A native desktop window will open automatically.
Voice recording happens on the Python side (sounddevice), not in the browser.
"""

import os
import json
import base64
import asyncio
import subprocess
import tempfile
import threading
import time

import uvicorn
import sounddevice as sd
import numpy as np
import scipy.io.wavfile as wav
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import ollama
from faster_whisper import WhisperModel
from memory import store_memory, recall_memory
from tools import open_app, get_time, search_google, run_command
from agent import execute_tool
from knowledge.rag import search_knowledge

# ─── Config ──────────────────────────────────────────────────────────────
MODEL_NAME = "llama3:latest"
VOICE_MODEL = "voices/en_US-lessac-medium.onnx"
SAMPLE_RATE = 16000
RECORD_DURATION = 4  # seconds, same as voice_jarvis.py

# ─── Lazy-load Whisper ───────────────────────────────────────────────────
_whisper = None

def get_whisper():
    global _whisper
    if _whisper is None:
        print("⏳ Loading Whisper model …")
        _whisper = WhisperModel("base", compute_type="int8")
    return _whisper

# ─── System Prompt ───────────────────────────────────────────────────────
SYSTEM_PROMPT = """
You are Jarvis, a smart, calm, and helpful AI voice assistant.
Be concise, clear, and intelligent.
Speak naturally like a real assistant.

You also have access to tools and can act autonomously.

When a user's request requires an ACTION, respond ONLY in this exact format:
ACTION: <tool_name> | <argument>

Available tools:
- open_app (chrome, vscode, terminal, safari, youtube)
- get_time (no argument)
- search_google (query)
- run_command (shell command)

If NO tool is required, respond normally like a helpful assistant.

Do NOT explain the action.
Do NOT say "I will open chrome".
Just output the ACTION line when needed.
"""

# ─── FastAPI App ─────────────────────────────────────────────────────────
app = FastAPI(title="Jarvis")

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")


# ─── Helper: TTS ────────────────────────────────────────────────────────
def generate_tts(text: str) -> str | None:
    """Run Piper TTS and return base64-encoded WAV, or None on failure."""
    try:
        out_path = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        subprocess.run(
            ["python", "-m", "piper", "--model", VOICE_MODEL, "--output-file", out_path],
            input=text.encode(),
            check=True,
            capture_output=True,
        )
        with open(out_path, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode()
        os.unlink(out_path)
        return audio_b64
    except Exception as e:
        print(f"TTS error: {e}")
        return None


# ─── Helper: parse ACTION ───────────────────────────────────────────────
def parse_action(text: str):
    if not text.startswith("ACTION:"):
        return None, None
    try:
        action = text.replace("ACTION:", "").strip()
        tool, arg = action.split("|", 1)
        return tool.strip(), arg.strip()
    except Exception:
        return None, None


# ─── Helper: keyword-based tool detection ───────────────────────────────
def try_tools(user_text: str):
    text = user_text.lower()
    if "open chrome" in text or "chrome" in text:
        return open_app("chrome")
    if "open safari" in text or "safari" in text:
        return open_app("safari")
    if "open vscode" in text or "open code" in text or "visual studio" in text:
        return open_app("vscode")
    if "open terminal" in text or "terminal" in text:
        return open_app("terminal")
    if "open youtube" in text or "youtube" in text:
        return open_app("youtube")
    if "time" in text:
        return get_time()
    if "search" in text:
        query = text.replace("search", "").strip()
        return search_google(query)
    if text.startswith("run"):
        cmd = text.replace("run", "", 1).strip()
        return run_command(cmd)
    return None


# ─── Helper: Record audio using sounddevice ─────────────────────────────
def record_audio() -> str:
    """Record RECORD_DURATION seconds from mic, save to temp WAV, return path."""
    audio = sd.rec(
        int(SAMPLE_RATE * RECORD_DURATION),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype=np.int16,
    )
    sd.wait()
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wav.write(tmp.name, SAMPLE_RATE, audio)
    return tmp.name


# ─── Helper: Transcribe audio ───────────────────────────────────────────
def transcribe_audio(audio_path: str) -> str:
    segments, _ = get_whisper().transcribe(audio_path)
    text = "".join(seg.text for seg in segments).strip()
    return text


# ─── Connected WebSocket clients ────────────────────────────────────────
# We use a simple global to hold the active WebSocket + conversation
active_ws = None
active_ws_lock = threading.Lock()
conversation_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
voice_listening = True  # controlled by frontend toggle


# ─── Process a user message (shared between voice & text) ───────────────
async def process_message(ws: WebSocket, user_text: str):
    """Handle a user message: tools → LLM stream → TTS → send to frontend."""
    global conversation_messages

    # ── Quick tool check ──
    tool_result = try_tools(user_text)
    if tool_result:
        await ws.send_text(json.dumps({"type": "tool_result", "text": tool_result}))
        audio_b64 = generate_tts(tool_result)
        if audio_b64:
            await ws.send_text(json.dumps({"type": "audio", "data": audio_b64}))
        return

    # ── Memory + RAG context ──
    memories = recall_memory(user_text)
    memory_ctx = ""
    if memories:
        memory_ctx = "\nRelevant past memory:\n" + "\n".join(memories)

    knowledge_ctx = ""
    knowledge = search_knowledge(user_text)
    if knowledge:
        knowledge_ctx = "\nRelevant knowledge:\n" + knowledge

    enriched = user_text + memory_ctx + knowledge_ctx
    conversation_messages.append({"role": "user", "content": enriched})

    # ── Stream LLM response ──
    await ws.send_text(json.dumps({"type": "stream_start"}))

    stream = ollama.chat(model=MODEL_NAME, messages=conversation_messages, stream=True)
    reply = ""
    for chunk in stream:
        token = chunk["message"]["content"]
        reply += token
        await ws.send_text(json.dumps({"type": "token", "text": token}))

    await ws.send_text(json.dumps({"type": "stream_end"}))

    # ── Check if LLM triggered a tool action ──
    tool, arg = parse_action(reply.strip())
    if tool:
        result = execute_tool(tool, arg)
        await ws.send_text(json.dumps({"type": "tool_result", "text": f"[{tool}] {result}"}))
        audio_b64 = generate_tts(result)
        if audio_b64:
            await ws.send_text(json.dumps({"type": "audio", "data": audio_b64}))
    else:
        conversation_messages.append({"role": "assistant", "content": reply})
        store_memory("User: " + user_text)
        store_memory("Jarvis: " + reply)

        audio_b64 = generate_tts(reply)
        if audio_b64:
            await ws.send_text(json.dumps({"type": "audio", "data": audio_b64}))


# ─── Voice Listening Loop (runs in background thread) ────────────────────
def voice_loop(loop):
    """Continuously record → transcribe → process, just like voice_jarvis.py."""
    global active_ws, voice_listening

    # Wait for Whisper to be ready
    get_whisper()
    print("🎙️  Voice loop started — always listening")

    while True:
        # Wait until we have an active websocket and listening is enabled
        if active_ws is None or not voice_listening:
            time.sleep(0.5)
            continue

        try:
            # Notify frontend: listening
            asyncio.run_coroutine_threadsafe(
                active_ws.send_text(json.dumps({"type": "status", "state": "listening"})),
                loop
            ).result(timeout=2)

            # Record audio
            audio_path = record_audio()

            # Notify frontend: processing
            asyncio.run_coroutine_threadsafe(
                active_ws.send_text(json.dumps({"type": "status", "state": "thinking"})),
                loop
            ).result(timeout=2)

            # Transcribe
            user_text = transcribe_audio(audio_path)
            os.unlink(audio_path)

            if not user_text or len(user_text.strip()) < 2:
                continue

            print(f"\n🗣️  You said: {user_text}")

            # Send user text to frontend
            asyncio.run_coroutine_threadsafe(
                active_ws.send_text(json.dumps({"type": "voice_input", "text": user_text})),
                loop
            ).result(timeout=2)

            # Process the message
            asyncio.run_coroutine_threadsafe(
                process_message(active_ws, user_text),
                loop
            ).result(timeout=120)

        except Exception as e:
            print(f"Voice loop error: {e}")
            time.sleep(1)


# ─── WebSocket endpoint ─────────────────────────────────────────────────
@app.websocket("/ws/chat")
async def websocket_chat(ws: WebSocket):
    global active_ws, voice_listening
    await ws.accept()

    with active_ws_lock:
        active_ws = ws

    try:
        while True:
            data = await ws.receive_text()
            payload = json.loads(data)

            # Handle voice toggle from frontend
            if payload.get("type") == "voice_toggle":
                voice_listening = payload.get("enabled", True)
                print(f"🎙️  Voice listening: {'ON' if voice_listening else 'OFF'}")
                continue

            # Handle text messages
            user_text = payload.get("text", "").strip()
            if not user_text:
                continue

            await process_message(ws, user_text)

    except WebSocketDisconnect:
        print("Client disconnected")
        with active_ws_lock:
            active_ws = None


# ─── Run as Desktop App ──────────────────────────────────────────────────
if __name__ == "__main__":
    import webview

    PORT = 8000

    # Mutable container to share the event loop between threads
    _loop_holder = [None]

    def start_server():
        _loop_holder[0] = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop_holder[0])
        config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", loop="asyncio")
        server = uvicorn.Server(config)
        _loop_holder[0].run_until_complete(server.serve())

    # Start the API server
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # Wait for server to start and event loop to be created
    while _loop_holder[0] is None:
        time.sleep(0.1)
    time.sleep(1)

    # Start the voice listening loop
    voice_thread = threading.Thread(target=voice_loop, args=(_loop_holder[0],), daemon=True)
    voice_thread.start()

    # Open native desktop window
    window = webview.create_window(
        title="J.A.R.V.I.S — AI Assistant",
        url=f"http://127.0.0.1:{PORT}",
        width=540,
        height=820,
        resizable=True,
        min_size=(400, 600),
    )
    webview.start()
