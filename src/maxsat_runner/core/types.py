from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List

@dataclass
class Event:
    t_sec: float
    cost: int

@dataclass
class RunResult:
    solver_tag: str
    solver_cmd: str
    instance: str
    events: List[Event]
    final_cost: Optional[int]
    time_to_best_sec: Optional[float]
    optimum_found: bool
    exit_code: int
