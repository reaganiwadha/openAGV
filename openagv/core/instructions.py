class Instruction:
    """Base class for instructions."""
    def __init__(self, prompt: str):
        self.prompt = prompt

class SystemInstruction(Instruction):
    """Represents a system instruction."""
    pass

class UserInstruction(Instruction):
    """Represents a user instruction/prompt."""
    pass
