from crewai import (Agent, Crew, Process, Task, LLM)
from crewai.project import CrewBase, agent, crew, task

from echo_synth.tools import (
    AudioAnalysisTool, 
    WhisperTranscriptionTool, 
    DallEImageGenerationTool
)

# If you want to run a snippet of code before or after the crew starts,
# you can use the @before_kickoff and @after_kickoff decorators
# https://docs.crewai.com/concepts/crews#example-crew-class-with-decorators

@CrewBase
class AudioProcessingCrew():
    """AudioProcessing crew"""

    # Learn more about YAML configuration files here:
    # Agents: https://docs.crewai.com/concepts/agents#yaml-configuration-recommended
    # Tasks: https://docs.crewai.com/concepts/tasks#yaml-configuration-recommended
    agents_config = 'configs/agents/audio_agents.yaml'
    tasks_config = 'configs/tasks/audio_tasks.yaml'

    # If you would like to add tools to your agents, you can learn more about it here:
    # https://docs.crewai.com/concepts/agents#agent-tools

    def __init__(self):
        """Initialize with optional API key"""
        self.setup_tools()
        
    def setup_tools(self):
        """Set up tools for the agents"""
        # Initialize our custom tools with the API key
        self.whisper_tool = WhisperTranscriptionTool()
        self.dalle_tool = DallEImageGenerationTool()
        self.audio_analysis_tool = AudioAnalysisTool()


    @agent
    def audio_to_text_transcriber(self) -> Agent:
        return Agent(
            config=self.agents_config['audio_to_text_transcriber'],
            tools=[self.whisper_tool, self.audio_analysis_tool],
            verbose=True
        )

    @agent
    def speech_writer(self) -> Agent:
        return Agent(
            llm = LLM(
                model="openai/gpt-4o",
                temperature=0.7,
            ),
            config=self.agents_config['speech_writer'],
            verbose=True
        )
    
    @agent
    def image_summary_writer(self) -> Agent:
        return Agent(
            config=self.agents_config['image_summary_writer'],
            verbose=True
        )
    
    @agent
    def summary_writer(self) -> Agent:
        return Agent(
            config=self.agents_config['summary_writer'],
            verbose=True
        )
    
    @agent
    def image_painter(self) -> Agent:
        return Agent(
            llm = LLM(
                model="openai/gpt-4o",
                temperature=0.7,
            ),
            config=self.agents_config['image_painter'],
            tools=[self.dalle_tool],
            verbose=True,
        )

    # To learn more about structured task outputs,
    # task dependencies, and task callbacks, check out the documentation:
    # https://docs.crewai.com/concepts/tasks#overview-of-a-task
    @task
    def convert_audio_to_text(self) -> Task:
        return Task(
            config=self.tasks_config['convert_audio_to_text'],
        )

    @task
    def write_speech(self) -> Task:
        return Task(
            config=self.tasks_config['write_speech'],
        )
    
    @task
    def write_image_summary(self) -> Task:
        return Task(
            config=self.tasks_config['write_image_summary'],
        )
    
    @task
    def write_summary(self) -> Task:
        return Task(
            config=self.tasks_config['write_summary'],
        )
    
    @task
    def create_image(self) -> Task:
        return Task(
            config=self.tasks_config['create_image'],
        )
    

    @crew
    def crew(self) -> Crew:
        """Creates the EchoSynth crew"""
        # To learn how to add knowledge sources to your crew, check out the documentation:
        # https://docs.crewai.com/concepts/knowledge#what-is-knowledge

        return Crew(
            agents=self.agents, # Automatically created by the @agent decorator
            tasks=self.tasks, # Automatically created by the @task decorator
            process=Process.sequential,
            verbose=True,
            # process=Process.hierarchical, # In case you wanna use that instead https://docs.crewai.com/how-to/Hierarchical/
        )
