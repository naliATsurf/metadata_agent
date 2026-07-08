SHELL := /bin/sh

UV := uv
VENV := .venv
PYTHON-VERSION := 3.11
PYTHON := $(VENV)/bin/python
RUFF := $(VENV)/bin/ruff

.PHONY: help uv-setup uv-clean activate docs-install docs docs-rebuild docs-update docs-clean install-demo demo docker-install-demo docker-run-demo docker-build docker-up docker-down docker-logs lint compile test ci

help:
	@printf '%s\n' \
		'Available targets:' \
		'  make uv-setup   - create the uv virtual environment and install only basic dependencies' \
		'  make activate   - print the command to activate the virtual environment' \
		'  make uv-clean   - clean the uv virtual environment and uv lock, keep pyproject.toml' \
		'  make docs-install - install basic dependencies plus those under the docs group' \
		'  make docs       - incrementally build the documentation and open it in the browser' \
		'  make docs-rebuild - rebuild all documentation from a fresh Sphinx environment' \
		'  make docs-update - watch docs and incrementally rebuild on changes' \
		'  make docs-clean - clean the generated documentation files' \
		'  make demo       - install demo dependencies and launch Streamlit locally' \
		'  make docker-install-demo - install locked demo dependencies for Docker' \
		'  make docker-run-demo - launch Streamlit for Docker' \
		'  make docker-build - build the Docker image' \
		'  make docker-up   - build and start the Docker Compose service' \
		'  make docker-down - stop the Docker Compose service' \
		'  make docker-logs - follow Docker Compose service logs' \
		'  make lint       - run ruff linter on the codebase' \
		'  make compile    - compile the source code to check for syntax errors' \
		'  make test       - run unit tests' \
		'  make ci         - run lint, compile, and test for continuous integration'


# Virtual environment setup and management
uv-setup:
	@if [ ! -f pyproject.toml ]; then \
		$(UV) init --python $(PYTHON-VERSION); \
	fi
	$(UV) python pin $(PYTHON-VERSION)
	$(UV) sync --no-default-groups 	# install dependencies without dev dependencies

activate: 
	@printf '%s\n' \
		'source $(VENV)/bin/activate'

uv-clean: 
	deactivate 2>/dev/null || true
	rm -rf $(VENV)
	rm -rf uv.lock


# Auto documentation
docs-install:
	$(UV) pip install -e .
	$(UV) sync --no-default-groups --group docs  # install dependencies for docs group

docs:
	$(UV) run sphinx-build -b html docs docs/_build/html
	open docs/_build/html/index.html

docs-rebuild:
	$(UV) run sphinx-build -E -a -b html docs docs/_build/html
	open docs/_build/html/index.html

docs-update:
	$(UV) run sphinx-autobuild docs docs/_build/html

docs-clean:
	rm -rf docs/_build docs/generated


demo-install:
	$(UV) sync --no-default-groups --group demo  # install dependencies for demo group
	
demo: demo-install
	$(UV) run --group demo streamlit run demo_app.py

docker-install-demo:
	$(UV) sync --locked --no-default-groups --group demo

docker-run-demo:
	$(UV) run --no-sync --group demo streamlit run demo_app.py --server.address=0.0.0.0 --server.port=8501

docker-build:
	docker compose build

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f metadata-demo caddy

lint:
	$(UV) run ruff check .

compile:
	$(UV) run python -m compileall src demo tests demo_app.py

test:
	$(UV) run python -m unittest discover -s tests -p 'test*.py'

ci-install:
	$(UV) sync --locked --no-default-groups

ci: lint compile test

tui:
	$(UV) pip install -e .
	metadata-agent --tui

tracking-install: 
	$(UV) sync --no-default-groups --group tracking
