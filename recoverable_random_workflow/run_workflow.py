from __future__ import annotations
import json, os, random, tempfile
from pathlib import Path
from datetime import datetime, timezone

root = Path(__file__).resolve().parent
state_path = root / "state.json"

def write_atomic(value):
    fd, temporary = tempfile.mkstemp(dir=root, prefix=".state-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, state_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def load():
    if state_path.exists():
        return json.loads(state_path.read_text())
    return {"completed_steps": [], "values": [], "running_total": 0, "completed": False}

state = load()
for step in range(1, 6):
    if step in state["completed_steps"]:
        continue
    value = random.SystemRandom().randint(1, 100)
    state["values"].append({"step": step, "value": value})
    state["running_total"] += value
    state["completed_steps"].append(step)
    state["last_completed_at"] = datetime.now(timezone.utc).isoformat()
    write_atomic(state)
    print(f"Step {step}/5 complete: generated {value}; running total = {state['running_total']}")

state["completed"] = len(state["completed_steps"]) == 5
state["completed_at"] = datetime.now(timezone.utc).isoformat() if state["completed"] else None
write_atomic(state)
print("Workflow complete." if state["completed"] else "Workflow remains resumable.")
