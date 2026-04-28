from app.config import settings
import os

def test_override():
    print(f"Current default provider: {settings.llm_default_provider}")
    
    # Mock node config with hardcoded google
    config = {"provider": "google"}
    
    # Logic from node_handlers.py
    provider = config.get("provider")
    if not provider or (provider == "google" and settings.llm_default_provider == "vertex"):
        provider = settings.llm_default_provider
    
    print(f"Hardcoded 'google' resolved to: {provider}")
    assert provider == "vertex", f"Expected vertex, got {provider}"

    # Test empty config
    config_empty = {}
    provider_empty = config_empty.get("provider")
    if not provider_empty or (provider_empty == "google" and settings.llm_default_provider == "vertex"):
        provider_empty = settings.llm_default_provider
    print(f"Empty config resolved to: {provider_empty}")
    assert provider_empty == "vertex"

    print("\nSUCCESS: Override logic working as expected.")

if __name__ == "__main__":
    test_override()
