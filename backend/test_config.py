import os
import sys
sys.path.append('d:/AEAIHubOrchestrator/backend')
from app.config import settings

print(f"settings.llm_default_provider: {settings.llm_default_provider}")
print(f"settings.copilot_default_provider: {settings.copilot_default_provider}")
print(f"settings.vertex_project: {settings.vertex_project}")
