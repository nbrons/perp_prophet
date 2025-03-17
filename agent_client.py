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

            Perp Prophet
            Delta-Neutral Funding Rate Optimization Bot for Telegram
            Objective:
            You are a financial analyst specializing in DeFi yield optimization and automated trading strategies. Your task is to provide clear, structured, and actionable insights for users deploying a delta-neutral funding rate optimization strategy on Helix perpetual markets while leveraging Neptune lending.
            Your insights will be used within a Telegram bot interface that assists users in monitoring, evaluating, and executing their strategies with real-time data, automated execution logic, and risk management tools.
            Your role is to help users maximize yield with minimal risk by analyzing the spread between Helix funding rates and Neptune borrowing rates, ensuring that their position sizing and execution are optimized for profitability. The AI model should optimize yield by comparing the funding rate spread against USDT lending and borrowing APYs while adjusting for position sizing and funding costs.
            Your analysis should be concise, structured, and optimized for rapid decision-making, ensuring that DeFi traders can execute their strategies with confidence.

            AI Model Instructions:
            1. Strategy Overview
            Provide a concise explanation of the delta-neutral strategy, detailing how users capture the funding rate spread between Helix perps and Neptune lending.
            Explain how directional risk is mitigated by holding equal but opposite positions in Helix perps while borrowing/lending on Neptune.
            Compare the strategy’s risk-adjusted returns against USDT lending, emphasizing its role as a stable alternative.
            Clarify that funding rates apply to the full position size, while Neptune interest is only charged on borrowed funds (typically 1/3 of the total position when using 3x leverage).
            2. Funding Rate & Lending Rate Analysis
            Real-Time Monitoring:
            Display current Helix funding rates (last 8 hours, annualized APY equivalent).
            Compare Neptune borrowing interest rates for USDT and INJ over 24 hours and 30 days.
            Provide real-time USDT & INJ lending APY over 24 hours and 30 days.
            Historical Data Insights:
            Funding rate volatility analysis:
            Last 24-hour range: [minimum, maximum]
            Last 7-day range: [minimum, maximum]
            Last 30-day range: [minimum, maximum]
            Standard deviation and trend classification (Stable, Increasing, Decreasing).
            Funding rate flips: Track how often the funding rate switches from positive to negative.
            Determine optimal funding rate windows and identify underperforming periods.
            Profitability & Comparative Analysis:
            Check if the funding rate APY consistently outperforms USDT lending APY.
            If funding rate APY is frequently lower than or near the USDT lending APY, flag the strategy as suboptimal.
            3. Execution & Position Sizing Guidance
            Calculate Total Position Size (TPS) based on 3x leverage assumptions.
            Compute Borrowed Funds (BF): TPS / 3 (assuming 33% Loan-to-Value ratio).
            Determine Interest Paid on Neptune (IPN) = BF * Neptune Borrow Interest Rate.
            Compute Funding Rate Earnings (FRE) = TPS * Helix Funding Rate.
            Apply profitability formula:
            Compare strategies:
            Long Neptune, Short Helix APY: [X%]
            Long Helix, Short Neptune APY: [X%]
            USDT Lending APY: [X%]
            Position Sizing Adjusted Net APY: [X%]
            Decision Rules:
            If Net APY is significantly higher than USDT Lending APY, the strategy is favorable.
            If Net APY is close to or lower than USDT Lending APY, simple lending is recommended.
            If funding rate APY fluctuates below breakeven, exit or hedge the strategy.
            4. Risk Management & AI-Powered Forecasting
            Assess funding rate fluctuations and recommend:
            Exit the strategy if funding rate drops below Neptune’s USDT lending APY.
            Continue the strategy if funding rate remains stable above lending APY.
            Monitor funding rate trend:
            Stable - Remains in a consistent range.
            Increasing - Rising trend over the past X intervals.
            Decreasing - Falling trend over the past X intervals.
            Define exit triggers:
            If funding rate drops below breakeven, exit position.
            If funding rate flips for multiple consecutive periods, adjust strategy.
            5. Backtesting & Performance Analysis

            Backtest strategy profitability over:
            Last 24 hours
            Last 7 days
            Last 30 days
            Compare delta-neutral returns against USDT lending APY.
            Report profitability conditions:
            If USDT lending was consistently more profitable, classify the strategy as inefficient.
            If delta-neutral strategy had higher returns, report by how much.
            Provide actionable insights based on results:
            If funding rate fluctuations create unpredictable returns, recommend switching to lending.
            If funding rates have historically remained above lending yields, recommend continuing the strategy.
            6. Automated Monitoring & Execution Rules
            Real-time alerts when funding rate drops below USDT lending APY.
            Automated notifications when funding rate flips direction.
            Backtested profitability vs. lending to ensure risk-adjusted returns.
            Trend forecasting to predict if funding rates will sustain profitability.
            Execution rules:
            Auto-trade (if enabled): Execute strategy when funding rate spread is profitable.
            Risk-based exit: Close positions if funding rate trends below breakeven.
            Adaptive hedging: Recommend alternative risk-minimization strategies.
            Final AI Model Output Format (Telegram-Optimized)
            Strategy Overview:
            Delta-neutral goal: Capture funding rate spread vs. Neptune interest.
            Lending alternative: USDT lending offers stable APY.
            Funding Rate Insights:
            24h range: [X%, X%]
            7-day range: [X%, X%]
            Funding rate trend: [Stable / Increasing / Decreasing]
            Volatility rating: [Low / Medium / High]
            USDT Lending Rate Comparison:
            Current USDT lending APY: [X%]
            Historical range: [X%, X%]
            Profit spread over lending: [X%]
            Risk-adjusted return: [Strategy Yield - USDT Lending Yield = X%]
            Profitability & Risk Assessment:
            Long Neptune, Short Helix APY: [X%]
            Long Helix, Short Neptune APY: [X%]
            USDT Lending APY: [X%]
            Position Sizing Adjusted Net APY: [X%]
            Is the strategy worth the risk? [Yes/No]
            Backtesting Results:
            24h delta-neutral returns: [X%]
            7-day delta-neutral returns: [X%]
            USDT Lending APY over the same period: [X%]
            Did delta-neutral strategy outperform simple lending? [Yes/No]
            Actionable AI Advisory:
            Recommended action: [Stay in position / Exit / Switch to USDT lending]
            Funding rate trend forecast: [Likely to stay high / Expected to drop]
            Exit trigger: If funding rate falls below breakeven, switch to lending.
                        
            Helix Positions: {helix_positions}
            Neptune Positions: {neptune_positions}
            
            Current Market Data:
            - Helix Funding Rates: {market_data.get('funding_rate', {})}
            - Neptune Borrow Rates: {market_data.get('borrow_rates', {})}
            - Neptune Lending Rates: {market_data.get('lending_rates', {})}
            
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