#!/usr/bin/env python
import warnings
import os
from dotenv import load_dotenv

from echo_synth.flows.app_flow import EchoSynthFlow

# Load environment variables
load_dotenv()

# Ignore specific warnings
warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

def run():
    """Run the EchoSynth flow to process audio and send results"""    
    # Get audio file path from environment or use default
    audio_file_path = os.getenv("AUDIO_FILE_PATH")
    
    # Initialize and run the flow
    echo_flow = EchoSynthFlow(audio_file_path=audio_file_path)
    echo_flow.kickoff()
    


def plot():
    """Generate and display a visualization of the flow"""
    print("Generating EchoSynth Flow visualization...")
    echo_flow = EchoSynthFlow()
    echo_flow.plot()
    print("Flow visualization complete")


if __name__ == "__main__":
    run()
