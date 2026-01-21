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

    def advance_step(self, name: str, description: str = ""):
        """
        Completes the current step (if any) and starts a new step.
        This is for agentic workflows where steps are determined dynamically.
        """
        # Complete previous step if it exists and is running
        if self.current_step and self.current_step.status == "IN_PROGRESS":
            self.complete_current_step()
            
        # Create and add new step
        new_step = Step(name, description)
        new_step.status = "IN_PROGRESS"
        self.steps.append(new_step)
        self.current_step_index = len(self.steps) - 1
        
        self.log(f"Advancing to step: {name}", "INFO")
        self.set_state(StepperState.EXECUTING)

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