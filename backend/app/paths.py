from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent
SHARED_DIR = PROJECT_ROOT / "shared"
BACKEND_ENV_FILE = BACKEND_DIR / ".env"
