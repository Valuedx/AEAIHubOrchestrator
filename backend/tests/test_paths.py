from app.engine.config_validator import _load_registry
from app.paths import BACKEND_DIR, BACKEND_ENV_FILE, PROJECT_ROOT, SHARED_DIR


def test_backend_paths_stay_within_portable_subtree():
    assert BACKEND_DIR.name == "backend"
    assert PROJECT_ROOT == BACKEND_DIR.parent
    assert SHARED_DIR.name == "shared"
    assert (SHARED_DIR / "node_registry.json").exists()


def test_env_file_path_points_to_backend_dotenv():
    assert BACKEND_ENV_FILE.parent == BACKEND_DIR
    assert BACKEND_ENV_FILE.name == ".env"


def test_config_validator_loads_shared_registry():
    registry = _load_registry()
    assert isinstance(registry, dict)
    assert "node_types" in registry
    assert registry["node_types"]
