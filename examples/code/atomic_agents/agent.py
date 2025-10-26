import instructor
import openai
from atomic_agents import AtomicAgent, AgentConfig, BasicChatInputSchema, BasicChatOutputSchema
from atomic_agents.context import ChatHistory
from atomic_agents.lib.tools.calculator_tool import CalculatorTool, CalculatorToolConfig
from atomic_agents.lib.tools.search.searx_tool import SearxNGSearchTool, SearxNGSearchToolConfig
from atomic_agents.lib.tools.youtube import YouTubeTranscriptTool, YouTubeTranscriptToolConfig  # Example additional tool

# Setup client
client = instructor.from_openai(openai.OpenAI())

# Initialize tools
calc_tool = CalculatorTool(CalculatorToolConfig())
search_tool = SearxNGSearchTool(SearxNGSearchToolConfig(base_url="http://localhost:8080", max_results=5))
yt_tool = YouTubeTranscriptTool(YouTubeTranscriptToolConfig())  # Assumes default config

# Create agent with multiple tools
history = ChatHistory()
agent = AtomicAgent[BasicChatInputSchema, BasicChatOutputSchema](
    AgentConfig(
        client=client,
        tools=[calc_tool, search_tool, yt_tool],
        history=history,
        system_prompt="You are a helpful assistant with access to a calculator, search, and YouTube transcript tools."
    )
)

# Run agent
result = agent.run("Calculate 15 * 3, search for who invented the calculator, and get transcript summary for https://www.youtube.com/watch?v=dQw4w9WgXcQ")
print(result.chat_message)