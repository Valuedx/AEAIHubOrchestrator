"""Engine-layer exceptions that carry control-flow intent across the
node handler → dag_runner boundary.

Kept in a dedicated module so both the handlers (which raise) and
dag_runner (which catches) can import without creating a cycle via
``node_handlers``.
"""

from __future__ import annotations


class NodeSuspendedAsync(Exception):
    """Raised by a node handler after it has submitted work to an external
    async system and persisted an ``AsyncJob`` row.

    The DAG runner catches this in ``_execute_single_node`` and marks the
    instance ``status='suspended'`` with ``suspended_reason='async_external'``
    — distinct from HITL suspension, which happens before the handler
    runs. The workflow resumes later via either the ``poll_async_jobs``
    Beat task or the ``/async-jobs/{id}/complete`` webhook.

    ``async_job_id`` is the ID of the row the handler inserted into
    ``async_jobs`` — used by logs / spans only; the Beat task and webhook
    both query the table directly.
    """

    def __init__(self, async_job_id: str, system: str, external_job_id: str):
        super().__init__(
            f"Node suspended on {system} job {external_job_id} "
            f"(async_job={async_job_id})"
        )
        self.async_job_id = async_job_id
        self.system = system
        self.external_job_id = external_job_id
