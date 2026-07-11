#!/usr/bin/env python3
"""
D.A.E.M.O.N. Quick Start Script
Demonstrates running D.A.E.M.O.N. with configuration check
"""

import sys
import os
from pathlib import Path

# Add project to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def _open_in_chrome(url: str) -> None:
    """Try to open `url` in Google Chrome; fall back to the default browser.

    Uses a tiny delayed-open thread so the FastAPI server has time to bind
    before Chrome hits it.
    """
    import threading
    import time
    import webbrowser

    def _go() -> None:
        time.sleep(1.2)
        # On Windows, register the common Chrome paths so webbrowser.get('chrome')
        # actually finds it. Falls through to the default browser otherwise.
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]
        for path in candidates:
            expanded = os.path.expandvars(path)
            if os.path.exists(expanded):
                try:
                    webbrowser.register(
                        "chrome", None,
                        webbrowser.BackgroundBrowser(expanded),
                    )
                    webbrowser.get("chrome").open_new(url)
                    return
                except Exception:
                    pass
        # Fallback: the user's default browser.
        try:
            webbrowser.open_new(url)
        except Exception:
            pass

    threading.Thread(target=_go, daemon=True).start()

def main():
    # Argument parsing.
    #   --text / --chat / -t : pure text-chat mode in the terminal
    #   --tray               : voice mode with a system-tray status icon
    #   --web                : launch the browser UI (default port 7860)
    #   --no-voice           : with --web, don't start the mic pipeline
    #   --no-hotkeys         : disable global hotkeys
    #   --port N             : override the web port
    #   --no-open            : don't auto-open the browser
    args = [a for a in sys.argv[1:]]
    args_lower = [a.lower() for a in args]

    text_mode  = any(a in ("--text", "--chat", "-t") for a in args_lower)
    tray_mode  = "--tray" in args_lower
    web_mode   = "--web" in args_lower
    no_voice   = "--no-voice" in args_lower
    no_hotkeys = "--no-hotkeys" in args_lower
    no_open    = "--no-open" in args_lower

    web_port = 7860
    if "--port" in args_lower:
        try:
            web_port = int(args[args_lower.index("--port") + 1])
        except (ValueError, IndexError):
            pass

    print("\n" + "="*70)
    print("🤖 D.A.E.M.O.N. - Starting Up")
    print("="*70 + "\n")
    if text_mode:
        print("   (text-chat mode requested)\n")
    if web_mode:
        print(f"   (web UI mode — http://127.0.0.1:{web_port})\n")
    
    # Load config
    print("📋 Loading configuration...")
    try:
        from core_logic.config import Config
        print(f"   ✅ Config loaded")
        print(f"   📌 Backend: {Config.LLM_BACKEND.upper()}")
        print(f"   📌 Debug: {Config.DEBUG_MODE}")
    except Exception as e:
        print(f"   ❌ Config failed: {e}")
        return 1
    
    # Check backend
    print(f"\n🔗 Checking {Config.LLM_BACKEND.upper()} configuration...")
    
    if Config.LLM_BACKEND == "groq":
        if not Config.GROQ_API_KEY:
            print("   ❌ GROQ_API_KEY not set!")
            return 1
        print(f"   ✅ Groq API Key found")
    elif Config.LLM_BACKEND == "ollama":
        print(f"   📍 Ollama URL: {Config.OLLAMA_BASE_URL}")
        print(f"   ⏳ Make sure Ollama is running: ollama serve")
    
    # Initialize components
    print(f"\n🧠 Initializing D.A.E.M.O.N...")
    daemon = None
    try:
        from core_logic.main import DAEMON
        daemon = DAEMON()
        print(f"   ✅ D.A.E.M.O.N. initialized")
    except Exception as e:
        print(f"   ⚠️  Initialization warning: {e}")
        # Continue anyway but mark daemon as failed
        daemon = None
    
    # Show status
    print(f"\n📊 System Status:")
    if daemon:
        print(f"   Audio: {'✅ Available' if daemon.audio else '❌ Not available'}")
        print(f"   LLM: {'✅ Available' if daemon.llm else '❌ Not available'}")
        print(f"   Memory: {'✅ Available' if daemon.memory else '❌ Not available'}")
        print(f"   Skills: {'✅ Available' if daemon.skill_router else '❌ Not available'}")
    else:
        print(f"   ❌ D.A.E.M.O.N. initialization failed")
        print(f"   Check the error above and try again")
        return 1
    
    # Ready to start
    print("\n" + "="*70)
    print("✅ D.A.E.M.O.N. is ready to start!")
    print("="*70)
    print("\n🚀 Starting main loop (Press Ctrl+C to stop)\n")
    print("Try saying or typing:")
    print("   - 'hello'")
    print("   - 'what time is it'")
    print("   - 'calculate 2 + 2'")
    print("   - 'show CPU usage'")
    print("   - 'list files'")
    print("\n" + "="*70 + "\n")
    
    # Start daemon (text / voice / tray / web)
    try:
        if text_mode:
            daemon.start_text_mode()
        elif web_mode:
            try:
                from web.server import serve
            except Exception as e:
                print(f"\n❌ Web mode unavailable: {e}")
                print("   Install: pip install fastapi uvicorn")
                return 1
            url = f"http://127.0.0.1:{web_port}"
            if not no_open:
                _open_in_chrome(url)
            print(f"\n🌐 Open {url} in your browser. Ctrl+C to stop.\n")
            serve(
                daemon,
                host="0.0.0.0",
                port=web_port,
                start_voice=not no_voice,
                enable_hotkeys=not no_hotkeys,
            )
        elif tray_mode:
            try:
                from core_logic.tray import run_with_tray
            except Exception as e:
                print(f"\n❌ Tray mode unavailable: {e}")
                print("   Install: pip install pystray pillow")
                return 1
            run_with_tray(daemon, enable_hotkeys=not no_hotkeys)
        else:
            # Optional global hotkeys (push-to-talk + mute)
            if not no_hotkeys:
                try:
                    from audio.hotkeys import start_hotkey_listener
                    start_hotkey_listener(daemon)
                except Exception as e:
                    print(f"   ⚠️  Hotkeys disabled: {e}")
            daemon.start()
    except KeyboardInterrupt:
        print("\n\n🛑 D.A.E.M.O.N. stopped")
        return 0
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
