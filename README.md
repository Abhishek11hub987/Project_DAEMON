
# 🤖 D.A.E.M.O.N.
**Data Analysis and Execution Monitor** - A highly personalized, offline-capable AI voice assistant

## 📋 Overview

D.A.E.M.O.N. is a sophisticated voice assistant designed to:
- 🎤 Listen for wake words and execute voice commands
- 🧠 Process requests using local LLMs (Ollama) or cloud APIs
- 💾 Analyze documents and execute system-level tasks
- 🔄 Work seamlessly across Windows and Ubuntu
- 🔐 Prioritize local execution and user privacy

## 🏗️ Architecture

```
project_DAEMON/
├── core_logic/      # Orchestration, routing, LLM integration, memory
├── audio/           # Wake word, STT, TTS, microphone, hotkeys
├── skills/          # Extensible voice commands (time, calc, system, etc.)
├── c_modules/       # Performance-critical compiled tasks
├── utils/           # Logging, helpers
├── tests/           # Unit and integration tests
├── web/             # FastAPI web UI with WebSocket
│   └── static/      # HTML/CSS/JS for browser interface
├── scripts/         # Autostart installers, service scripts
├── models/          # Wake word models, TTS voices
└── logs/            # Session history, conversation memory
```

## 🚀 Quick Start

### Prerequisites
- Python 3.9+
- Microphone and speakers
- Ollama (for local LLM) or API keys (Gemini, Groq, OpenAI)

### Installation

1. **Clone and setup virtual environment:**
   ```bash
   cd project_DAEMON
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On Linux/macOS:
   source venv/bin/activate
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment:**
   ```bash
   # Edit the existing .env file with your API keys and preferences
   # Or create from scratch:
   notepad .env   # Windows
   nano .env      # Linux/Mac
   ```

4. **Run D.A.E.M.O.N.:**
   ```bash
   # Web UI (Chrome auto-opens)
   python quickstart.py --web
   
   # Voice mode only (no browser)
   python quickstart.py
   
   # Text mode (type commands)
   python quickstart.py --text
   ```

## 📚 Documentation

- **Phase 1**: Environment & Foundation ✅
- **Phase 2**: Hearing and Speaking (Audio I/O) ✅
- **Phase 3**: The Brain (LLM Integration + Memory) ✅
- **Phase 4**: Custom Skills & Utilities ✅
- **Phase 5**: Polish and Background Execution ✅

See `ARCHITECTURE.md` for detailed technical documentation.

## 🎯 Key Features

### Audio Processing
- **Wake Word Detection**: OpenWakeWord (offline) or push-to-talk hotkey
- **Speech-to-Text**: OpenAI Whisper for accurate transcription
- **Text-to-Speech**: Piper TTS (fast, local) with multiple voices
- **Web UI**: Cyberpunk HUD in browser with session history, mute control

### Intelligence
- **Local LLMs**: Ollama support for Llama 3, DeepSeek
- **Cloud APIs**: Gemini, Groq, OpenAI fallback
- **System Prompt**: Customizable personality and behavior

### Cross-Platform
- **Windows & Ubuntu Support**: Automatic OS detection
- **System Integration**: Execute commands, monitor resources
- **Document Processing**: Extract and analyze PDFs

## ⚙️ Configuration

Edit `.env` to configure:
```
PORCUPINE_ACCESS_KEY=your_key
LLM_BACKEND=ollama
OLLAMA_MODEL=llama2
TTS_ENGINE=pyttsx3
DEBUG_MODE=False
```

See `.env` file in project root for all available options.

## 📖 Usage

```python
from core_logic.main import DAEMON

# Initialize
daemon = DAEMON()

# Start voice loop
daemon.start()

# Or start web UI
# python quickstart.py --web
```

## 🧪 Testing

```bash
pytest tests/
pytest --cov=. tests/  # With coverage
```

## 📝 License

MIT License - See LICENSE file for details

## 🤝 Contributing

Contributions welcome! Please follow:
1. Black formatting (`black .`)
2. Flake8 linting (`flake8 .`)
3. Type hints (mypy compatible)

## 📞 Support

- 📧 Issues: GitHub Issues
- 💬 Discussions: GitHub Discussions

---

**Project Status**: Beta (Phase 4 Complete - Web UI + Voice)  
**Last Updated**: 2026-05-30
