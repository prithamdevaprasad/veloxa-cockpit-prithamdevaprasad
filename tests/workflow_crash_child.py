"""TEST-ONLY child process: start a pack pipeline and die abruptly inside its projection step (after the admit step
has been checkpointed), leaving the DBOS workflow PENDING for the parent to recover. Environment comes from the
parent test. Usage: python workflow_crash_child.py <tenant_id> <idempotency_key>"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from veloxa import projector  # noqa: E402
from veloxa.workflows.catalogue import CATALOGUE  # noqa: E402
from veloxa.workflows.runner import DBOSActivityRunner  # noqa: E402


def crash(*_a, **_k):
    sys.stdout.flush()
    os._exit(3)                        # an abrupt process death: no cleanup, no exception handling


projector.build = crash
runner = DBOSActivityRunner(CATALOGUE)
runner.launch()
h = runner.start("veloxa.pack_pipeline.v1", {"tenant_id": sys.argv[1], "pack_id": "D1"}, sys.argv[2])
print(h.workflow_id, flush=True)
runner.result(h.workflow_id)           # never returns: the projection step kills the process
os._exit(0)
