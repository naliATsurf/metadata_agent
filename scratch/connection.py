import requests
import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("SURF_API_KEY")
headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
willma_base_url = os.getenv("SURF_API_BASE")

# List available models
models = requests.get(f"{willma_base_url}/sequences", headers=headers).json()
print("Available models:")
for m in models:
    if m['sequence_type'] == 'text':
        print(f"  - {m['name']}: {m['description']}")