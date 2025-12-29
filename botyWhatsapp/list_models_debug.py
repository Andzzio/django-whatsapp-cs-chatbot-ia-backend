import os
import google.generativeai as genai

# Configure API key
# Assuming it's in env, but since we are running as a script we might need to load it or rely on env var if set.
# The user env has GOOGLE_API_KEY or similar? Code uses settings.IA_TOKEN.
# I will try to read .env manually to be sure or just assume os.environ if it's there.
# Better to use the Django settings approach if I can, but a standalone script is safer to run quickly.

import sys

# Add project root to path to import settings if needed, or just parse .env
import environ
from pathlib import Path

BASE_DIR = Path("/home/andzzio/Documentos/proyectos/BOTY/BotyWhatsapp")
env = environ.Env()
environ.Env.read_env(os.path.join(BASE_DIR, ".env"))

api_key = env("GEMINI_API_KEY", default=None)
if not api_key:
    # Try alternate name from previous interactions
    api_key = env("IA_TOKEN", default=None)

if not api_key:
    print("Error: Could not find API key")
    sys.exit(1)

genai.configure(api_key=api_key)

print("Listing models...")
for m in genai.list_models():
    print(f"Name: {m.name}")
    print(f"Supported generation methods: {m.supported_generation_methods}")
    print("-" * 20)
