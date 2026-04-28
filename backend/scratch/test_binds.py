from sqlalchemy import text, bindparam
import re

sql = """
    SELECT id, 1 - (embedding <=> :query::vector) AS score
    FROM memory_records
    WHERE tenant_id = :tenant_id
      AND id IN :record_ids
"""
stmt = text(sql).bindparams(bindparam("record_ids", expanding=True))
print("Binds with ::vector:", stmt._bindparams.keys())

sql2 = """
    SELECT id, 1 - (embedding <=> CAST(:query AS vector)) AS score
    FROM memory_records
    WHERE tenant_id = :tenant_id
      AND id IN :record_ids
"""
stmt2 = text(sql2).bindparams(bindparam("record_ids", expanding=True))
print("Binds with CAST:", stmt2._bindparams.keys())
