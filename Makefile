# D.A.E.M.O.N. Makefile - Convenient shortcuts for development

.PHONY: help setup install activate run test clean lint

help:
	@echo "D.A.E.M.O.N. Development Commands"
	@echo "=================================="
	@echo ""
	@echo "Setup & Installation:"
	@echo "  make setup       - Run complete project setup (creates dirs, files, venv)"
	@echo "  make install     - Install Python dependencies (requires venv activated)"
	@echo "  make venv        - Create Python virtual environment"
	@echo ""
	@echo "Running:"
	@echo "  make run         - Start D.A.E.M.O.N."
	@echo ""
	@echo "Development:"
	@echo "  make test        - Run tests with pytest"
	@echo "  make lint        - Run code quality checks (flake8, mypy)"
	@echo "  make format      - Format code with black"
	@echo ""
	@echo "Maintenance:"
	@echo "  make clean       - Remove cache, logs, build artifacts"
	@echo "  make clean-venv  - Remove virtual environment"
	@echo "  make audio-debug - List available audio devices"
	@echo ""

# Setup
setup:
	python setup_daemon.py

venv:
	python -m venv venv
	@echo ""
	@echo "Virtual environment created!"
	@echo "Activate it with: venv\\Scripts\\activate (Windows) or source venv/bin/activate (Linux)"

install:
	pip install -r requirements.txt

# Running
run:
	python core_logic/main.py

# Testing
test:
	pytest tests/ -v

test-smoke:
	pytest tests/ -m smoke -v

test-e2e:
	pytest tests/ -m e2e -v

test-coverage:
	pytest tests/ --cov=. --cov-report=html

# Code Quality
lint:
	flake8 core_logic audio skills utils tests
	mypy core_logic audio skills utils

format:
	black core_logic audio skills utils tests

# Cleaning
clean:
	@echo "Cleaning cache, logs, and build artifacts..."
	find . -type d -name __pycache__ -exec rm -r {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name ".pytest_cache" -exec rm -r {} + 2>/dev/null || true
	find . -name ".coverage" -delete 2>/dev/null || true
	find . -name "htmlcov" -exec rm -r {} + 2>/dev/null || true
	rm -rf build dist *.egg-info
	@echo "✓ Clean complete"

clean-logs:
	@echo "Clearing logs..."
	rm -f logs/*.log
	@echo "✓ Logs cleared"

clean-venv:
	@echo "Removing virtual environment..."
	rm -rf venv
	@echo "✓ Virtual environment removed"

# Debugging
audio-debug:
	python -c "from audio.audio_config import print_audio_devices; print_audio_devices()"

config-debug:
	python -c "from core_logic.config import Config; import json; print(json.dumps({k: str(v) for k, v in vars(Config).items() if not k.startswith('_')}, indent=2))"

# Full setup (one command to rule them all)
init: setup venv install
	@echo ""
	@echo "✅ D.A.E.M.O.N. Project Initialized!"
	@echo ""
	@echo "Next steps:"
	@echo "  1. Activate virtual environment: venv\\Scripts\\activate"
	@echo "  2. Copy .env.example: copy config\\.env.example .env"
	@echo "  3. Edit .env with your configuration"
	@echo "  4. Run: make run"
