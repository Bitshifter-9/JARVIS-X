import ollama
import sounddevice as sd
import numpy as np
import scipy.io.wavfile as wav
import subprocess
import os
from faster_whisper import WhisperModel
from memory import store_memory, recall_memory
from tools import open_app, get_time, search_google, run_command
from agent import execute_tool
from knowledge.rag import search_knowledge




MODEL_NAME = "llama3:latest"
VOICE_MODEL = "voices/en_US-lessac-medium.onnx"
SAMPLE_RATE = 16000
DURATION = 4 

whisper = None

def _get_whisper():
    global whisper
    if whisper is None:
        print("Loading Whisper Model")
        whisper = WhisperModel("base", compute_type="int8")
    return whisper

SYSTEM_PROMPT = """
You are Jarvis, a smart, calm, and helpful AI voice assistant.
Be concise, clear, and intelligent.
Speak naturally like a real assistant.

You also have access to tools and can act autonomously.

When a user's request requires an ACTION, respond ONLY in this exact format:
ACTION: <tool_name> | <argument>

Available tools:
- open_app (chrome, vscode, terminal)
- get_time (no argument)
- search_google (query)
- run_command (shell command)

If NO tool is required, respond normally like a helpful assistant.

Do NOT explain the action.
Do NOT say "I will open chrome".
Just output the ACTION line when needed.
"""

messages = [{"role": "system", "content": SYSTEM_PROMPT}]

def record_audio():
    print("\n Listening..")
    audio=sd.rec(int(SAMPLE_RATE*DURATION),samplerate=SAMPLE_RATE,channels=1,dtype=np.int16)
    sd.wait()
    wav.write("input.wav",SAMPLE_RATE,audio)
    return "input.wav"
def transcribe(audio_path):
    segments, _ = _get_whisper().transcribe(audio_path)
    text=""
    for j in segments:
        text+=j.text
    return text.strip()
def parse_action(text):
    if not text.startswith("ACTION:"):
        return None, None

    try:
        action = text.replace("ACTION:", "").strip()
        tool, arg = action.split("|", 1)
        return tool.strip(), arg.strip()
    except:
        return None, None

def try_tools(user_text):
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


def jarvis(text):
    memories=recall_memory(text)
    memory_context=""
    if memories:
        memory_context = "\nRelevant past memory:\n" + "\n".join(memories)
    knowledge = search_knowledge(user_text)

    knowledge_context = ""
    if knowledge:
        knowledge_context = "\nRelevant knowledge:\n" + knowledge
    messages.append({
    "role": "user",
    "content": text + knowledge_context
})

    # messages.append({"role":"user","content":text})

    stream=ollama.chat(
        model=MODEL_NAME,
        messages=messages,
        stream=True
    )
    reply=""
    buffer=""
    for chunk in stream:
        token = chunk["message"]["content"]
        print(token, end="", flush=True)
        reply+=token
        buffer+=token
        if token in [".", "!", "?", "\n"]:
            speak(buffer.strip())
            buffer = ""
    if buffer.strip():
        speak(buffer.strip())
    tool, arg = parse_action(reply)

    if tool:
        print(f"\n[Agent executing] {tool} -> {arg}")
        result = execute_tool(tool, arg)
        print("Tool result:", result)
        speak(result)
        return


    # response=ollama.chat(
    #     model=MODEL_NAME,
    #     messages=messages
    # )
    # reply= response["message"]["content"]
    messages.append({"role":"assistant","content":reply})
    store_memory("User: " + text)
    store_memory("Jarvis: " + reply)

    # return reply
def speak(text):
    try:
        subprocess.run([
            "python", "-m", "piper",
            "--model", VOICE_MODEL,
            "--output-file", "output.wav"
        ], input=text.encode(), check=True)

        if os.path.exists("output.wav"):
            subprocess.run(["afplay", "output.wav"])
    except Exception as e:
        print("TTS Error:", e)

print("\n Jarvis voice Assistant Read,press ctrl+c to stop")

if __name__ == "__main__":
    try:
        while True:
            audio_file=record_audio()
            user_text=transcribe(audio_file)
            if not user_text:
                continue
            print(f"\nYou said: {user_text}")
            tool_result = try_tools(user_text)

            if tool_result:
                print("Jarvis:", tool_result)
                speak(tool_result)
                continue
            jarvis(user_text)
       
    except KeyboardInterrupt:
        print("\nJarvis stopped.")

