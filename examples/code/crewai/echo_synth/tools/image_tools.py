import os
import requests

from openai import OpenAI
from typing import Type, Optional
from crewai.tools import BaseTool
from pydantic import BaseModel, Field


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

