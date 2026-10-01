"""Eenmalig: zet de contracturen in data/employees.csv op 40 uur, Thijn op 16 uur.

Draai vanuit de projectmap:  python set_contract_hours.py
Sluit de app in de browser eerst, en open hem daarna opnieuw.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
import storage  # noqa: E402

HOURS = {"Thijn": 16}
DEFAULT = 40

team = storage.load_team()
team["contracturen"] = team["naam"].map(lambda n: HOURS.get(n, DEFAULT))
storage.save("employees", team)
print(team[["naam", "contracturen"]].to_string(index=False))
