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

            Delta-Neutral Strategy Analysis & Funding Rate Insights with USDT Lending Comparison and Position Sizing Adjustments

            Objective:
            Analyze the profitability, volatility, and sustainability of a delta-neutral strategy for INJ using Helix perpetual funding rates and Neptune lending rates, while comparing returns against USDT lending and incorporating position sizing math to ensure accurate funding cost calculations.

            AI Model Instructions:

            1. Strategy Summary
            Provide a concise explanation of the delta-neutral strategy and how it generates returns.
            Explain that this strategy captures the spread between Helix perpetual funding rates and Neptune borrowing rates, minimizing directional risk.
            Emphasize that USDT lending serves as a risk-free alternative, requiring comparison to determine if the delta-neutral approach provides superior risk-adjusted returns.
            Clarify that funding rates apply to the full position size while Neptune interest is only charged on borrowed funds (typically 1/3 of the total position when using 3x leverage).

            2. Funding Rate Analysis
            Query historical funding rate data from the Helix INJ perpetual market.
            Identify and report the following:
            Funding rate range over last 24 hours: [minimum, maximum]
            Funding rate range over last 7 days: [minimum, maximum]
            Funding rate range over last 30 days: [minimum, maximum]
            Funding rate volatility (measured by standard deviation of funding rates over time)
            Assess if funding rate direction has been stable or fluctuating, and report how often funding rate flips occur (i.e., how frequently the funding rate switches from positive to negative).
            Identify optimal funding rate windows for profitability and periods where returns underperform borrowing costs.

            3. USDT Lending Rate Comparison
            Retrieve Neptune’s historical USDT lending rates for:
            Current USDT Lending APY: [current rate]
            24-hour USDT Lending APY range: [minimum, maximum]
            7-day USDT Lending APY range: [minimum, maximum]
            30-day USDT Lending APY range: [minimum, maximum]
            USDT lending rate volatility: [stable, moderate, high]
            Determine whether the Helix funding rate APY consistently outperforms USDT lending APY.
            Flag the strategy as suboptimal if the funding rate APY is frequently lower than or near the USDT lending APY.

            4. Profitability Check with Position Sizing Adjustments
            Funding rate applies to the full position while Neptune interest applies only to borrowed funds (1/3 of total position when using 3x leverage).
            Position Sizing Calculations:
            Total Position Size (TPS) = Amount deployed in the strategy (assumed 3x leverage).
            Borrowed Funds (BF) = TPS / 3 (Since borrowing on Neptune is typically at 33% Loan-to-Value).
            Interest Paid on Neptune (IPN) = BF * Neptune Borrow Interest Rate
            Funding Rate Earnings (FRE) = TPS * Helix Funding Rate
            Profitability Formula:
            # TODO: add back formula here
            Comparison Metrics:
            Net APY for Long Neptune, Short Helix Strategy
            Net APY for Long Helix, Short Neptune Strategy
            USDT Lending APY for same period
            Profit spread over lending (Net APY - USDT Lending APY)
            Decision Rules:
            If Net APY is significantly higher than USDT Lending APY, the strategy is favorable.
            If Net APY is close to or lower than USDT Lending APY, simple lending is recommended.
            If funding rate APY fluctuates below breakeven, the strategy should be closed or hedged.
            Analyze these positions and current market conditions:

            5. Risk Management and Market Monitoring
            Assess funding rate fluctuations and report:
            If funding rate drops below Neptune’s USDT lending APY, recommend closing the strategy.
            If funding rate remains stable above lending APY, recommend continuing the position.
            Monitor funding rate trend direction and classify as:
            [Stable] - Funding rate remains in a consistent range.
            [Increasing] - Funding rate has been rising over the past X intervals.
            [Decreasing] - Funding rate has been declining over the past X intervals.
            Exit strategy triggers:
            If funding rate falls below the breakeven level, exit position.
            If funding rate flips for multiple consecutive funding periods, adjust strategy.

            6. Backtesting and Performance Analysis
            Run backtested performance of the strategy over:
            Last 24 hours
            Last 7 days
            Last 30 days
            Compare historical profitability of the delta-neutral strategy vs. USDT lending.
            Report profitability conditions:
            If simple USDT lending was consistently more profitable, classify the strategy as inefficient.
            If delta-neutral strategy had higher returns, report by how much.
            Provide actionable insights based on results:
            If funding rate fluctuations make profitability unpredictable, recommend switching to lending.
            If funding rates have historically remained above lending yields, recommend continuing the strategy.

            AI Model Output Structure
            Strategy Overview
            Delta-neutral goal: Capture the funding rate spread vs. Neptune interest rates.
            Lending alternative: USDT lending offers stable APY without risk.
            Funding Rate Insights
            Last 24h range: [minimum, maximum]
            Last 7-day range: [minimum, maximum]
            Funding rate trend: [stable, increasing, decreasing]
            Volatility rating: [low, medium, high]
            USDT Lending Rate Comparison
            Current USDT lending APY: [current APY]
            Historical range: [minimum, maximum]
            Profit spread over lending: [X%]
            Risk-adjusted return: [Strategy Yield - USDT Lending Yield = X%]
            Profitability and Risk Assessment
            Long Neptune, Short Helix APY: [X%]
            Long Helix, Short Neptune APY: [X%]
            USDT Lending APY: [X%]
            Position Sizing Adjusted Net APY: [X%]
            Is the strategy worth the risk? [Yes/No]
            Backtesting Results
            Last 24h delta-neutral returns: [X%]
            Last 7-day delta-neutral returns: [X%]
            USDT Lending APY over the same period: [X%]
            Did delta-neutral strategy outperform simple lending? [Yes/No]
            Actionable AI Advisory
            Recommended action: [Stay in position / Exit / Switch to USDT lending]
            Funding rate trend forecast: [Likely to stay high / Expected to drop]
            Exit trigger: If funding rate falls below breakeven, switch to lending.
            Additional AI Monitoring Features:
            Real-time alerts when funding rate drops below USDT lending APY.
            Automated alerts when funding rate flips direction.
            Backtested strategy profitability vs. lending to ensure better risk-adjusted returns.
            Trend forecasting to predict if funding rates will sustain profitability.
                        
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