"""Example script that connects to the Willma/SURF API and lists text models."""

import requests
import os
from dotenv import load_dotenv

# Load SURF_API_KEY and SURF_API_BASE from a local .env file when present.
load_dotenv()

# The API key is sent with every request so the service can authenticate us.
api_key = os.getenv("SURF_API_KEY")
headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}

# Base URL for the Willma/SURF API, for example https://.../api.
willma_base_url = os.getenv("SURF_API_BASE")

# Fetch the available model sequences from the API.
models = requests.get(f"{willma_base_url}/sequences", headers=headers).json()
print("Available models:")

# This example only prints text generation models; other sequence types may exist.
for m in models:
    if m['sequence_type'] == 'text':
        print(f"  - {m['name']}: {m['latency_mode']}")
