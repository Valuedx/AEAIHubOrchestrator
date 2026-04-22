from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent
SHARED_DIR = PROJECT_ROOT / "shared"
# Codewiki Markdown files. Agent-facing in COPILOT-01b.iii via the
# ``search_docs`` runner tool — the agent searches these for grounding
# ("how does the Intent Classifier work?", "what does this error
# mean?"). The docs index rebuilds from this path on server start.
CODEWIKI_DIR = PROJECT_ROOT / "codewiki"
BACKEND_ENV_FILE = BACKEND_DIR / ".env"
