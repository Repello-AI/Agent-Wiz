import asyncio
import json
from dataclasses import dataclass
from typing import List, Dict, Any

from autogen_core import (
    AgentId, FunctionCall, MessageContext, RoutedAgent, 
    SingleThreadedAgentRuntime, message_handler
)
from autogen_core.models import (
    ChatCompletionClient, LLMMessage, SystemMessage,
    UserMessage, AssistantMessage, FunctionExecutionResult, FunctionExecutionResultMessage
)
from autogen_core.tools import FunctionTool, Tool
from autogen_ext.models.openai import OpenAIChatCompletionClient

import logging

logging.basicConfig(level=logging.WARNING)
logging.getLogger("autogen_core").setLevel(logging.DEBUG)


@dataclass
class Message:
    content: str

class MathGreetAgent(RoutedAgent):
    """Agent that handles math calculations and greetings."""
    
    def __init__(self, model_client: ChatCompletionClient, tool_schema: List[Tool]):
        super().__init__("Math and greeting assistant")
        self._system_messages: List[LLMMessage] = [
            SystemMessage(content="You are a helpful assistant that can perform math calculations and provide greetings.")
        ]
        self._model_client = model_client
        self._tools = tool_schema
    
    @message_handler
    async def handle_user_message(self, message: Message, ctx: MessageContext) -> Message:
        session = self._system_messages + [
            UserMessage(content=message.content, source="user")
        ]
        
        create_result = await self._model_client.create(
            messages=session,
            tools=self._tools,
            cancellation_token=ctx.cancellation_token
        )
        
        if isinstance(create_result.content, str):
            return Message(content=create_result.content)
        
        # Handle tool calls if any
        assert isinstance(create_result.content, list) and all(isinstance(call, FunctionCall) for call in create_result.content)
        
        session.append(AssistantMessage(content=create_result.content, source="assistant"))
        
        results = await asyncio.gather(*[
            self._execute_tool_call(call, ctx.cancellation_token) 
            for call in create_result.content
        ])
        
        session.append(FunctionExecutionResultMessage(content=results))
        
        final_result = await self._model_client.create(
            messages=session,
            cancellation_token=ctx.cancellation_token
        )
        
        assert isinstance(final_result.content, str)
        
        return Message(content=final_result.content)
    
    async def _execute_tool_call(self, call: FunctionCall, cancellation_token) -> FunctionExecutionResult:
        tool = next((tool for tool in self._tools if tool.name == call.name), None)
        assert tool is not None
        
        try:
            arguments = json.loads(call.arguments)
            result = await tool.run_json(arguments, cancellation_token)
            return FunctionExecutionResult(
                call_id=call.id,
                content=tool.return_value_as_string(result),
                is_error=False,
                name=tool.name
            )
        except Exception as e:
            return FunctionExecutionResult(
                call_id=call.id,
                content=str(e),
                is_error=True,
                name=tool.name
            )

# Define tool functions
async def calculator(a: float, b: float, op: str) -> Dict[str, Any]:
    """Performs basic arithmetic operations."""
    if op == "add":
        result = a + b
    elif op == "subtract":
        result = a - b
    elif op == "multiply":
        result = a * b
    elif op == "divide":
        result = a / b if b != 0 else float('inf')
    else:
        raise ValueError("Unsupported operation. Use 'add', 'subtract', 'multiply', or 'divide'.")
    
    return {
        "result": result
    }

async def greet(name: str) -> Dict[str, Any]:
    """Greets a person by name."""
    return {
        "greeting": f"Hello, {name}!"
    }

async def greet_and_calculate(name: str, a: float, b: float, op: str) -> Dict[str, Any]:
    """Greets a person and performs a calculation for them."""
    greeting_result = await greet(name)
    calc_result = await calculator(a, b, op)
    
    return {
        "message": f"{greeting_result['greeting']} The result of {op}ing {a} and {b} is {calc_result['result']}."
    }

async def main():
    runtime = SingleThreadedAgentRuntime()
    model_client = OpenAIChatCompletionClient(model="gpt-4o-mini")

    # Define all tools for the single agent
    tools = [
        FunctionTool(calculator, description="Performs basic arithmetic operations."),
        FunctionTool(greet, description="Greets a person by name."),
        FunctionTool(greet_and_calculate, description="Greets a person and performs a calculation for them.")
    ]

    # Register the single agent
    await MathGreetAgent.register(runtime, "math_greet_agent", lambda: MathGreetAgent(model_client, tools))

    runtime.start()

    agent_id = AgentId("math_greet_agent", "default")

    # Example query
    await runtime.send_message(
        Message(content="Please greet Bob and multiply 6 by 7."),
        agent_id,
    )

    await runtime.stop_when_idle()


# Run everything in a single main function
if __name__ == "__main__":
    asyncio.run(main())
