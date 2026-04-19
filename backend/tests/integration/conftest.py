"""Postgres testcontainers fixture for backend integration tests.

Spins up a pgvector/pgvector:pg16 container once per test session, runs
``alembic upgrade head`` against it, and creates a dedicated non-superuser
role so RLS policies actually enforce isolation during tests (a superuser
bypasses every policy — same trap described in SETUP_GUIDE §5.2a).

Each test function receives a fresh ``Session`` that uses the non-superuser
role; cleanup happens via a module-level TRUNCATE between tests so state
doesn't leak across tests.

Requirements:
  * Docker running on the host (GitHub Actions ubuntu-latest has it).
  * The ``testcontainers[postgres]`` extra (in requirements-dev.txt).

Skip cleanly when Docker isn't available — these are integration tests
that we don't want blocking the pure-unit CI job.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


_TEST_APP_ROLE = "ae_test_app"
_TEST_APP_PASSWORD = "test-app-role-pw"


def _docker_available() -> bool:
    try:
        subprocess.run(
            ["docker", "info"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def pytest_collection_modifyitems(config, items):
    """Auto-skip every test under tests/integration/ when Docker is not
    available on the host — so integration tests don't blow up local dev
    or the pure-unit CI job, but still run in the dedicated integration
    job where Docker is guaranteed.
    """
    if _docker_available():
        return
    skip_marker = pytest.mark.skip(
        reason="Docker is not available; skipping integration tests."
    )
    for item in items:
        if "tests/integration" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip_marker)


@pytest.fixture(scope="session")
def pg_container():
    """Start a pgvector-enabled Postgres once per test session."""
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers[postgres] is not installed")

    # Pinned to pg16-v0.7.4: pgvector 0.8+ tightened the HNSW index
    # check to require a fixed-dimension vector column, but migrations
    # 0009 / 0010 / 0012 create dimension-less vector columns and then
    # build HNSW indexes on them. The floating ``pg16`` tag now pulls
    # 0.8+ and breaks alembic upgrade in CI. Pin until the schema is
    # fixed — tracked as S1-14 ("Declare pgvector dimensions and fix
    # HNSW indexes").
    # Uses the floating pg16 tag — matches docker-compose.yml. The bad
    # HNSW CREATE INDEX statements in migrations 0009 / 0010 / 0012 are
    # now wrapped in EXCEPTION blocks so alembic upgrade head succeeds
    # even on strict pgvector builds that reject dimension-less HNSW.
    # S1-14 is the forward fix (declare fixed dimensions + rebuild).
    with PostgresContainer(
        image="pgvector/pgvector:pg16",
        username="postgres",
        password="postgres",
        dbname="ae_orchestrator",
    ) as pg:
        yield pg


@pytest.fixture(scope="session")
def applied_schema(pg_container):
    """Run ``alembic upgrade head`` against the container and create the
    dedicated non-superuser app role RLS needs.

    Yields the connection URL of the non-superuser role; tests that need
    the superuser (for setup/teardown) go through ``superuser_engine``.
    """
    from sqlalchemy import create_engine, text

    superuser_url = pg_container.get_connection_url()
    # testcontainers emits a psycopg2 URL; alembic and our app use
    # psycopg2 dialect too, so no conversion needed.

    # Point alembic at this URL via the env var the Settings class reads.
    prior_url = os.environ.get("ORCHESTRATOR_DATABASE_URL")
    os.environ["ORCHESTRATOR_DATABASE_URL"] = superuser_url

    # Force reload of app.config.settings so alembic's env.py picks it up.
    import importlib
    import app.config
    importlib.reload(app.config)

    backend_root = Path(__file__).resolve().parents[2]
    subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=backend_root,
        check=True,
        env={**os.environ, "ORCHESTRATOR_DATABASE_URL": superuser_url},
    )

    # Provision the non-superuser app role.
    su_engine = create_engine(superuser_url)
    with su_engine.begin() as conn:
        conn.execute(text(f"DROP ROLE IF EXISTS {_TEST_APP_ROLE}"))
        conn.execute(
            text(f"CREATE ROLE {_TEST_APP_ROLE} WITH LOGIN PASSWORD :pw"),
            {"pw": _TEST_APP_PASSWORD},
        )
        conn.execute(text(f"GRANT CONNECT ON DATABASE ae_orchestrator TO {_TEST_APP_ROLE}"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {_TEST_APP_ROLE}"))
        conn.execute(text(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {_TEST_APP_ROLE}"
        ))
        conn.execute(text(
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_TEST_APP_ROLE}"
        ))
    su_engine.dispose()

    # Build the non-superuser URL.
    app_url = superuser_url.replace(
        "postgres:postgres@", f"{_TEST_APP_ROLE}:{_TEST_APP_PASSWORD}@"
    )

    yield {
        "superuser_url": superuser_url,
        "app_url": app_url,
    }

    # Restore env for subsequent unrelated test runs.
    if prior_url is not None:
        os.environ["ORCHESTRATOR_DATABASE_URL"] = prior_url
    else:
        os.environ.pop("ORCHESTRATOR_DATABASE_URL", None)


@pytest.fixture(scope="session")
def superuser_engine(applied_schema):
    from sqlalchemy import create_engine

    engine = create_engine(applied_schema["superuser_url"])
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_engine(applied_schema):
    """Engine authenticated as the non-superuser test app role.

    Queries through this engine actually honour RLS policies — running as
    the superuser would silently bypass them (the exact failure mode we
    are testing against).
    """
    from sqlalchemy import create_engine

    engine = create_engine(applied_schema["app_url"])
    yield engine
    engine.dispose()


@pytest.fixture
def app_session(app_engine, superuser_engine):
    """Function-scoped Session under the non-superuser role.

    Cleans the memory + workflow tables between tests via the superuser
    connection so RLS doesn't interfere with teardown.
    """
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=app_engine, autoflush=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        # Truncate tenant-scoped tables as superuser to keep tests isolated.
        with superuser_engine.begin() as conn:
            conn.execute(text(
                "TRUNCATE TABLE "
                "conversation_messages, conversation_episodes, conversation_sessions, "
                "memory_records, memory_profiles, entity_facts, "
                "workflow_snapshots, workflow_instances, workflow_definitions, "
                "a2a_api_keys, scheduled_triggers "
                "RESTART IDENTITY CASCADE"
            ))
