import os
import datetime
import webbrowser
import subprocess


def open_app(name):
    apps = {
        "chrome": "open -a 'Google Chrome'",
        "safari": "open -a Safari",
        "vscode": "open -a 'Visual Studio Code'",
        "terminal": "open -a Terminal",
        "youtube": "open https://youtube.com",
    }
    cmd = apps.get(name.lower())
    if cmd:
        os.system(cmd)
        return f"Opened {name}"
    return "App not found"


def get_time():
    return datetime.datetime.now().strftime("%I:%M %p")


def search_google(q):
    webbrowser.open(f"https://www.google.com/search?q={q}")
    return f"Searching {q}"


def run_command(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True)[:500]
    except Exception as e:
        return str(e)