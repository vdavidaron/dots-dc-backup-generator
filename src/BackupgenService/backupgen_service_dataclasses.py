from dataclasses import dataclass
from typing import List

@dataclass
class RealTimeBackupOutput:
    backup_supplied_power : float | None = None
    available_max_power : float | None = None

