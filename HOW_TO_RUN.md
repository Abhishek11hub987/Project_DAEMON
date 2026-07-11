# How to Run D.A.E.M.O.N.

## 1. One-time setup

```powershell
# Windows PowerShell, from the project root
python -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

```bash
# Linux / Ubuntu
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

> **Picovoice / Porcupine**: get a free `AccessKey` from
> <https://console.picovoice.co/> and put it in `.env` as `PORCUPINE_ACCESS_KEY`.
> Without it, wake-word detection is disabled and the loop falls back to a
> manual "Press Enter to record" trigger.
>
> **LLM backend**: pick one in `.env` via `LLM_BACKEND` (`ollama` |
> `groq` | `gemini` | `openai`). Set the matching API key. Model names are
> overridable: `GROQ_MODEL`, `GEMINI_MODEL`, `OPENAI_MODEL`, `OLLAMA_MODEL`.

## 2. Configure `.env`

Minimum keys for the default Groq backend:

```ini
LLM_BACKEND=groq
GROQ_API_KEY=gsk_xxx
GROQ_MODEL=llama-3.1-8b-instant     # current Groq model
PORCUPINE_ACCESS_KEY=               # optional, for wake-word
```

## 3. Smoke tests (no microphone required)

```powershell
# Config + skills
.\venv\Scripts\python.exe -c "from core_logic.config import Config; print(Config.LLM_BACKEND)"

# LLM round-trip
.\venv\Scripts\python.exe -c "from core_logic.llm_engine import LLMEngine; print(LLMEngine().generate('Reply with PONG.', max_tokens=10))"

# Skill router
.\venv\Scripts\python.exe -c "from core_logic.skill_router import SkillRouter; r=SkillRouter(); print(r.execute_command('what time is it'))"

# C integration (requires gcc or clang on PATH)
.\venv\Scripts\python.exe -c "from skills.c_integration_skill import CIntegrationSkill as C; print(C.handle('run sysinfo'))"
```

## 4. Run the full voice loop

```powershell
.\venv\Scripts\python.exe quickstart.py            # default: voice mode
.\venv\Scripts\python.exe quickstart.py --text     # type instead of speak
.\venv\Scripts\python.exe quickstart.py --tray     # voice + system tray icon
.\venv\Scripts\python.exe quickstart.py --web      # voice + browser UI (Chrome auto-open)
.\venv\Scripts\python.exe quickstart.py --web --no-voice   # browser UI only (no mic)
.\venv\Scripts\python.exe quickstart.py --web --port 8080  # custom port
.\venv\Scripts\python.exe quickstart.py --no-hotkeys       # disable global hotkeys
```

`quickstart.py` performs a config / backend / component check and then enters
the wake-word -> listen -> transcribe -> route -> speak loop. Press
`Ctrl+C` to stop.

### Modes

| Mode | Flag | Description |
| --- | --- | --- |
| Voice | *(default)* | Wake word "Daemon", VAD listening, TTS replies. |
| Text  | `--text` / `--chat` | Type at the `you ▸` prompt. `/clear`, `/status`, `/quit` work. Skills + LLM (with conversation context) still work — useful for debugging or quiet hours. |
| Tray  | `--tray` | Same as voice, but also runs a coloured-dot tray icon (grey idle / green listening / yellow thinking / blue speaking / red error) with a right-click menu (Mute, Clear memory, Quit). Requires `pystray` + `pillow`. |

### Global hotkeys (voice / tray modes)

These work even when D.A.E.M.O.N. doesn't have focus:

| Hotkey | Action |
| --- | --- |
| `Ctrl+Alt+Space` | **Push-to-talk** — skip the wake word and start recording immediately. Also barges in if D.A.E.M.O.N. is mid-sentence. |
| `Ctrl+Alt+M` | **Mute** — stop the current TTS utterance immediately. |

Override the bindings in `.env`:

```ini
HOTKEY_PUSH_TO_TALK=ctrl+alt+space
HOTKEY_MUTE=ctrl+alt+m
ENABLE_HOTKEYS=true
```

> Hotkeys require the optional `keyboard` package. If the package isn't
> installed (or the OS blocks the hooks), they're silently skipped — the
> daemon still runs normally.

CLI sub-commands available on `core_logic/main.py`:

```powershell
.\venv\Scripts\python.exe -m core_logic.main status     # dump system status as JSON
.\venv\Scripts\python.exe -m core_logic.main devices    # list audio devices
.\venv\Scripts\python.exe -m core_logic.main test       # speak a test phrase
```

## 5. Run as a background service

```powershell
# Windows: silent autostart at login (uses pythonw.exe, logs to logs\daemon.log)
.\venv\Scripts\python.exe scripts\install_autostart.py install
.\venv\Scripts\python.exe scripts\install_autostart.py status
.\venv\Scripts\python.exe scripts\install_autostart.py uninstall
```

```bash
# Linux: per-user systemd unit
./venv/bin/python scripts/install_autostart.py install
systemctl --user status daemon
journalctl --user -u daemon -f
./venv/bin/python scripts/install_autostart.py uninstall
```

## 6. Voice / text commands you can try

```
what time is it
calculate 12 * 7
show cpu usage
list files
read pdf <path>
list available programs
run sysinfo
run cpu_scheduler with rr 3
run file_sorter with . --limit 10
```

## Web UI Controls

When running with `--web`, the cyberpunk HUD provides:

| Button | Action |
| --- | --- |
| **Mute Mic** | Toggle microphone on/off. When muted, the mic indicator turns red and wake word detection pauses. |
| **End Conversation** | Immediately stop the current session, turn off the mic, and save session history. |
| **Text Input** | Type commands instead of speaking. Press Enter or click Send. |
| **Session Sidebar** | Browse past conversations. Click to view history. |

## 7. Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| `ModuleNotFoundError: groq` | `pip install groq` (or switch `LLM_BACKEND`) |
| `model_decommissioned` | update `GROQ_MODEL` in `.env` (default is current) |
| `sounddevice` errors on Windows | install Microsoft VC++ Redistributable; pick a real input device with `python -m core_logic.main devices` |
| Wake word never fires | `PORCUPINE_ACCESS_KEY` missing or quota exhausted |
| Repeated "Pipeline error" | the daemon now backs off exponentially up to 30 s and stops after 6 consecutive failures — check `logs/daemon.log` |
| Chrome font too large | Press `Ctrl+Shift+R` to hard-refresh, or check browser zoom level |
| Mic indicator always on | By design — mic opens in 5-second bursts for wake word. Indicator blinks between checks. |
