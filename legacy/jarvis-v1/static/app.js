/* ═══════════════════════════════════════════════════════════════════════
   J.A.R.V.I.S — Desktop AI Assistant
   Voice recording is done by the Python backend (sounddevice).
   Frontend only displays conversation + allows text input.
   ═══════════════════════════════════════════════════════════════════════ */

// ─── DOM ────────────────────────────────────────────────────────────────
const chatMessages = document.getElementById('chatMessages');
const msgInput = document.getElementById('msgInput');
const sendBtn = document.getElementById('sendBtn');
const micToggle = document.getElementById('micToggle');
const micOnIcon = document.getElementById('micOnIcon');
const micOffIcon = document.getElementById('micOffIcon');
const micLabel = document.getElementById('micLabel');
const arcReactor = document.getElementById('arcReactor');
const statusDot = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');
const voiceControls = document.getElementById('voiceControls');
const textControls = document.getElementById('textControls');
const toTextBtn = document.getElementById('toTextBtn');
const toVoiceBtn = document.getElementById('toVoiceBtn');

// ─── State ──────────────────────────────────────────────────────────────
let ws = null;
let currentStreamEl = null;
let isStreaming = false;
let audioQueue = [];
let isPlayingAudio = false;
let inputMode = 'voice';
let voiceEnabled = true;

// ─── Mode Toggle ────────────────────────────────────────────────────────
toTextBtn.addEventListener('click', () => {
    inputMode = 'text';
    voiceControls.classList.add('hidden');
    textControls.classList.add('active');
    msgInput.focus();
    // Pause voice listening when in text mode
    if (voiceEnabled) {
        toggleVoice(false);
    }
});

toVoiceBtn.addEventListener('click', () => {
    inputMode = 'voice';
    voiceControls.classList.remove('hidden');
    textControls.classList.remove('active');
    // Resume voice listening
    if (!voiceEnabled) {
        toggleVoice(true);
    }
});

// ─── Mic Toggle (pause / resume server-side listening) ──────────────────
micToggle.addEventListener('click', () => {
    toggleVoice(!voiceEnabled);
});

function toggleVoice(enabled) {
    voiceEnabled = enabled;
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'voice_toggle', enabled }));
    }
    if (enabled) {
        micToggle.classList.add('active-listening');
        micToggle.classList.remove('paused');
        micOnIcon.style.display = 'block';
        micOffIcon.style.display = 'none';
        micLabel.textContent = 'LISTENING…';
    } else {
        micToggle.classList.remove('active-listening');
        micToggle.classList.add('paused');
        micOnIcon.style.display = 'none';
        micOffIcon.style.display = 'block';
        micLabel.textContent = 'PAUSED';
    }
}

// ─── Status Helpers ─────────────────────────────────────────────────────
function setStatus(state) {
    const labels = {
        online: 'SYSTEM ONLINE',
        listening: 'LISTENING…',
        thinking: 'PROCESSING…',
        speaking: 'SPEAKING…',
        error: 'DISCONNECTED',
    };
    statusText.textContent = labels[state] || 'SYSTEM ONLINE';
    statusDot.classList.toggle('active', state !== 'error');
    statusText.classList.toggle('active', state !== 'error');

    arcReactor.classList.remove('listening', 'thinking', 'speaking');
    if (['listening', 'thinking', 'speaking'].includes(state)) {
        arcReactor.classList.add(state);
    }
}

// ─── WebSocket ──────────────────────────────────────────────────────────
function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws/chat`);

    ws.onopen = () => {
        setStatus('online');
        console.log('[WS] connected');
        // Tell server voice is enabled
        ws.send(JSON.stringify({ type: 'voice_toggle', enabled: voiceEnabled }));
    };

    ws.onclose = () => {
        setStatus('error');
        console.log('[WS] disconnected — retrying in 3s');
        setTimeout(connectWS, 3000);
    };

    ws.onerror = (e) => console.error('[WS] error', e);

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);

        switch (msg.type) {
            // Server detected voice input
            case 'voice_input':
                addMessage('user', msg.text);
                break;

            // Server status updates (listening/thinking)
            case 'status':
                setStatus(msg.state);
                if (msg.state === 'listening') {
                    micLabel.textContent = 'LISTENING…';
                }
                break;

            case 'stream_start':
                isStreaming = true;
                setStatus('thinking');
                currentStreamEl = addMessage('jarvis', '', true);
                break;

            case 'token':
                if (currentStreamEl) appendToken(currentStreamEl, msg.text);
                break;

            case 'stream_end':
                isStreaming = false;
                if (currentStreamEl) {
                    finishStream(currentStreamEl);
                    currentStreamEl = null;
                }
                break;

            case 'tool_result':
                addMessage('tool', msg.text);
                break;

            case 'audio':
                queueAudio(msg.data);
                break;
        }
    };
}

// ─── Messages ───────────────────────────────────────────────────────────
function clearWelcome() {
    const w = chatMessages.querySelector('.welcome');
    if (w) w.remove();
}

function addMessage(role, text, streaming = false) {
    clearWelcome();
    const div = document.createElement('div');
    div.classList.add('message', role);

    const label = document.createElement('span');
    label.classList.add('label');
    label.textContent = role === 'user' ? 'You' : role === 'tool' ? 'Tool' : 'Jarvis';
    div.appendChild(label);

    const content = document.createElement('span');
    content.classList.add('msg-content');
    content.textContent = text;
    div.appendChild(content);

    if (streaming) {
        const cursor = document.createElement('span');
        cursor.classList.add('cursor-blink');
        div.appendChild(cursor);
    }

    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return content;
}

function appendToken(el, token) {
    el.textContent += token;
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function finishStream(el) {
    const cursor = el.parentElement.querySelector('.cursor-blink');
    if (cursor) cursor.remove();
}

// ─── Audio Playback ─────────────────────────────────────────────────────
function queueAudio(b64) {
    audioQueue.push(b64);
    if (!isPlayingAudio) playNextAudio();
}

function playNextAudio() {
    if (audioQueue.length === 0) {
        isPlayingAudio = false;
        setStatus('online');
        return;
    }
    isPlayingAudio = true;
    setStatus('speaking');

    const b64 = audioQueue.shift();
    const audio = new Audio('data:audio/wav;base64,' + b64);
    audio.onended = () => playNextAudio();
    audio.onerror = () => playNextAudio();
    audio.play().catch(() => playNextAudio());
}

// ─── Send Text ──────────────────────────────────────────────────────────
function sendText() {
    const text = msgInput.value.trim();
    if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
    addMessage('user', text);
    ws.send(JSON.stringify({ text }));
    msgInput.value = '';
}

sendBtn.addEventListener('click', sendText);
msgInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendText();
    }
});

// ─── Init ───────────────────────────────────────────────────────────────
connectWS();
