from crewai_tools import FileWriterTool
from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task


@CrewBase
class JsonSavingCrew():

    agents_config = 'configs/agents/json_agents.yaml'
    tasks_config = 'configs/tasks/json_tasks.yaml'

    @agent
    def data_organizer(self):
        """Agent responsible for organizing and structuring the data for JSON export"""
        return Agent(
            config=self.agents_config['data_organizer'],
            tools=[FileWriterTool()],
            verbose=True
        )
    
    @task
    def save_to_json(self):
        """Save the organized data to a JSON file"""
        return Task(
            config=self.tasks_config['save_to_json'],
        )
    
    @crew
    def crew(self):
        """Create the JSON saving crew"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True
        )
