import os
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# STARTUP-01 — don't fire the lifespan preflight pass during tests.
# Every TestClient(app) invokes the lifespan, which would otherwise try
# to hit a real DB / Redis / Celery broker and dump noisy warning logs
# per test. Tests that exercise startup_checks directly import the
# functions rather than relying on the lifespan.
os.environ.setdefault("ORCHESTRATOR_SKIP_STARTUP_CHECKS", "true")
