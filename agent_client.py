import requests
import logging
import os
from dotenv import load_dotenv
import yaml

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

class AgentClient:
    def __init__(self, base_url="http://localhost:5000"):
        self.base_url = base_url
        self.session_id = "default"
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.agents_config = self._load_agents_config()
    
    def _load_agents_config(self):
        """Load agents configuration from YAML file"""
        try:
            with open('agents_config.yaml', 'r') as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Error loading agents config: {str(e)}")
            return {}
    
    async def analyze_positions(self, helix_positions, neptune_positions, market_data):
        """Send a request to analyze positions using iAgent"""
        try:
            # Format prompt with position information
            prompt = f"""
            Analyze these positions and current market conditions:
            
            Helix Positions: {helix_positions}
            Neptune Positions: {neptune_positions}
            
            Current Market Data:
            - Helix Funding Rates: {market_data.get('helix_rates', {})}
            - Neptune Borrow Rates: {market_data.get('neptune_borrow', {})}
            - Neptune Lending Rates: {market_data.get('neptune_lend', {})}
            
            How do the market conditions look for these positions? Are there any risks or opportunities to be aware of?
            Provide a concise analysis focusing on funding rates, borrowing rates, and overall market conditions.
            """
            
            # Get agent key from config
            agent_id = "hello_main"
            agent_key = self.agents_config.get(agent_id, {}).get("private_key", "")
            
            logger.info(f"Using agent: {agent_id}")
            
            # Send request to the /chat endpoint using the required format
            response = requests.post(
                f"{self.base_url}/chat",
                json={
                    "message": prompt,
                    "session_id": self.session_id,
                    "agent_id": agent_id,
                    "agent_key": agent_key,
                    "environment": "mainnet"
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get('response', 'No analysis available')
            else:
                logger.error(f"Error from iAgent: {response.status_code} - {response.text}")
                return f"Error from iAgent: {response.status_code}"
                
        except Exception as e:
            logger.error(f"Error making iAgent request: {str(e)}")
            return f"Error communicating with iAgent: {str(e)}"
    
    async def clear_history(self):
        """Clear the chat history"""
        try:
            response = requests.post(
                f"{self.base_url}/clear",
                params={"session_id": self.session_id}
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Error clearing history: {str(e)}")
            return False 