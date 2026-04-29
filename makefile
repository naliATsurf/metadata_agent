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
		'  make uv-setup   - create the uv virtual environment' \
		'  make activate   - print the command to activate the virtual environment' \
		'  make uv-clean   - clean the uv virtual environment and related files'

uv-setup:
	@if [ ! -f pyproject.toml ]; then \
		uv init --python $(PYTHON_VERSION); \
	fi
	uv python pin $(PYTHON_VERSION)
	uv sync

activate: 
	@printf '%s\n' \
		'source $(VENV)/bin/activate'

uv-clean: 
	deactivate 2>/dev/null || true
	rm -rf $(VENV)
	rm -rf pyproject.toml
	rm -rf uv.lock


