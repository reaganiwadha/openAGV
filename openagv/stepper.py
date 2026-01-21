from enum import Enum, auto
from typing import List, Callable, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

class StepperState(Enum):
    IDLE = auto()
    PLANNING = auto()
    EXECUTING = auto()
    WAITING_FOR_INPUT = auto()
    FINISHED = auto()
    ERROR = auto()
    STOPPED = auto()

@dataclass
class LogEntry:
    timestamp: datetime
    message: str
    level: str = "INFO"

@dataclass
class Step:
    name: str
    description: str
    status: str = "PENDING"  # PENDING, IN_PROGRESS, COMPLETED, FAILED

class Stepper:
    def __init__(self):
        self.steps: List[Step] = []
        self.current_step_index: int = -1
        self._state: StepperState = StepperState.IDLE
        self.logs: List[LogEntry] = []
        self._callbacks: List[Callable[['Stepper'], None]] = []
        self._is_active: bool = False

    @property
    def state(self) -> StepperState:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def current_step(self) -> Optional[Step]:
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    def register_callback(self, callback: Callable[['Stepper'], None]):
        """Register a callback to be notified of state changes."""
        self._callbacks.append(callback)

    def _notify_on_change(self):
        for cb in self._callbacks:
            cb(self)

    def log(self, message: str, level: str = "INFO"):
        """Add a log entry and notify callbacks."""
        entry = LogEntry(datetime.now(), message, level)
        self.logs.append(entry)
        self._notify_on_change()

    def set_state(self, state: StepperState):
        """Update the state and notify callbacks."""
        self._state = state
        self._is_active = state not in [StepperState.IDLE, StepperState.FINISHED, StepperState.ERROR, StepperState.STOPPED]
        self._notify_on_change()

    def add_step(self, name: str, description: str):
        """Define a new step in the process."""
        self.steps.append(Step(name, description))
        self._notify_on_change()

    def add_steps(self, steps_data: List[tuple[str, str]]):
        """Define multiple new steps. Input is a list of (name, description) tuples."""
        for name, desc in steps_data:
            self.steps.append(Step(name, desc))
        self._notify_on_change()

    def start_next_step(self):
        """Starts the next PENDING step in the list."""
        next_index = self.current_step_index + 1
        if next_index < len(self.steps):
            self.current_step_index = next_index
            self.steps[next_index].status = "IN_PROGRESS"
            self._notify_on_change()
        else:
            self.log("No more steps to start.", "WARNING")

    def complete_current_step(self):
        """Mark the current step as COMPLETED."""
        if self.current_step:
            self.current_step.status = "COMPLETED"
            self._notify_on_change()

    def fail_current_step(self, reason: str):
        """Mark the current step as FAILED."""
        if self.current_step:
            self.current_step.status = "FAILED"
            self.log(f"Step '{self.current_step.name}' failed: {reason}", "ERROR")
            self.set_state(StepperState.ERROR)
        else:
            self.log(f"Failure occurred without an active step: {reason}", "ERROR")
            self.set_state(StepperState.ERROR)

    def stop(self):
        """Stop the execution."""
        self.set_state(StepperState.STOPPED)