SHELL := /bin/sh

UV := uv
VENV := .venv
PYTHON-VERSION := 3.12
PYTHON := $(VENV)/bin/python
RUFF := $(VENV)/bin/ruff

.PHONY: help setup sync lock run test lint format check clean

help:
	@printf '%s\n' \
		'Available targets:' \
		'  make first-setup   - create the uv virtual environment' \

uv-setup: 
	uv init --python $(PYTHON-VERSION)
	uv python pin $(PYTHON-VERSION)
	uv add -r requirements.txt
	uv sync

uv-clean: 
	deactivate 2>/dev/null || true
	rm -rf $(VENV)
	rm -rf pyproject.toml
	rm -rf uv.lock
