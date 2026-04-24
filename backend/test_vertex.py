import os
import sys
sys.path.append('d:/AEAIHubOrchestrator/backend')
from app.config import settings

print(f"GOOGLE_APPLICATION_CREDENTIALS in env: {os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')}")
print(f"settings.google_application_credentials: {settings.google_application_credentials}")
print(f"settings.vertex_project: {settings.vertex_project}")
print(f"settings.google_api_key: {settings.google_api_key}")

from google import genai
try:
    client = genai.Client(
        vertexai=True,
        project=settings.vertex_project,
        location=settings.vertex_location,
    )
    print("Client initialized with vertexai=True")
    # Try a dummy call
    # response = client.models.generate_content(model='gemini-2.5-flash', contents='hi')
    # print("Call successful")
except Exception as e:
    print(f"Error initializing/calling client: {e}")

try:
    client_with_none = genai.Client(
        vertexai=True,
        project=settings.vertex_project,
        location=settings.vertex_location,
        api_key=None
    )
    print("Client initialized with vertexai=True and api_key=None")
except Exception as e:
    print(f"Error initializing client with api_key=None: {e}")
