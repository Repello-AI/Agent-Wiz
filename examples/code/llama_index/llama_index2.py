import asyncio
from llama_index.llms.openai import OpenAI
from llama_index.core.agent.workflow import ReActAgent, AgentStream, ToolCallResult
from llama_index.core.workflow import Context
from llama_index.core import PromptTemplate

def multiply(a: int, b: int) -> int:
    """Multiply two integers and returns the result integer"""
    return a * b


def add(a: int, b: int) -> int:
    """Add two integers and returns the result integer"""
    return a + b


# Define custom ReAct system prompt
react_system_header_str = """\

You are designed to help with a variety of tasks, from answering questions \
    to providing summaries to other types of analyses.

## Tools
You have access to a wide variety of tools. You are responsible for using
the tools in any sequence you deem appropriate to complete the task at hand.
This may require breaking the task into subtasks and using different tools
to complete each subtask.

You have access to the following tools:
{tool_desc}

## Output Format
To answer the question, please use the following format.

```
Thought: I need to use a tool to help me answer the question.
Action: tool name (one of {tool_names}) if using a tool.
Action Input: the input to the tool, in a JSON format representing the kwargs (e.g. {{"input": "hello world", "num_beams": 5}})
```

Please ALWAYS start with a Thought.

Please use a valid JSON format for the Action Input. Do NOT do this {{'input': 'hello world', 'num_beams': 5}}.

If this format is used, the user will respond in the following format:

```
Observation: tool response
```

You should keep repeating the above format until you have enough information
to answer the question without using any more tools. At that point, you MUST respond
in the one of the following two formats:

```
Thought: I can answer without using any more tools.
Answer: [your answer here]
```

```
Thought: I cannot answer the question with the provided tools.
Answer: Sorry, I cannot answer your query.
```

## Additional Rules
- The answer MUST contain a sequence of bullet points that explain how you arrived at the answer. This can include aspects of the previous conversation history.
- You MUST obey the function signature of each tool. Do NOT pass in no arguments if the function expects arguments.

## Current Conversation
Below is the current conversation consisting of interleaving human and assistant messages.

"""

async def main():
    # Initialize the OpenAI LLM
    llm = OpenAI(model="gpt-4o-mini")
    
    # Create a ReAct agent with multiply and add tools
    agent = ReActAgent(tools=[multiply, add], llm=llm)
    
    # Create a context to store the conversation history/session state
    ctx = Context(agent)
    
    # First example: Calculate 20+(2*4)
    print("\nExample 1: What is 20+(2*4)?")
    handler = agent.run("What is 20+(2*4)?", ctx=ctx)
    
    async for ev in handler.stream_events():
        # Uncomment to see tool calls and results
        # if isinstance(ev, ToolCallResult):
        #     print(f"\nCall {ev.tool_name} with {ev.tool_kwargs}\nReturned: {ev.tool_result}")
        if isinstance(ev, AgentStream):
            print(f"{ev.delta}", end="", flush=True)
    
    response = await handler
    
    # Display the prompts used by the agent
    print("\n\nAgent Prompts:")
    prompt_dict = agent.get_prompts()
    for k, v in prompt_dict.items():
        print(f"Prompt: {k}\n\nValue: {v.template}")
    
    # Create a custom system prompt template
    react_system_prompt = PromptTemplate(react_system_header_str)
    
    # Update the agent with the custom prompt
    agent.update_prompts({"react_header": react_system_prompt})
    
    # Second example: Calculate 5+3+2
    print("\nExample 2: What is 5+3+2")
    handler = agent.run("What is 5+3+2")
    
    async for ev in handler.stream_events():
        # Uncomment to see tool calls and results
        # if isinstance(ev, ToolCallResult):
        #     print(f"\nCall {ev.tool_name} with {ev.tool_kwargs}\nReturned: {ev.tool_result}")
        if isinstance(ev, AgentStream):
            print(f"{ev.delta}", end="", flush=True)
    
    response = await handler

# Run the async main function
if __name__ == "__main__":
    asyncio.run(main())
