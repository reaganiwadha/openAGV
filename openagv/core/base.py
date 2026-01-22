from semantic_kernel.functions import kernel_function

# Alias agent_action to kernel_function for direct SK compatibility
agent_action = kernel_function

class Agentable:
    """Base class for things that an agent can do actions upon."""
    def __init__(self, description: str):
        self.description = description
