import json
from semantic_kernel.functions import kernel_function
from .base import Agentable

class ChecklistManager(Agentable):
    def __init__(self):
        super().__init__(description="Manages a stateful checklist of tasks.")
        # tasks is a list of dicts: {'description': str, 'completed': bool}
        self.tasks = []

    @kernel_function(description="Initializes or overwrites the checklist with a list of tasks. Accepts a newline-separated string or a JSON array of strings.")
    def set_checklist(self, items: str) -> str:
        """
        Sets the checklist. 
        items: Can be a JSON string like '["Task 1", "Task 2"]' or a plain text block where each line is a task.
        """
        try:
            # Try parsing as JSON first
            parsed = json.loads(items)
            if isinstance(parsed, list):
                task_list = [str(x) for x in parsed]
            else:
                task_list = items.splitlines()
        except json.JSONDecodeError:
            # Fallback to splitting by newlines
            task_list = items.splitlines()

        # Clean up tasks (remove "1. ", "- ", empty lines)
        cleaned_tasks = []
        for t in task_list:
            t = t.strip()
            if not t: continue
            # Remove leading numbering if present (e.g. "1. Buy milk" -> "Buy milk")
            # Simple heuristic: if starts with digit and dot
            if len(t) > 2 and t[0].isdigit():
                parts = t.split('.', 1)
                if len(parts) > 1 and parts[0].isdigit():
                    t = parts[1].strip()
            
            cleaned_tasks.append(t)
        
        self.tasks = [{'description': t, 'completed': False} for t in cleaned_tasks]
        return f"Checklist initialized with {len(self.tasks)} tasks."

    @kernel_function(description="Marks a task as completed using its number (1-based index).")
    def mark_task_completed(self, task_number: int) -> str:
        try:
            idx = int(task_number) - 1
            if 0 <= idx < len(self.tasks):
                self.tasks[idx]['completed'] = True
                return f"Task {task_number} ('{self.tasks[idx]['description']}') marked as completed."
            return f"Error: Task number {task_number} is out of range."
        except ValueError:
             return f"Error: Invalid task number '{task_number}'."

    @kernel_function(description="Appends a new task to the end of the checklist.")
    def append_new_task(self, task_description: str) -> str:
        self.tasks.append({'description': task_description, 'completed': False})
        return f"Task added: {task_description}"

    @kernel_function(description="Returns a list of tasks that are NOT yet completed.")
    def get_remaining_tasks(self) -> str:
        remaining = []
        for i, task in enumerate(self.tasks):
            if not task['completed']:
                remaining.append(f"{i + 1}. {task['description']}")
        
        if not remaining:
            return "No remaining tasks."
        return "Remaining Tasks:\n" + "\n".join(remaining)

    @kernel_function(description="Returns the full checklist with status indicators.")
    def read_full_checklist(self) -> str:
        if not self.tasks:
            return "Checklist is empty."
        
        output = []
        for i, task in enumerate(self.tasks):
            status = "[x]" if task['completed'] else "[ ]"
            output.append(f"{status} {i + 1}. {task['description']}")
        return "\n".join(output)