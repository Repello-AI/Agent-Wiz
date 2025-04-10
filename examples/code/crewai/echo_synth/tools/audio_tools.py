import os
import requests

from openai import OpenAI
from typing import Type, Optional
from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class WhisperTranscriptionInput(BaseModel):
    """Input schema for WhisperTranscriptionTool."""
    audio_file_path: str = Field(..., description="Path to the audio file to transcribe.")
    language: Optional[str] = Field(None, description="Optional language code (e.g., 'en', 'es'). If not provided, language will be auto-detected.")
    prompt: Optional[str] = Field(None, description="Optional prompt to guide the transcription for better accuracy.")

class WhisperTranscriptionTool(BaseTool):
    name: str = "whisper_transcription"
    description: str = (
        "Transcribes audio files to text using OpenAI's Whisper model. "
        "Accepts .mp3, .mp4, .mpeg, .mpga, .m4a, .wav, and .webm files. "
        "Maximum file size is 25MB. For best results, use a high-quality audio file."
    )
    args_schema: Type[BaseModel] = WhisperTranscriptionInput
    api_key: str = None
    
    def __init__(self, api_key: Optional[str] = None):
        super().__init__()
        self.api_key = os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key is required for WhisperTranscriptionTool")
    
    def _run(self, audio_file_path: str, language: Optional[str] = None, prompt: Optional[str] = None) -> str:
        """
        Transcribe audio using OpenAI's Whisper model.
        
        Args:
            audio_file_path: Path to the audio file to transcribe
            language: Optional language code
            prompt: Optional prompt to guide transcription
            
        Returns:
            Transcribed text from the audio file
        """
        if not os.path.exists(audio_file_path):
            return f"Error: Audio file not found at {audio_file_path}"
        
        try:
            client = OpenAI(api_key=self.api_key)
            
            with open(audio_file_path, "rb") as audio_file:
                # Set up optional parameters
                transcription_params = {
                    "file": audio_file,
                    "model": "whisper-1"  # Using the latest Whisper model
                }
                
                if language:
                    transcription_params["language"] = language
                    
                if prompt:
                    transcription_params["prompt"] = prompt
                
                # Perform transcription
                response = client.audio.transcriptions.create(**transcription_params)
                
                return response.text
                
        except Exception as e:
            return f"Transcription error: {str(e)}"


class DallEImageGenerationInput(BaseModel):
    """Input schema for DallEImageGenerationTool."""
    prompt: str = Field(..., description="Detailed description of the image you want to generate.")
    size: str = Field("1024x1024", description="Image size. Options: '1024x1024', '1792x1024', or '1024x1792'")
    quality: str = Field("standard", description="Image quality. Options: 'standard' or 'hd'")
    style: str = Field("vivid", description="Image style. Options: 'vivid' or 'natural'")
    save_path: Optional[str] = Field(None, description="Optional path to save the generated image.")

class DallEImageGenerationTool(BaseTool):
    name: str = "dalle_image_generation"
    description: str = (
        "Generates images based on text descriptions using OpenAI's DALL-E 3 model. "
        "Provides high-quality, detailed images that match the given prompt. "
        "Can generate images in different sizes and styles."
    )
    args_schema: Type[BaseModel] = DallEImageGenerationInput
    api_key: str = None
    
    def __init__(self, api_key: Optional[str] = None):
        super().__init__()
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key is required for DallEImageGenerationTool")
    
    def _run(self, prompt: str, size: str = "1024x1024", quality: str = "standard", 
             style: str = "vivid", save_path: Optional[str] = None) -> str:
        """
        Generate an image using OpenAI's DALL-E 3 model.
        
        Args:
            prompt: Text description of the desired image
            size: Image dimensions (1024x1024, 1792x1024, or 1024x1792)
            quality: Image quality (standard or hd)
            style: Image style (vivid or natural)
            save_path: Optional path to save the generated image
            
        Returns:
            URL of the generated image or path to saved image file
        """
        try:
            client = OpenAI(api_key=self.api_key)
            
            # Validate parameters
            valid_sizes = ["1024x1024", "1792x1024", "1024x1792"]
            if size not in valid_sizes:
                return f"Error: Invalid size. Choose from {valid_sizes}"
                
            valid_qualities = ["standard", "hd"]
            if quality not in valid_qualities:
                return f"Error: Invalid quality. Choose from {valid_qualities}"
                
            valid_styles = ["vivid", "natural"]
            if style not in valid_styles:
                return f"Error: Invalid style. Choose from {valid_styles}"
            
            # Generate image
            response = client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size=size,
                quality=quality,
                style=style,
                n=1
            )
            
            image_url = response.data[0].url
            
            # Save the image if a save path is provided
            if save_path:
                # Download image
                image_response = requests.get(image_url)
                if image_response.status_code == 200:
                    # Ensure directory exists
                    os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
                    
                    # Save image
                    with open(save_path, "wb") as f:
                        f.write(image_response.content)
                    return f"Image generated and saved to {save_path}"
                else:
                    return f"Generated image URL: {image_url} (Failed to download and save image)"
            
            return f"Generated image URL: {image_url}"
            
        except Exception as e:
            return f"Image generation error: {str(e)}"


class AudioAnalysisInput(BaseModel):
    """Input schema for AudioAnalysisTool."""
    audio_file_path: str = Field(..., description="Path to the audio file to analyze.")
    analysis_type: str = Field(..., description="Type of analysis to perform. Options: 'sentiment', 'speaker_count', 'background_noise'")

class AudioAnalysisTool(BaseTool):
    name: str = "audio_analysis"
    description: str = (
        "Analyzes audio files to extract information beyond just transcription. "
        "Can detect sentiment, count speakers, or analyze background noise levels."
    )
    args_schema: Type[BaseModel] = AudioAnalysisInput
    api_key: str = None
    
    def __init__(self, api_key: Optional[str] = None):
        super().__init__()
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key is required for AudioAnalysisTool")
    
    def _run(self, audio_file_path: str, analysis_type: str) -> str:
        """
        Analyze audio beyond transcription using AI.
        
        Args:
            audio_file_path: Path to the audio file to analyze
            analysis_type: Type of analysis to perform
            
        Returns:
            Analysis results based on the chosen analysis type
        """
        if not os.path.exists(audio_file_path):
            return f"Error: Audio file not found at {audio_file_path}"
        
        # First, transcribe the audio
        transcription_tool = WhisperTranscriptionTool(api_key=self.api_key)
        transcription = transcription_tool._run(audio_file_path)
        
        # Now perform the requested analysis using GPT
        try:
            client = OpenAI(api_key=self.api_key)
            
            if analysis_type == "sentiment":
                # Analyze sentiment in the transcribed text
                prompt = f"Analyze the sentiment in this transcribed audio. Identify the overall mood, emotional tone, and any significant emotional shifts. Transcription: {transcription}"
                
            elif analysis_type == "speaker_count":
                # Estimate number of unique speakers
                prompt = f"Based on this transcription, estimate how many different speakers were in the conversation. Provide evidence from the text. Transcription: {transcription}"
                
            elif analysis_type == "background_noise":
                # Suggest background noise characteristics
                prompt = f"Based on this transcription, identify any mentions or indications of background noises or audio quality issues. Transcription: {transcription}"
                
            else:
                return f"Error: Invalid analysis type '{analysis_type}'. Supported types: 'sentiment', 'speaker_count', 'background_noise'"
            
            # Get analysis from GPT
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are an audio analysis assistant that specializes in extracting insights from transcribed audio."},
                    {"role": "user", "content": prompt}
                ]
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            return f"Audio analysis error: {str(e)}"
