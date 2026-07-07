# Demo

## Run

```bash
uv run --group demo streamlit run demo_app.py
```

or

```bash
make demo
```

## Deploy as a web server with Docker Compose

This repository includes a Docker Compose setup for running the Streamlit demo
behind Caddy with automatic HTTPS:

- `metadata-demo`: builds the app image from `Dockerfile` and runs Streamlit on
  port `8501` inside the Compose network.
- `caddy`: exposes ports `80` and `443`, gets and renews TLS certificates, and
  proxies browser traffic to the Streamlit container.

### 1. Prepare the server

Install Docker Engine and the Compose plugin on the server, then clone the
repository:

```bash
git clone <repository-url>
cd metadata_agent
```

The server must allow inbound traffic on ports `80` and `443`. Caddy uses port
`80` for the HTTP-to-HTTPS redirect and Let's Encrypt validation, then serves
the app over HTTPS on port `443`.

### 2. Create `.env`

Docker Compose loads `.env` for both Compose variables and application
configuration. Create it in the repository root:

```bash
LLM_PROVIDER=surf
SURF_API_KEY=your_surf_api_key
SURF_API_BASE=
LLM_MODEL=
DEFAULT_TOPOLOGY=single
SERVER_NAME=metadata.example.org
```

Provider choices:

```bash
# Google
LLM_PROVIDER=google
GOOGLE_API_KEY=your_google_api_key

# OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=your_openai_api_key

# SURF/OpenAI-compatible endpoint
LLM_PROVIDER=surf
SURF_API_KEY=your_surf_api_key
SURF_API_BASE=
```

`DEFAULT_TOPOLOGY=single` is a good web-demo default because it avoids the extra
multi-player debate calls used by heavier topologies.

Set `SERVER_NAME` to the public domain that resolves to your server. Caddy needs
a real hostname for public Let's Encrypt certificates.

### 3. Build and start the service

```bash
docker compose up -d --build
```

Open the app:

```text
https://metadata.example.org/
```

For local testing on the server:

```bash
curl -I https://metadata.example.org/
```

### 4. Watch logs

Use the repository helper:

```bash
make docker-logs
```

or plain Compose:

```bash
docker compose logs -f metadata-demo
docker compose logs -f caddy
```

The Streamlit container should show `streamlit run demo_app.py`. Caddy should
show certificate provisioning logs the first time it starts, then proxy requests
to `metadata-demo:8501`.

### 5. Update the deployment

Pull the latest code, rebuild the image, and restart:

```bash
git pull
docker compose up -d --build
```

To stop the service:

```bash
docker compose down
```

The Caddy certificate cache is stored in named Docker volumes. Use plain
`docker compose down` for normal restarts so certificates are preserved.

### 6. Troubleshooting

If the page does not load, check:

```bash
docker compose ps
docker compose logs metadata-demo
docker compose logs caddy
```

Common causes:

- Missing API key in `.env`: the app starts, but metadata generation fails when
  the selected provider is called.
- Port `80` or `443` already in use: stop the existing service or change the
  published ports in `docker-compose.yml`.
- `SERVER_NAME` is missing or is not a public DNS name: set it in `.env` to the
  domain that points at the server.
- Domain not routed to the server: test with:
  ```bash
  curl -H 'Host: metadata.example.org' http://SERVER_IP/
  ```
- Long metadata generation: keep `DEFAULT_TOPOLOGY=single` for the demo, then
  use `fast`, `default`, or `thorough` only when you explicitly want more LLM
  calls.

## Folder roles

### `demo_app.py`

Streamlit entry point that configures and launches the demo app.

### `demo/`

Everything related to the web demo.

```text
Streamlit pages
workflow wrappers
UI components
```

### `demo/workflows`
Main workflows used for the apps. 
```text
metadata_generation.py
```

### `demo/pages/`

One UI page per workflow.

```text
metadata_generation.py
```

Each page handles the UI for one workflow.
