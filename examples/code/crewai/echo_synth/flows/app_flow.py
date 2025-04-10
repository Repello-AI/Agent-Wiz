import os
import traceback
from typing import List
from pydantic import BaseModel

from crewai.flow.flow import Flow, listen, start
from crewai.tasks import TaskOutput

from echo_synth.crews import AudioProcessingCrew, JsonSavingCrew


class EchoSynthState(BaseModel):
    
    transcribed_text: str = ""
    image_file: str = ""
    speech_text: str = ""
    summary_text: str = ""
    summary_for_image: str = ""
    
    json_file_path: str = ""


class EchoSynthFlow(Flow[EchoSynthState]):

    def __init__(self, audio_file_path=None):
        """Initialize the flow with optional parameters"""
        super().__init__()
        self.audio_file_path = audio_file_path
    
    @start()
    def process_audio(self) -> None:
        """
        Process an audio file using the AudioProcessingCrew.
        
        Args:
            state: The current state of the flow
            audio_file_path: Path to the audio file to process
            openai_api_key: OpenAI API key for Whisper and DALL-E
            
        Returns:
            Updated state with transcription, speech, summary and image
        """
        try:
            print("=== STARTING PROCESS_AUDIO ===")
            
            audio_crew = AudioProcessingCrew().crew()
            
            # Set up context for the first task
            initial_context = {
                "audio_file_path": os.path.abspath(self.audio_file_path)
            }
                        
            # Run the crew with the initial context
            results = audio_crew.kickoff(inputs=initial_context).tasks_output
            
            # Extract task outputs by task ID
            self.state.transcribed_text = self._get_task_output_by_name(results, "convert_audio_to_text")
            self.state.speech_text = self._get_task_output_by_name(results, "write_speech")
            self.state.summary_text = self._get_task_output_by_name(results, "write_summary")
            self.state.summary_for_image = self._get_task_output_by_name(results, "write_image_summary")
            self.state.image_file = self._get_task_output_by_name(results, "create_image")
            
            print("=== AUDIO PROCESSING COMPLETE ===")
            
        except Exception as e:
            print(f"Error in process_audio: {e}")
            print(traceback.format_exc())
            raise


    @listen(process_audio)
    def save_output(self) -> None:
        """
        Save the state data to a JSON file using the JsonSavingCrew
        """
        try:
            print("=== STARTING JSON SAVING ===")
            
            json_crew = JsonSavingCrew().crew()
            
            # Convert state to dictionary for the crew and add the flow ID
            state_dict = self.state.model_dump()
            state_dict["id"] = self.state.id
            
            # Run the crew with the state data
            results = json_crew.kickoff(
                inputs={
                    "state_data": state_dict,
                    "file_id": self.state.id
                }
            ).tasks_output
            
            # Get the json file path from the results
            self.state.json_file_path = self._get_task_output_by_name(results, "save_to_json")
            
            print("=== JSON SAVING COMPLETE ===")
            
        except Exception as e:
            print(f"Error in save_output: {e}")
            print(traceback.format_exc())
            raise
    

    @listen(save_output)
    def show_results(self) -> None:
        # Print summary of results
        print("\n=== RESULTS ===\n")

        print(f"AUDIO FILE: {self.audio_file_path}")
        print("\n=========================================\n")
        print(f"TRANSCRIPTION: {self.state.transcribed_text}")
        print("\n=========================================\n")
        print(f"SPEECH OUTPUT: {self.state.speech_text}")
        print("\n=========================================\n")
        print(f"SUMMARY OUTPUT: {self.state.summary_text}")
        print("\n=========================================\n")
        print(f"IMAGE SUMMARY OUTPUT: {self.state.summary_for_image}")
        print("\n=========================================\n")
        print(f"IMAGE OUTPUT: {self.state.image_file}")
        print("\n=========================================\n")
        print(f"JSON OUTPUT: {self.state.json_file_path}")
        print("\n=========================================\n")


    def _get_task_output_by_name(self, tasks_output: List[TaskOutput], task_name):
        """
        Helper method to extract output from a specific task by ID
        
        Args:
            task_outputs: List of TaskOutput objects
            task_id: ID of the task to find
            
        Returns:
            The output of the task or empty string if not found
        """
        for task_output in tasks_output:
            if hasattr(task_output, 'name') and task_output.name == task_name:
                return task_output.raw
                
        print(f"No task output found with name: {task_name}")
        return ""
