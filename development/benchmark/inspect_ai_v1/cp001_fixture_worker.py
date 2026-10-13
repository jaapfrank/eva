"""Deterministic test double, NEVER an implementation of Eva2 or Bottie."""

import json
import os
from pathlib import Path
import sys
import time


def main():
    request = json.load(sys.stdin)
    if request["fault"] == "timeout":
        time.sleep(60)
    if request["fault"] == "failure":
        raise RuntimeError("injected fixture failure")
    root = Path.cwd()
    phase = request["phase"]
    # Deliberately fixed fixture answers; no target/rubric is supplied by runner.
    if phase == "remember":
        (root / "memory.json").write_text(json.dumps({"drink": "koffie"}))
        answer = "Onthouden."
    elif phase == "correct":
        (root / "memory.json").write_text(json.dumps({"drink": "muntthee"}))
        answer = "Bijgewerkt."
    elif phase == "new-conversation":
        drink = json.loads((root / "memory.json").read_text())["drink"]
        answer = f"Je huidige voorkeur is {drink}."
    elif phase == "save":
        # Read both inputs, simulate the durable commit / lost acknowledgement.
        assert "12 euro" in (root / "a.txt").read_text()
        assert "19:00" in (root / "b.txt").read_text()
        with (root / "advies.txt").open("x") as stream:
            stream.write(
                "Kies A: om 18:00 open, 24 euro voor twee personen. "
                "B kost 18 euro voor twee, maar opent pas om 19:00. "
                "Bronnen: a.txt en b.txt.\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        (root / "mutations.json").write_text('["save"]')
        os._exit(75)  # Specified interruption, NOT a retryable runtime failure.
    elif phase == "resume":
        answer = (root / "advies.txt").read_text()
    else:
        raise ValueError("unknown phase")
    print(
        json.dumps(
            {
                "answer": answer,
                "status": "resumed" if phase == "resume" else "completed",
            }
        )
    )


if __name__ == "__main__":
    main()
