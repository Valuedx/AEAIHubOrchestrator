import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.api.memory import resolve_instance_memory
from app.models.memory import ConversationMessage, EntityFact, MemoryRecord
from app.models.workflow import ExecutionLog, WorkflowInstance


class _RecorderQuery:
    def __init__(self, rows):
        self._rows = list(rows)
        self.filter_calls: list[tuple[object, ...]] = []
        self.filter_by_calls: list[dict[str, object]] = []

    def filter_by(self, **kwargs):
        self.filter_by_calls.append(kwargs)
        return self

    def filter(self, *clauses):
        self.filter_calls.append(clauses)
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _FakeDB:
    def __init__(self, mapping):
        self.mapping = mapping
        self.queries: dict[object, _RecorderQuery] = {}

    def query(self, model):
        query = _RecorderQuery(self.mapping.get(model, []))
        self.queries[model] = query
        return query


def test_resolve_instance_memory_filters_memory_rows_by_tenant():
    instance_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    record_id = uuid.uuid4()
    fact_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    instance = SimpleNamespace(id=instance_id, tenant_id="tenant-a")
    log = SimpleNamespace(
        node_id="node_agent",
        node_type="LLM Agent",
        completed_at=None,
        started_at=None,
        output_json={
            "memory_debug": {
                "recent_turn_ids": [str(turn_id)],
                "memory_record_ids": [str(record_id)],
                "entity_fact_ids": [str(fact_id)],
            }
        },
    )
    turn = SimpleNamespace(id=turn_id, role="user", content="hello", message_at=None, turn_index=1)
    record = SimpleNamespace(
        id=record_id,
        tenant_id="tenant-a",
        scope="session",
        scope_key="sess-1",
        kind="episode",
        content="User: hello",
        metadata_json={},
        session_ref_id=None,
        workflow_def_id=None,
        entity_type=None,
        entity_key=None,
        source_instance_id=None,
        source_node_id=None,
        embedding_provider="openai",
        embedding_model="text-embedding-3-small",
        vector_store="pgvector",
        created_at=now,
    )
    fact = SimpleNamespace(
        id=fact_id,
        tenant_id="tenant-a",
        entity_type="customer",
        entity_key="123",
        fact_name="plan",
        fact_value="pro",
        confidence=1.0,
        valid_from=now,
        valid_to=None,
        superseded_by=None,
        session_ref_id=None,
        workflow_def_id=None,
        source_instance_id=None,
        source_node_id=None,
        metadata_json={},
        created_at=now,
    )

    db = _FakeDB(
        {
            WorkflowInstance: [instance],
            ExecutionLog: [log],
            ConversationMessage: [turn],
            MemoryRecord: [record],
            EntityFact: [fact],
        }
    )

    result = resolve_instance_memory(instance_id, tenant_id="tenant-a", db=db)

    assert len(result) == 1
    assert result[0].recent_turns[0]["id"] == str(turn_id)
    assert any("tenant_id" in str(clause) for clause in db.queries[ConversationMessage].filter_calls[0])
    assert any("tenant_id" in str(clause) for clause in db.queries[MemoryRecord].filter_calls[0])
    assert any("tenant_id" in str(clause) for clause in db.queries[EntityFact].filter_calls[0])
