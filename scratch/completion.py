import json
import requests
import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("SURF_API_KEY")
headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
willma_base_url = os.getenv("SURF_API_BASE")

response = requests.post(
    f"{willma_base_url}/chat/completions",
    data=json.dumps({
        "model": "Qwen 2.5 Coder 32B Instruct AWQ",
        "messages": [{"role": "user", "content": "Hello! What can you do?"}],
    }),
    headers=headers
).json()

print(response["choices"][0]["message"]["content"])