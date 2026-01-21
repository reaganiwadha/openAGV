import asyncio
from typing import List, Optional, Any, Union
from openagv import AssetBin, SKLoopExecutor, UserInstruction
from openagv.modules.vision import ORVisionAnalyzer
from semantic_kernel.connectors.ai.chat_completion_client_base import ChatCompletionClientBase
from semantic_kernel.contents.chat_message_content import ChatMessageContent
from semantic_kernel.contents.utils.author_role import AuthorRole

# Mock Chat Completion for testing without API keys
class MockChatCompletion(ChatCompletionClientBase):
    def __init__(self):
        super().__init__(service_id="default", ai_model_id="mock-model")

    async def get_chat_message_contents(self, chat_history, settings, kernel, arguments):
        # Simulate agentic behavior: calling advance_step then analyze_asset
        # This is simplified; real SK would handle tool calls.
        return [ChatMessageContent(role=AuthorRole.ASSISTANT, content="Done!")]

    async def get_streaming_chat_message_contents(self, chat_history, settings, kernel, arguments):
        yield [ChatMessageContent(role=AuthorRole.ASSISTANT, content="Streaming not supported in mock")]

    def get_prompt_execution_settings_class(self):
        from semantic_kernel.connectors.ai.open_ai import OpenAIPromptExecutionSettings
        return OpenAIPromptExecutionSettings

async def main():
    # Mock client for the Vision Analyzer
    class MockORClient:
        pass

    orclient = MockORClient()
    instruct = UserInstruction("Analyze the chop video")
    ab = AssetBin()
    ab.add("chop.mp4")
    vision_analyzer = ORVisionAnalyzer(orclient, model='test-model')
    
    # Use our mock completion service
    mock_chat = MockChatCompletion()

    ex = SKLoopExecutor(ab, instruct, chat_completion=mock_chat, uses=[vision_analyzer], debug=True)

    def log_callback(stepper):
        if stepper.logs:
            last_log = stepper.logs[-1]
            print(f"[{last_log.level}] {last_log.message}")

    ex.register_callback(log_callback)

    print("Starting real (mocked) execution...")
    await ex.start()

    print("\nFinal State:", ex.state)

if __name__ == "__main__":
    asyncio.run(main())
