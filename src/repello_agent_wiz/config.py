import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configuration constants
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")