"""Configuration Management with environment variables"""
import os
from pathlib import Path
from dotenv import load_dotenv

ENV_PATH = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

class Config:
    """Central configuration for D.A.E.M.O.N."""
    
    # ====== PROJECT PATHS ======
    PROJECT_ROOT = Path(__file__).parent.parent
    AUDIO_DIR = PROJECT_ROOT / "audio"
    ASSETS_DIR = PROJECT_ROOT / "assets"
    LOGS_DIR = PROJECT_ROOT / "logs"
    CONFIG_DIR = PROJECT_ROOT / "config"
    
    # ====== AUDIO CONFIGURATION ======
    SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "16000"))
    CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1024"))
    CHANNELS = int(os.getenv("CHANNELS", "1"))
    # Optional: pin a specific input device by index (see tests/check_mic.py).
    # Leave empty / blank for system default.
    _aid = os.getenv("AUDIO_INPUT_DEVICE", "").strip()
    AUDIO_INPUT_DEVICE = int(_aid) if _aid.isdigit() else None

    # Barge-in (interrupt while D.A.E.M.O.N. is speaking) tuning:
    #   THRESHOLD is the RMS amplitude (0..32767) the mic must exceed.
    #     - Raise if D.A.E.M.O.N. interrupts itself from speaker leakage.
    #     - Lower if you have to shout to interrupt.
    #   SUSTAIN_MS is how long the loud audio must last (ignores short pops).
    BARGE_IN_THRESHOLD = float(os.getenv("BARGE_IN_THRESHOLD", "1500"))
    BARGE_IN_SUSTAIN_MS = int(os.getenv("BARGE_IN_SUSTAIN_MS", "250"))

    # Software-side microphone gain multiplier. 1.0 = unchanged. Bump to 2.0–4.0
    # if your mic input is faint (RMS while speaking < ~1500 in the wake logs).
    MIC_GAIN = float(os.getenv("MIC_GAIN", "1.0"))

    # ====== SPEECH-TO-TEXT ======
    # Whisper model size: tiny | base | small | medium | large
    # 'small' is the best speed/accuracy trade-off on CPU and handles
    # short keywords (like "Daemon") much better than 'base'.
    WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
    
    # ====== WAKE WORD DETECTION ======
    # Engine: "openwakeword" (free, offline, no signup) | "porcupine" (free key)
    WAKE_WORD_ENGINE = os.getenv("WAKE_WORD_ENGINE", "openwakeword")
    # openWakeWord pre-trained models (no signup):
    #   alexa | hey_mycroft | hey_rhasspy | hey_jarvis | timer | weather
    # Use "hey_mycroft" as a neutral default.
    WAKE_WORD_MODEL = os.getenv("WAKE_WORD_MODEL", "hey_mycroft")
    # Detection confidence threshold (0.0 - 1.0). Higher = fewer false positives.
    WAKE_WORD_THRESHOLD = float(os.getenv("WAKE_WORD_THRESHOLD", "0.5"))
    # Porcupine-only:
    PORCUPINE_ACCESS_KEY = os.getenv("PORCUPINE_ACCESS_KEY", "")
    WAKE_WORD = os.getenv("WAKE_WORD", "daemon")
    
    # ====== LLM BACKEND CONFIGURATION ======
    LLM_BACKEND = os.getenv("LLM_BACKEND", "ollama")
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama2")
    
    # ====== CLOUD LLM API KEYS & MODELS ======
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    
    # ====== TEXT-TO-SPEECH ======
    # TTS_ENGINE: "edge" (free neural, default) | "elevenlabs" (paid) | "pyttsx3" (offline)
    TTS_ENGINE = os.getenv("TTS_ENGINE", "edge")
    # Edge-TTS voice. British male = en-GB-RyanNeural / en-GB-ThomasNeural.
    # American male = en-US-GuyNeural / en-US-DavisNeural.
    TTS_VOICE = os.getenv("TTS_VOICE", "en-GB-RyanNeural")
    ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
    ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    
    # ====== CONVERSATION & MEMORY ======
    MAX_CONVERSATION_HISTORY = int(os.getenv("MAX_CONVERSATION_HISTORY", "20"))
    MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "4000"))
    
    # ====== SKILL CONFIGURATION ======
    ENABLE_DOCUMENT_SKILL = os.getenv("ENABLE_DOCUMENT_SKILL", "true").lower() == "true"
    ENABLE_SYSTEM_SKILL = os.getenv("ENABLE_SYSTEM_SKILL", "true").lower() == "true"
    ENABLE_FILE_SKILL = os.getenv("ENABLE_FILE_SKILL", "true").lower() == "true"
    ENABLE_C_INTEGRATION_SKILL = os.getenv("ENABLE_C_INTEGRATION_SKILL", "true").lower() == "true"
    C_COMPILATION_TIMEOUT = int(os.getenv("C_COMPILATION_TIMEOUT", "10"))
    MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))

    # ====== EMAIL CREDENTIALS (used by both EmailMonitor and MessagingSkill) ======
    EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
    EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "")
    
    # ====== LOGGING & DEBUG ======
    DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT = os.getenv("LOG_FORMAT", "text")
    
    # ====== NETWORK & PROXY ======
    HTTP_PROXY = os.getenv("HTTP_PROXY", "")
    HTTPS_PROXY = os.getenv("HTTPS_PROXY", "")
    REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
    
    # ====== SECURITY & PERMISSIONS ======
    ALLOW_EXTERNAL_FILE_ACCESS = os.getenv("ALLOW_EXTERNAL_FILE_ACCESS", "false").lower() == "true"
    ALLOWED_FILE_EXTENSIONS = os.getenv("ALLOWED_FILE_EXTENSIONS", ".pdf,.txt,.md,.docx").split(",")
    
    # ====== PERFORMANCE TUNING ======
    CACHE_C_BINARIES = os.getenv("CACHE_C_BINARIES", "true").lower() == "true"
    
    # ====== UI & OUTPUT ======
    USE_COLOR_OUTPUT = os.getenv("USE_COLOR_OUTPUT", "true").lower() == "true"
    VERBOSE_SKILL_OUTPUT = os.getenv("VERBOSE_SKILL_OUTPUT", "false").lower() == "true"
    
    # ====== ADVANCED OPTIONS ======
    ENABLE_EXPERIMENTAL = os.getenv("ENABLE_EXPERIMENTAL", "false").lower() == "true"
    LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))

    # ====== ORCHESTRATOR & WORKSPACE SANDBOX ======
    WORKSPACE_ROOT = Path(os.getenv("DAEMON_WORKSPACE", "").strip() or str(Path.home() / "daemon_workspace"))
    SANDBOX_COMMAND_TIMEOUT = int(os.getenv("SANDBOX_COMMAND_TIMEOUT", "30"))
    SANDBOX_MAX_READ_BYTES = int(os.getenv("SANDBOX_MAX_READ_BYTES", str(1024 * 1024)))        # 1 MB
    SANDBOX_MAX_WRITE_BYTES = int(os.getenv("SANDBOX_MAX_WRITE_BYTES", str(10 * 1024 * 1024)))  # 10 MB
    ORCHESTRATOR_MAX_TOTAL_ITERATIONS = int(os.getenv("ORCHESTRATOR_MAX_ITERATIONS", "15"))

    # ====== PHASE 6: MULTI-AGENT SYSTEM ======
    AGENTS_DIR = PROJECT_ROOT / "agents"
    BRIEFING_QUEUE_PATH = Path(os.getenv("BRIEFING_QUEUE_PATH", str(LOGS_DIR / "briefing_queue.json")))

    # Per-agent Piper TTS voice models (auto-downloaded from HuggingFace)
    NOVA_VOICE_MODEL = os.getenv("NOVA_VOICE_MODEL", "en_US-amy-medium")
    CIPHER_VOICE_MODEL = os.getenv("CIPHER_VOICE_MODEL", "en_GB-northern_english_male-medium")
    FORGE_VOICE_MODEL = os.getenv("FORGE_VOICE_MODEL", "en_US-ryan-high")

    # Agent-specific directories
    NOVA_DOCS_DIR = Path(os.getenv("NOVA_DOCS_DIR", str(PROJECT_ROOT / "documents")))
    FORGE_PRINT_DIR = Path(os.getenv("FORGE_PRINT_DIR", str(PROJECT_ROOT / "print_jobs")))

    # ====== PHASE 7: RAG (Retrieval-Augmented Generation) ======
    ENABLE_RAG = os.getenv("ENABLE_RAG", "true").lower() == "true"
    RAG_DB_PATH = Path(os.getenv("RAG_DB_PATH", str(LOGS_DIR / "chromadb")))
    RAG_CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "512"))
    RAG_CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "64"))
    RAG_TOP_K = int(os.getenv("RAG_TOP_K", "5"))
    RAG_DIRS = os.getenv("RAG_DIRS", "").strip()  # comma-separated extra dirs
    RAG_EMBEDDING_MODEL = os.getenv("RAG_EMBEDDING_MODEL", "all-MiniLM-L6-v2")

    # ====== PHASE 7: MESSAGING ======
    EMAIL_SMTP_SERVER = os.getenv("EMAIL_SMTP_SERVER", "smtp.gmail.com")
    EMAIL_SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", "587"))
    ENABLE_WHATSAPP = os.getenv("ENABLE_WHATSAPP", "false").lower() == "true"
    CONTACTS_PATH = Path(os.getenv("CONTACTS_PATH", str(CONFIG_DIR / "contacts.json")))

Config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
Config.WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
Config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
