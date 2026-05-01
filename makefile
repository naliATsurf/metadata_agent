SHELL := /bin/sh

UV := uv
VENV := .venv
PYTHON-VERSION := 3.12
PYTHON := $(VENV)/bin/python
RUFF := $(VENV)/bin/ruff

.PHONY: help uv-setup uv-clean activate install-docs docs docs-clean

help:
	@printf '%s\n' \
		'Available targets:' \
		'  make uv-setup   - create the uv virtual environment' \
		'  make activate   - print the command to activate the virtual environment' \
		'  make uv-clean   - clean the uv virtual environment and related files'


# Virtual environment setup and management
uv-setup:
	@if [ ! -f pyproject.toml ]; then \
		$(UV) init --python $(PYTHON_VERSION); \
	fi
	$(UV) python pin $(PYTHON_VERSION)
	$(UV) sync

activate: 
	@printf '%s\n' \
		'source $(VENV)/bin/activate'

uv-clean: 
	deactivate 2>/dev/null || true
	rm -rf $(VENV)
	rm -rf pyproject.toml
	rm -rf uv.lock


# Auto documentation
install-docs:
	$(UV) pip install -e .
	$(UV) sync --group docs

docs: install-docs
	$(UV) run sphinx-build -b html docs docs/_build/html
	open docs/_build/html/index.html

docs-clean:
	rm -rf docs/_build docs/generated
