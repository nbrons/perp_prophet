from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    ContextTypes,
    filters
)
import urllib.request
import json
import signal
import aiohttp
from datetime import datetime, timedelta
import asyncio
import logging
from grpc import RpcError
from urllib.parse import quote
import os
from dotenv import load_dotenv
from pyinjective.core.network import Network
from pyinjective.async_client import AsyncClient
from eth_utils import remove_0x_prefix
from bech32 import bech32_decode, convertbits
import base64
import requests
from agent_client import AgentClient
from decimal import Decimal
from time import sleep
from pyinjective.constant import GAS_FEE_BUFFER_AMOUNT, GAS_PRICE
from pyinjective.transaction import Transaction
from pyinjective.wallet import PrivateKey
import uuid

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Set event loop policy
asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())

# Initialize network for positions
network = Network.mainnet()
client = AsyncClient(network=network)  # Add back for positions

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

WHITELIST_PAIRS = ["INJ/USDT PERP", "ETH/USDT PERP"]

HELX_DATA = os.getenv('HELIX_DATA_URL')
NEPTUNE_BORROW = os.getenv('NEPTUNE_BORROW_URL')
NEPTUNE_LEND = os.getenv('NEPTUNE_LEND_URL')

# Constants for contract addresses
NEPTUNE_MARKET_CONTRACT = "inj1nc7gjkf2mhp34a6gquhurg8qahnw5kxs5u3s4u"
HELIX_MARKET_CONTRACT = "inj1q8qk6c7n44gf4e6jlhpvpwujdz0qm5hc4vuwhs"
INJ_PERP_MARKET_ID = "0x9b9980167ecc3645ff1a5517886652d94a0825e54a77d2057cbbe3ebee015963"
NEPTUNE_QUERIER_ADDRESS = "inj1kfjff5f0xjy7gece36watkqtscpycv666tqq7t"  # Added querier contract address
NEPTUNE_INTEREST_MODEL_ADDRESS = "inj1ftech0pdjrjawltgejlmpx57cyhsz6frdx2dhq"  # Interest model contract



# Example market IDs - we'll fetch all positions dynamically
# INJ/USDT PERP Market ID
INJ_USDT_PERP_MARKET_ID = "0x9b9980167ecc3645ff1a5517886652d94a0825e54a77d2057cbbe3ebee015963" 

# iAgent configuration
IAGENT_URL = "http://localhost:5000"  # Default port for iAgent docker

# Add new constants for close commands
CLOSE_COMMANDS = {
    'a': '/close_a',
    'b': '/close_b',
    'lend': '/close_lending'
}

# Add new constants for Neptune and Helix integration
NEPTUNE_ORACLE_ADDRESS = "inj1u6cclz0qh5tep9m2qayry9k97dm46pnlqf8nre"
INJ_MARKET_ID = "0x9b9980167ecc3645ff1a5517886652d94a0825e54a77d2057cbbe3ebee015963"
FEE_RECIPIENT = "inj1xwfmk0rxf5nw2exvc42u2utgntuypx3k3gdl90"
MIN_NOTIONAL_SMALLEST_UNITS = 1000000  # 1,000,000 in USDT's smallest units
GAS_BUFFER = 40000  # Buffer for gas fee computation

def get_subaccount_id(address, subaccount_index=0):
    """Convert an Injective address to a subaccount ID"""
    hrp, data = bech32_decode(address)
    if not data:
        raise ValueError(f"Invalid Injective address: {address}")
    
    # Convert from bech32 to eth address format
    eth_address = "0x" + "".join(["{:02x}".format(d) for d in convertbits(data, 5, 8, False)])
    
    # Create subaccount ID by padding with zeros
    subaccount_id = eth_address.lower() + format(subaccount_index, '024x')
    return subaccount_id

# Initialize agent client
agent_client = AgentClient()

def get_server_url() -> str:
    """Get the server URL from file or environment"""
    try:
        with open('server_url.txt', 'r') as f:
            server_url = f.read().strip()
            logger.info(f"Using server URL: {server_url}")
            return server_url
    except FileNotFoundError:
        logger.error("server_url.txt not found. Make sure web_server.py is running")
        raise

def create_transaction_url(transactions: list, user_id: str) -> str:
    """Create a URL for executing a transaction in Keplr"""
    base_url = get_server_url()
    
    # Prepare transaction data
    data = {
        'transactions': transactions,
        'user_id': user_id
    }
    
    # URL encode the data
    encoded_data = quote(json.dumps(data))
    return f"{base_url}/transaction?data={encoded_data}"

def get_helix_rates():
    """Convert the list of opportunities to a dictionary with token as key"""
    data = urllib.request.urlopen(HELX_DATA).read().decode("utf-8").replace("'", '"')
    loaded_data = json.loads(data)
    rates = {}
    
    for pair in loaded_data:
        if pair["ticker_id"] in WHITELIST_PAIRS:
            token = pair["ticker_id"].split('/')[0]  # Extract token name (INJ or ETH)
            rates[token] = {
                'funding_rate': pair.get('funding_rate', 0),
                'ticker_id': pair['ticker_id'],
                'open_interest': pair['open_interest'],
            }
    return rates

def get_neptune_borrow_rates():
    try:
        # Fetch data from Neptune API
        response = urllib.request.urlopen(NEPTUNE_BORROW).read()
        data = json.loads(response)
        
        # Initialize dictionary for results
        rates = {}
        
        # Map of token identifiers we're interested in
        token_map = {
            "inj": "INJ",
            "peggy0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2": "ETH",
            "peggy0xdAC17F958D2ee523a2206206994597C13D831ec7": "USDT"
        }
        
        # Parse through the response
        for item in data:
            denom = item[0]['native_token']['denom']
            rate = float(item[1])
            
            if denom in token_map:
                token_name = token_map[denom]
                rates[token_name] = rate * 100  # Convert to percentage
        
        return rates
    except Exception as e:
        print(f"Error fetching Neptune borrow rates: {e}")
        return {}

def get_neptune_lend_rates():
    try:
        # Fetch data from Neptune API
        response = urllib.request.urlopen(NEPTUNE_LEND).read()
        data = json.loads(response)
        
        # Initialize dictionary for results
        rates = {}
        
        # Map of token identifiers we're interested in
        token_map = {
            "inj": "INJ",
            "peggy0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2": "ETH",
            "peggy0xdAC17F958D2ee523a2206206994597C13D831ec7": "USDT"
        }
        
        # Parse through the response
        for item in data:
            denom = item[0]['native_token']['denom']
            rate = float(item[1])
            
            if denom in token_map:
                token_name = token_map[denom]
                rates[token_name] = rate * 100  # Convert to percentage
        
        return rates
    except Exception as e:
        print(f"Error fetching Neptune lending rates: {e}")
        return {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user_id = update.effective_user.id

    message = "Welcome to Perp Prophet! I'm a Delta-Neutral Funding Rate Optimization Bot for Telegram.\n\n"

    """Show available opportunities"""
    try:
        # Get rates from both protocols
        helix_rates = get_helix_rates()
        neptune_borrow = get_neptune_borrow_rates()
        neptune_lend = get_neptune_lend_rates()
        
        # Constants
        avg_ltv = 0.5
        implied_helix_leverage = 1/avg_ltv
        amount = 1000  # Base amount for comparison
        HOURS_PER_YEAR = 24 * 365  # Convert hourly to annual
        
        # Delta Neutral Strategy calculations
        neptune_borrow_rate = neptune_borrow.get('USDT', 0) / 100  # Convert percentage to decimal
        collateral_interest_rate = neptune_lend.get('INJ', 0) / 100
        strategy_borrowed_value = amount * avg_ltv
        amt_paid_neptune_interest = amount * neptune_borrow_rate
        funding_rate = helix_rates.get('INJ', {}).get('funding_rate', 0) * HOURS_PER_YEAR
        amt_earned_helix_funding = strategy_borrowed_value * implied_helix_leverage * funding_rate
        amt_earned_collateral = amount * collateral_interest_rate
        profits = amt_earned_helix_funding - amt_paid_neptune_interest + amt_earned_collateral
        apy = (profits / amount) * 100

        message += (
            "Available Opportunity:\n\n"
            f"Delta Neutral Strategy:\n"
            f"• Expected APY: {apy:.2f}%\n"
            f"• Funding Rate (Annual): {funding_rate * 100:.4f}%\n"
            f"• Borrow Rate: {neptune_borrow_rate * 100:.2f}%\n"
            f"• Collateral Rate: {collateral_interest_rate * 100:.2f}%\n\n"
        )

    except Exception as e:
        error_msg = f"Error fetching opportunities: {str(e)}"
        logger.error(error_msg)
        if update.callback_query:
            await update.callback_query.edit_message_text(text=error_msg)
        else:
            await update.message.reply_text(error_msg)
    
    # Check if private key is configured
    if not os.getenv("INJECTIVE_PRIVATE_KEY"):
        message += (
            "\n⚠️ Private Key Not Configured\n"
            "Please add your private key to the .env file:\n"
            "INJECTIVE_PRIVATE_KEY=your_private_key_here\n"
            "(Remove the '0x' prefix if present)"
        )
        keyboard = [
            [InlineKeyboardButton("Explain Strategy", callback_data="explain_strategy")],
            [InlineKeyboardButton("Show Math", callback_data="show_math")]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("Execute Strategy", callback_data="execute_strategy")],
            [InlineKeyboardButton("View Positions", callback_data="view_positions")],
            [InlineKeyboardButton("Close Position", callback_data="close_position")],
            [InlineKeyboardButton("Explain Strategy", callback_data="explain_strategy")],
            [InlineKeyboardButton("Show Math", callback_data="show_math")]
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(message, reply_markup=reply_markup)
    else:
        await update.message.reply_text(message, reply_markup=reply_markup)


async def get_wallet_balances(wallet_address: str) -> dict:
    """Get token balances for a wallet"""
    try:
        network = Network.mainnet()
        client = AsyncClient(network=network)

        # Fetch balances from chain
        response = await client.fetch_bank_balances(wallet_address)
        logger.info(f"Raw bank balances response: {response}")
            
        # Map of denom to token symbol and decimals
        token_map = {
            'inj': {'symbol': 'INJ', 'decimals': 1e18},
            'peggy0xdAC17F958D2ee523a2206206994597C13D831ec7': {'symbol': 'USDT', 'decimals': 1e6},
                # Add other tokens as needed
        }
            
        balances = {}
        if 'balances' in response:
            for balance in response['balances']:
                denom = balance['denom']
                if denom in token_map:
                    token_info = token_map[denom]
                    amount = float(balance['amount']) / token_info['decimals']
                    balances[token_info['symbol']] = f"{amount:.6f}"
            
        return balances
    except Exception as e:
        logger.error(f"Error fetching wallet balances: {str(e)}")
        return {}

async def get_helix_positions(wallet_address: str) -> list:
    """Get all Helix positions for a wallet across all markets"""
    try:
        # Convert wallet address to subaccount ID (first subaccount)
        subaccount_id = get_subaccount_id(wallet_address, 0)
        
        # Set up network and client
        network = Network.mainnet()
        client = AsyncClient(network=network)
        
        # Fetch all positions for the subaccount without specifying market
        positions_response = await client.fetch_chain_subaccount_positions(
            subaccount_id=subaccount_id
        )
        
        formatted_positions = []
        
        if positions_response and 'state' in positions_response:
            for pos in positions_response['state']:
                # Skip if no position data
                if 'position' not in pos:
                    continue
                    
                # Get market data to identify the trading pair
                market_id = pos['marketId']
                try:
                    market_data = await client.fetch_derivative_market(market_id=market_id)
                    if 'market' in market_data and 'ticker' in market_data['market']:
                        market_symbol = market_data['market']['ticker']
                    else:
                        market_symbol = market_id[:10] + '...'  # Shortened version of market ID
                except Exception as e:
                    logger.warning(f"Failed to get market info for {market_id}: {str(e)}")
                    # If we can't get market data, use the market ID as fallback
                    market_symbol = market_id[:10] + '...'
                
                # Interpret position data
                position = pos['position']
                is_long = position.get('isLong', False)
                quantity = float(position.get('quantity', 0))
                position_type = "LONG" if is_long else "SHORT"
                
                formatted_pos = {
                    'market_id': market_symbol,
                    'type': position_type,
                    'entry_price': float(position.get('entryPrice', 0)) / 1e18,  # Adjust for decimals
                    'quantity': abs(quantity) / 1e18,  # Adjust for decimals
                    'margin': float(position.get('margin', 0)) / 1e18,  # Adjust for decimals
                    'funding': float(position.get('cumulativeFundingEntry', 0)) / 1e18  # Adjust for decimals
                }
                formatted_positions.append(formatted_pos)
        
        # Debug log
        logger.info(f"Found {len(formatted_positions)} Helix positions")
        
        return formatted_positions
    except Exception as e:
        logger.error(f"Error fetching Helix positions: {str(e)}")
        return []

async def get_neptune_positions(wallet_address: str) -> list:
    """Get Neptune lending positions"""
    try:
        # Neptune Market contract
        NEPTUNE_MARKET_CONTRACT = "inj1nc7gjkf2mhp34a6gquhurg8qahnw5kxs5u3s4u"
        
        network = Network.mainnet()
        client = AsyncClient(network=network)
        
        def decode_base64_data(data):
            if isinstance(data, dict):
                for key, value in data.items():
                    data[key] = decode_base64_data(value)
            elif isinstance(data, list):
                data = [decode_base64_data(item) for item in data]
            elif isinstance(data, str):
                try:
                    data = base64.b64decode(data).decode('utf-8')
                    # Try to parse JSON in case the decoded data is JSON
                    data = json.loads(data)
                except Exception:
                    pass
            return data
        
        # Create query string
        query_data = f'{{"get_user_accounts": {{"addr": "{wallet_address}"}}}}'
        
        # Fetch user positions
        response = await client.fetch_smart_contract_state(
            address=NEPTUNE_MARKET_CONTRACT,
            query_data=query_data
        )
        
        # Decode base64 response
        decoded_response = decode_base64_data(response)
        
        positions = []
        if decoded_response and 'data' in decoded_response:
            data = decoded_response['data']
            
            # Process each subaccount
            for subaccount in data:
                account_info = subaccount[1]
                
                # Check debt pool accounts
                for debt_pool in account_info.get('debt_pool_accounts', []):
                    asset_info = debt_pool[0]
                    pool_info = debt_pool[1]
                    
                    if 'native_token' in asset_info:
                        denom = asset_info['native_token']['denom']
                        if denom == 'inj':
                            positions.append({
                                'type': 'INJ Lending',
                                'amount': str(float(pool_info['principal']) / 1e18),  # INJ has 18 decimals
                                'shares': str(float(pool_info['shares']) / 1e18),
                                'token': 'INJ',
                                'rate': '8.25'  # Placeholder rate - could be dynamic in future
                            })
                        elif denom == 'peggy0xdAC17F958D2ee523a2206206994597C13D831ec7':
                            positions.append({
                                'type': 'USDT Lending',
                                'amount': str(float(pool_info['principal']) / 1e6),  # USDT has 6 decimals
                                'shares': str(float(pool_info['shares']) / 1e6),
                                'token': 'USDT',
                                'rate': '12.5'  # Placeholder rate - could be dynamic in future
                            })
        
        return positions
    except Exception as e:
        logger.error(f"Error fetching Neptune positions: {str(e)}")
        return []

async def get_position_info(client_tuple):
    """Get current position information for the Delta Neutral Strategy"""
    try:
        # Unpack client components
        client, composer, network, priv_key, pub_key, address = client_tuple
        
        # Get subaccount ID
        subaccount_id = get_subaccount_id(address.to_acc_bech32())
        
        # Query Neptune position
        query_data = f'{{"get_user_accounts": {{"addr": "{address.to_acc_bech32()}"}}}}'
        neptune_state = await query_market_state(client, NEPTUNE_MARKET_CONTRACT, query_data)
        
        # Query Helix position
        helix_position = await client.fetch_chain_subaccount_position_in_market(
            subaccount_id=subaccount_id,
            market_id=INJ_PERP_MARKET_ID
        )
        
        # Get current prices
        price_query = json.dumps({
            "get_prices": {
                "assets": [
                    {"native_token": {"denom": "inj"}},
                    {"native_token": {"denom": "peggy0xdAC17F958D2ee523a2206206994597C13D831ec7"}}
                ]
            }
        })
        prices_data = await query_prices(client, NEPTUNE_ORACLE_ADDRESS, price_query)
        inj_price, usdt_price = await extract_prices(prices_data)
        
        # Extract position details
        debt_amount = 0
        if neptune_state and 'debt' in neptune_state:
            for denom, info in neptune_state['debt'].items():
                if denom == 'inj':
                    debt_amount = float(info['principal']) / 1e18
        
        collateral_amount = 0
        if neptune_state and 'collateral' in neptune_state:
            for denom, info in neptune_state['collateral'].items():
                if 'peggy' in denom:  # USDT
                    collateral_amount = float(info['principal']) / 1e6
        
        # Get Helix position details
        short_size = 0
        entry_price = 0
        if helix_position and 'state' in helix_position:
            position_data = helix_position['state']
            if position_data:
                short_size = float(position_data.get('quantity', '0')) / 1e18
                entry_price = float(position_data.get('entryPrice', '0')) / 1e18
        
        # Calculate PnL
        pnl = (entry_price - inj_price) * short_size if short_size > 0 else 0
        
        # Calculate health factor (simplified)
        health_factor = (collateral_amount / (debt_amount * inj_price)) if debt_amount > 0 else float('inf')
        
        return {
            'debt': debt_amount,
            'collateral': collateral_amount,
            'short_size': short_size,
            'entry_price': entry_price,
            'current_price': inj_price,
            'pnl': pnl,
            'health_factor': health_factor
        }
    except Exception as e:
        logger.error(f"Error getting position info: {str(e)}")
        return None

async def query_prices(client, contract_address, query_data):
    """Query prices from Neptune Oracle"""
    try:
        response = await client.fetch_smart_contract_state(
            address=contract_address,
            query_data=query_data
        )
        return json.loads(base64.b64decode(response["data"]))
    except Exception as e:
        logger.error(f"Error querying prices: {str(e)}")
        return None

async def show_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current positions in the Delta Neutral Strategy."""
    # Check if private key is configured
    if not os.getenv("INJECTIVE_PRIVATE_KEY"):
        message = (
            "⚠️ Private Key Not Configured\n\n"
            "Please add your private key to the .env file:\n"
            "INJECTIVE_PRIVATE_KEY=your_private_key_here\n"
            "(Remove the '0x' prefix if present)"
        )
        await update.callback_query.edit_message_text(message)
        return

    try:
        # Initialize the client
        client = await setup_client()
        
        # Get user address and subaccount ID
        _, _, _, _, _, address = client
        user_address = address.to_acc_bech32()
        subaccount_id = address.get_subaccount_id(index=0)
        
        # Query Neptune user accounts
        user_query = f'{{"get_user_accounts": {{"addr": "{user_address}"}}}}'
        decoded_data = await query_contract_state(client[0], NEPTUNE_MARKET_CONTRACT, user_query)
        
        # Query prices
        price_query = '{"get_prices": {"assets": [{"native_token": {"denom": "inj"}}, {"native_token": {"denom": "peggy0xdAC17F958D2ee523a2206206994597C13D831ec7"}}]}}'
        prices_data = await query_contract_state(client[0], NEPTUNE_ORACLE_ADDRESS, price_query)
        
        # Query account health
        health_query = f'{{"get_account_health": {{"addr": "{user_address}", "account_index": 0}}}}'
        health_data = await query_contract_state(client[0], NEPTUNE_QUERIER_ADDRESS, health_query)
        
        # Query derivative position
        position_data = await query_derivative_position(client[0], INJ_PERP_MARKET_ID, subaccount_id)
        
        # Query Neptune interest model for borrow rate
        usdt_borrow_rate = await query_borrow_rate(client[0], NEPTUNE_INTEREST_MODEL_ADDRESS)
        
        # Query funding rate
        funding_rate = await query_funding_rate(client[0], INJ_PERP_MARKET_ID)
        
        # Query funding payments
        total_funding, funding_details = await query_funding_payments(client[0], [INJ_PERP_MARKET_ID], subaccount_id)
        
        # Query derivative market data
        cumulative_funding, market_mark_price = await query_derivative_market_data(client[0], INJ_PERP_MARKET_ID)
        
        # Query collateral parameters
        inj_liquidation_ltv, inj_allowable_ltv = await query_collateral_params(client[0], NEPTUNE_MARKET_CONTRACT)
        
        # Extract data from responses
        inj_collateral = await extract_inj_collateral(decoded_data)
        usdt_debt = await extract_usdt_debt(decoded_data)
        inj_price, usdt_price = await extract_prices(prices_data)
        health_factor, liquidation_threshold = await extract_account_health(health_data)
        
        # Calculate values
        inj_collateral_value = inj_collateral * inj_price
        usdt_debt_value = usdt_debt * usdt_price
        
        # Prepare the message
        message = (
            "<b>=== CURRENT MARKET PRICES ===</b>\n"
            f"INJ Price: ${inj_price:.2f}\n"
            f"USDT Price: ${usdt_price:.2f}\n\n"
            
            "<b>=== NEPTUNE FINANCE POSITION ===</b>\n"
            f"INJ Collateral: {inj_collateral} INJ (${inj_collateral_value:.2f})\n"
            f"USDT Debt: {usdt_debt} USDT (${usdt_debt_value:.2f})\n"
            f"Health Factor: {health_factor:.4f}\n"
            f"Liquidation Threshold: {liquidation_threshold:.4f}\n"
        )
        
        # Add liquidation price information
        if inj_liquidation_ltv is not None:
            neptune_liquidation_price = usdt_debt_value / (inj_collateral * inj_liquidation_ltv)
            price_drop_percentage = ((inj_price - neptune_liquidation_price) / inj_price) * 100
            message += (
                f"Liquidation LTV: {inj_liquidation_ltv:.2f}\n"
                f"Neptune Liquidation Price: ${neptune_liquidation_price:.2f} (current price: ${inj_price:.2f})\n"
                f"Price Drop to Neptune Liquidation: {price_drop_percentage:.2f}%\n"
            )
            
            # Add safety status
            if price_drop_percentage > 40:
                message += "✅ Very safe - large buffer to liquidation\n"
            elif price_drop_percentage > 25:
                message += "✅ Safe - good buffer to liquidation\n"
            elif price_drop_percentage > 15:
                message += "⚠️ Moderate buffer to liquidation\n"
            else:
                message += "🚨 Small buffer to liquidation - consider reducing debt or adding collateral\n"
        
        # Add health factor status
        if health_factor > 0:
            health_margin = ((health_factor / liquidation_threshold) - 1) * 100 if liquidation_threshold > 0 else 0
            message += f"Health Status: "
            if health_factor >= 1.5:
                message += f"✅ Excellent (margin: {health_margin:.2f}%)\n"
            elif health_factor >= 1.2:
                message += f"✅ Good (margin: {health_margin:.2f}%)\n"
            elif health_factor >= 1.0:
                message += f"⚠️ Caution (margin: {health_margin:.2f}%)\n"
            else:
                message += "🚨 At Risk of Liquidation!\n"
        
        # Add LTV ratio
        ltv_ratio = (usdt_debt_value / inj_collateral_value) * 100 if inj_collateral_value > 0 else 0
        message += f"Loan-to-Value Ratio: {ltv_ratio:.2f}%\n\n"
        
        # Add Injective perp position details
        message += "<b>=== INJECTIVE PERP POSITION ===</b>\n"
        
        if position_data:
            direction = "Long" if position_data.get('isLong', False) else "Short"
            raw_entry_price = position_data.get('entryPrice', '0')
            raw_quantity = position_data.get('quantity', '0')
            raw_margin = position_data.get('margin', '0')
            raw_cumulative_funding_entry = position_data.get('cumulativeFundingEntry', '0')
            
            quantity = float(raw_quantity) / 10**18
            entry_price = float(raw_entry_price) / 10**24
            margin = float(raw_margin) / 10**24
            cumulative_funding_entry = float(raw_cumulative_funding_entry) / 10**18
            
            position_notional = quantity * inj_price
            pnl = (entry_price - inj_price) * quantity if direction == "Short" else (inj_price - entry_price) * quantity
            
            message += (
                f"Position Direction: {direction}\n"
                f"Entry Price: ${entry_price:.2f}\n"
                f"Current Price: ${inj_price:.2f}\n"
                f"Quantity: {quantity} INJ\n"
                f"Original Margin: {margin:.4f} USDT (${margin * usdt_price:.2f})\n"
            )
            
            # Calculate funding payment
            if cumulative_funding is not None and cumulative_funding_entry is not None:
                scaling_factor = 1/1000000
                funding_diff = cumulative_funding_entry - cumulative_funding if direction == "Short" else cumulative_funding - cumulative_funding_entry
                funding_payment = -(quantity * funding_diff * scaling_factor)
                
                if funding_payment > 0:
                    message += f"Total Accumulated Funding: +{funding_payment:.6f} USDT (+${funding_payment * usdt_price:.2f})\n"
                else:
                    message += f"Total Accumulated Funding: {funding_payment:.6f} USDT (${funding_payment * usdt_price:.2f})\n"
            
            margin_with_funding = margin + (funding_payment if 'funding_payment' in locals() else 0)
            message += (
                f"Current Margin (With Funding Payments): {margin_with_funding:.4f} USDT (${margin_with_funding * usdt_price:.2f})\n"
                f"Notional Value: ${position_notional:.2f} ({quantity} INJ @ ${inj_price:.2f})\n"
                f"Unrealized PnL: ${pnl:.2f}\n"
            )
            
            # Calculate effective margin and leverage
            effective_margin = margin_with_funding * usdt_price + pnl
            leverage = position_notional / effective_margin if effective_margin > 0 else 0
            message += (
                f"Effective Margin: ${effective_margin:.2f}\n"
                f"Effective Leverage: {leverage:.2f}x\n"
            )
            
            # Calculate liquidation price for perp position
            max_leverage = 25.0
            maintenance_margin_ratio = 1 / max_leverage
            
            if direction == "Short":
                buffer_factor = 0.02
                liquidation_mark_price = (margin_with_funding * usdt_price + entry_price * quantity) / (quantity * (1 + maintenance_margin_ratio - buffer_factor))
                price_movement_to_liquidation = ((liquidation_mark_price - inj_price) / inj_price) * 100
                
                message += (
                    f"Perp Liquidation Price: ${liquidation_mark_price:.3f} (current: ${inj_price:.2f})\n"
                    f"Price Increase to Perp Liquidation: {price_movement_to_liquidation:.2f}%\n"
                )
                
                if price_movement_to_liquidation > 40:
                    message += "✅ Very safe - large buffer to liquidation\n"
                elif price_movement_to_liquidation > 25:
                    message += "✅ Safe - good buffer to liquidation\n"
                elif price_movement_to_liquidation > 15:
                    message += "⚠️ Moderate buffer to liquidation\n"
                else:
                    message += "🚨 Small buffer to liquidation - consider adding margin\n"
                
                leverage_utilization = (leverage / (1/maintenance_margin_ratio)) * 100
                message += f"Leverage Utilization: {leverage_utilization:.2f}% of maximum\n"
            
            # Add leverage warnings
            if effective_margin <= 0:
                message += "🚨 CRITICAL: Negative or zero effective margin! Position at extreme risk.\n"
            elif leverage > 5:
                message += "🚨 WARNING: Extremely high leverage! High risk of liquidation.\n"
            elif leverage > 3:
                message += "⚠️ CAUTION: Leverage is higher than recommended. Consider adding margin.\n"
            
            # Calculate overall strategy metrics
            neptune_equity = inj_collateral_value - usdt_debt_value
            overall_strategy_value = neptune_equity + effective_margin
            
            funding_pnl = funding_payment * usdt_price if 'funding_payment' in locals() else 0
            funding_pnl_percentage = (funding_pnl / overall_strategy_value) * 100 if overall_strategy_value > 0 else 0
            
            message += (
                "\n<b>=== OVERALL STRATEGY VALUE ===</b>\n"
                f"Neptune Equity (Collateral - Debt): ${neptune_equity:.2f}\n"
                f"Injective Perp Effective Margin: ${effective_margin:.2f}\n"
                f"Total Strategy Value: ${overall_strategy_value:.2f}\n"
            )
            
            if funding_pnl > 0:
                message += f"PnL from Funding Rate: +${funding_pnl:.2f} (+{funding_pnl_percentage:.2f}% of strategy value)\n"
            else:
                message += f"PnL from Funding Rate: ${funding_pnl:.2f} ({funding_pnl_percentage:.2f}% of strategy value)\n"
            
            # Add borrowing and funding costs
            message += (
                "\n<b>=== BORROWING & FUNDING COSTS ===</b>\n"
                f"USDT Borrow Rate (Neptune): {usdt_borrow_rate:.2f}% APR (${(usdt_debt_value * usdt_borrow_rate / 100):.2f}/year on ${usdt_debt_value:.2f} debt)\n"
            )
            
            if funding_rate > 0:
                message += (
                    f"INJ Funding Rate (Injective): +{funding_rate:.2f}% APR (${(position_notional * funding_rate / 100):.2f}/year on ${position_notional:.2f} position)\n"
                    "(shorts receive)\n"
                )
            elif funding_rate < 0:
                message += (
                    f"INJ Funding Rate (Injective): {funding_rate:.2f}% APR (-${abs(position_notional * funding_rate / 100):.2f}/year on ${position_notional:.2f} position)\n"
                    "(shorts pay)\n"
                )
            else:
                message += (
                    f"INJ Funding Rate (Injective): {funding_rate:.2f}% APR ($0.00/year)\n"
                    "(neutral)\n"
                )
            
            if direction == "Short":
                annual_borrow_cost = usdt_debt_value * (usdt_borrow_rate / 100)
                annual_funding_income = position_notional * (funding_rate / 100)
                annual_net_yield = annual_funding_income - annual_borrow_cost
                
                weighted_borrow_cost = (usdt_debt_value * (usdt_borrow_rate / 100)) / overall_strategy_value * 100 if overall_strategy_value > 0 else 0
                weighted_funding_income = (position_notional * (funding_rate / 100)) / overall_strategy_value * 100 if overall_strategy_value > 0 else 0
                weighted_effective_cost = weighted_borrow_cost - weighted_funding_income
                
                message += (
                    "\nNet Annual Dollar Amounts:\n"
                    f"   → Annual Borrow Cost: ${annual_borrow_cost:.2f}\n"
                    f"   → Annual Funding Income: ${annual_funding_income:.2f}\n"
                    f"   → Net Annual Yield: ${annual_net_yield:.2f}\n\n"
                    f"As Percentage of Strategy Value (${overall_strategy_value:.2f}):\n"
                    f"   → Borrow Cost: {weighted_borrow_cost:.2f}% APR\n"
                    f"   → Funding Income: {weighted_funding_income:.2f}% APR\n"
                    f"   → Effective Cost: {weighted_effective_cost:.2f}% APR\n"
                )
                
                if weighted_effective_cost < 0:
                    message += f"   → Funding payments received exceed borrowing costs by {abs(weighted_effective_cost):.2f}%\n"
                else:
                    message += "   → Net cost after accounting for funding payments\n"
                
                strategy_yield = -weighted_effective_cost
                message += f"\nStrategy Yield: {strategy_yield:.2f}% APR (${annual_net_yield:.2f}/year)\n"
                
                if strategy_yield > 30:
                    message += "✅ Excellent yield\n"
                elif strategy_yield > 15:
                    message += "✅ Very good yield\n"
                elif strategy_yield > 5:
                    message += "✅ Good yield\n"
                elif strategy_yield > 0:
                    message += "✅ Positive yield\n"
                else:
                    message += "🚨 Negative yield - consider adjusting strategy\n"
            
            # Add position comparison
            message += (
                "\n<b>=== POSITION COMPARISON ===</b>\n"
                "Strategy: Short INJ on Injective Perp to hedge INJ collateral on Neptune\n"
            )
            
            if direction == "Short":
                hedge_ratio = inj_collateral_value / position_notional * 100 if position_notional > 0 else 0
                message += f"Hedge Ratio (Collateral / Perp Notional): {hedge_ratio:.2f}%\n"
                
                if hedge_ratio > 95 and hedge_ratio < 105:
                    message += (
                        "✅ Position is well-hedged (95-105% range)\n"
                        "   Your INJ collateral value is properly hedged against price movements.\n"
                    )
                elif hedge_ratio < 95:
                    under_hedged_amount = position_notional - inj_collateral_value
                    message += (
                        f"⚠️ Position is under-hedged. Consider increasing collateral by ${under_hedged_amount:.2f}\n"
                        f"   This would require approximately {under_hedged_amount / inj_price:.4f} more INJ as collateral.\n"
                    )
                else:
                    over_hedged_amount = inj_collateral_value - position_notional
                    message += (
                        f"⚠️ Position is over-hedged. Consider reducing collateral by ${over_hedged_amount:.2f}\n"
                        f"   This would require withdrawing approximately {over_hedged_amount / inj_price:.4f} INJ.\n"
                    )
                
                net_inj_exposure = inj_collateral - quantity
                net_inj_value = net_inj_exposure * inj_price
                message += f"\nNet INJ Exposure: {net_inj_exposure:.4f} INJ (${net_inj_value:.2f})\n"
                if abs(net_inj_exposure) < 0.01:
                    message += "✅ Delta neutral position achieved\n"
                else:
                    message += f"{'⚠️ Long' if net_inj_exposure > 0 else '⚠️ Short'} bias in your overall position\n"
        else:
            message += (
                "No derivative position found.\n\n"
                "<b>=== POSITION COMPARISON ===</b>\n"
                f"⚠️ No hedge for ${inj_collateral_value:.2f} of collateral. Consider opening a short position.\n"
                f"   Recommended position size: Short {inj_collateral_value / inj_price:.4f} INJ\n"
            )
        
        # Add navigation buttons
        keyboard = [
            [InlineKeyboardButton("Close Position", callback_data="close_position")],
            [InlineKeyboardButton("Analyze with AI", callback_data="analyze_positions")],
            [InlineKeyboardButton("Back to Menu", callback_data="back_to_menu")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Split message if it's too long (Telegram has a 4096 character limit)
        if len(message) > 4000:
            # Send the first part
            first_part = message[:4000] + "...\n(continued in next message)"
            await update.callback_query.edit_message_text(
                first_part,
                reply_markup=None,
                parse_mode="HTML"
            )
            # Send the second part
            second_part = "...(continued)\n" + message[4000:]
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=second_part,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        else:
            await update.callback_query.edit_message_text(
                message,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        
    except Exception as e:
        error_message = f"Error getting position info: {str(e)}"
        logger.error(error_message)
        await update.callback_query.edit_message_text(error_message)

async def close_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Close positions in the Delta Neutral Strategy."""
    # Check if private key is configured
    if not os.getenv("INJECTIVE_PRIVATE_KEY"):
        message = (
            "⚠️ Private Key Not Configured\n\n"
            "Please add your private key to the .env file:\n"
            "INJECTIVE_PRIVATE_KEY=your_private_key_here\n"
            "(Remove the '0x' prefix if present)"
        )
        await update.callback_query.edit_message_text(message)
        return

    try:
        status_message = await update.callback_query.edit_message_text(
            "Closing Delta Neutral Strategy positions... Please wait."
        )
        
        # Initialize client and components
        client, composer, network, priv_key, pub_key, address = await setup_client()
        
        # Get subaccount ID
        subaccount_id = get_subaccount_id(address.to_acc_bech32())
        
        # 1. Close Helix position
        await status_message.edit_text("Step 1/2: Closing Helix short position...")
        helix_result = await close_helix_position(
            client, composer, address, subaccount_id, 
            INJ_PERP_MARKET_ID, network, priv_key, pub_key
        )
        if not helix_result:
            raise Exception("Failed to close Helix position")
        
        # 2. Withdraw collateral from Neptune
        await status_message.edit_text("Step 2/2: Withdrawing collateral from Neptune...")
        
        # Query user's debt first
        user_query = f'{{"get_user_accounts": {{"addr": "{address.to_acc_bech32()}"}}}}'
        debt_info = await query_market_state(client, NEPTUNE_MARKET_CONTRACT, user_query)
        
        if debt_info and debt_info.get('debt'):
            # Repay any remaining debt
            for denom, info in debt_info['debt'].items():
                if float(info['principal']) > 0:
                    repay_msg = {
                        "repay": {
                            "account_index": 0,
                            "amount": info['principal'],
                            "asset_info": {
                                "native_token": {"denom": denom}
                            }
                        }
                    }
                    repay_result = await execute_contract(
                        json.dumps(repay_msg), debt_info, client, composer,
                        address, network, priv_key, pub_key,
                        float(info['principal'])
                    )
                    if not repay_result:
                        raise Exception(f"Failed to repay {denom} debt")
        
        # Withdraw all collateral
        withdraw_msg = {"withdraw_collateral": {"account_index": 0}}
        withdraw_result = await execute_contract(
            json.dumps(withdraw_msg), debt_info, client, composer,
            address, network, priv_key, pub_key, 0
        )
        if not withdraw_result:
            raise Exception("Failed to withdraw collateral")
        
        # Update message with success
        keyboard = [[InlineKeyboardButton("Back to Menu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await status_message.edit_text(
            "✅ Successfully closed all Delta Neutral Strategy positions!",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        error_message = f"Error closing positions: {str(e)}"
        logger.error(error_message)
        
        keyboard = [[InlineKeyboardButton("Back to Menu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await status_message.edit_text(
            f"❌ {error_message}",
            reply_markup=reply_markup
        )

async def analyze_with_iagent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Analyze positions using AI."""
    try:
        # Initialize client
        client = await setup_client()
        
        # Get position information
        position_info = await get_position_info(client)
        
        if not position_info:
            message = "No active positions to analyze."
        else:
            # Format position data for analysis
            analysis = (
                "Delta Neutral Strategy Position Analysis:\n\n"
                f"• Current Position Size: {position_info['short_size']} INJ\n"
                f"• Entry Price: ${position_info['entry_price']:.2f}\n"
                f"• Current Price: ${position_info['current_price']:.2f}\n"
                f"• PnL: ${position_info['pnl']:.2f}\n"
                f"• Health Factor: {position_info['health_factor']:.2f}\n\n"
                "Recommendations:\n"
                "1. Monitor funding rates for optimal exit timing\n"
                "2. Keep health factor above 1.5 for safety\n"
                "3. Consider taking profits if PnL > 5%"
            )
            message = analysis
        
        keyboard = [
            [InlineKeyboardButton("View Positions", callback_data="view_positions")],
            [InlineKeyboardButton("Back to Menu", callback_data="back_to_menu")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.edit_message_text(
            message,
            reply_markup=reply_markup
        )
        
    except Exception as e:
        error_message = f"Error analyzing positions: {str(e)}"
        logger.error(error_message)
        await update.callback_query.edit_message_text(error_message)

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button clicks from inline keyboards."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "view_positions":
        await show_positions(update, context)
    elif query.data == "explain_strategy":
        await explain_strategy(update, context)
    elif query.data == "show_math":
        await show_strategy_math(update, context)
    elif query.data == "execute_strategy":
        await execute_strategy(update, context)
    elif query.data == "close_position":
        await close_strategy(update, context)
    elif query.data.startswith("invest_amount_"):
        amount = float(query.data.split("_")[2])
        context.args = [str(amount)]
        await execute_strategy(update, context)
    elif query.data == "back_to_menu":
        await start(update, context)
    elif query.data == "analyze_positions":
        await analyze_with_iagent(update, context)
    else:
        await query.edit_message_text(f"Unsupported button: {query.data}")

async def explain_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Explain Delta Neutral Strategy to the user."""
    explanation = (
        "*Delta Neutral Strategy: Borrow → Short*\n\n"
        "This strategy involves borrowing INJ from Neptune Markets, selling it on Helix, "
        "and then placing a short position to profit from funding rates while remaining delta-neutral.\n\n"
        "Steps:\n"
        "1. Borrow INJ from Neptune Markets\n"
        "2. Sell the borrowed INJ on Helix\n"
        "3. Create a short position on INJ/USDT perpetual market\n"
        "4. Collect negative funding rates while maintaining delta neutrality\n\n"
        "To execute this strategy, use the /invest command followed by the amount you wish to invest."
    )
    
    keyboard = [
        [InlineKeyboardButton("Show Math", callback_data="show_math")],
        [InlineKeyboardButton("Execute Strategy", callback_data="execute_strategy")],
        [InlineKeyboardButton("Back to Menu", callback_data="back_to_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(explanation, reply_markup=reply_markup, parse_mode="HTML")

async def show_strategy_math(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the mathematical formulas behind the Delta Neutral Strategy."""
    math_explanation = (
        "<b>Delta Neutral Strategy: Mathematical Breakdown</b>\n\n"
        "This strategy profits from negative funding rates while remaining delta neutral.\n\n"
        "<b>Key Variables:</b>\n"
        "• BF = Borrowed Funds (e.g., 1 INJ)\n"
        "• BR = Neptune Borrow Rate (APY)\n"
        "• FR = Helix Funding Rate (annual equivalent)\n"
        "• TPS = Total Position Size (1x leverage)\n\n"
        
        "<b>Cost Structure:</b>\n"
        "Borrowing Cost (BC) = BF × BR\n\n"
        
        "<b>Revenue:</b>\n"
        "Funding Rate Earnings (FRE) = TPS × Helix Funding Rate\n\n"
        
        "<b>Profitability Formula:</b>\n"
        "Net APY = (TPS × Helix Funding Rate) − (BF × Neptune Borrow Rate)\n\n"
        
        "<b>Risk Management:</b>\n"
        "• Delta Neutral: Short position matches borrowed amount\n"
        "• Liquidation Risk: Monitor collateral requirements on Neptune\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("Back to Strategy", callback_data="explain_strategy")],
        [InlineKeyboardButton("Execute Strategy", callback_data="execute_strategy")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(math_explanation, reply_markup=reply_markup, parse_mode="HTML")

async def execute_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute the Delta Neutral Strategy with the specified amount."""
    # Check if private key is configured
    if not os.getenv("INJECTIVE_PRIVATE_KEY"):
        message = (
            "⚠️ Private Key Not Configured\n\n"
            "Please add your private key to the .env file:\n"
            "INJECTIVE_PRIVATE_KEY=your_private_key_here\n"
            "(Remove the '0x' prefix if present)"
        )
        if update.callback_query:
            await update.callback_query.edit_message_text(message)
        else:
            await update.message.reply_text(message)
        return
    
    # Check if amount is provided in command arguments
    if context.args:
        try:
            amount = float(context.args[0])
            # Execute strategy with amount
            await execute_delta_neutral_strategy(update, context, amount)
            return
        except ValueError:
            error_message = "Invalid amount format. Please provide a number."
            if update.callback_query:
                await update.callback_query.edit_message_text(error_message)
            else:
                await update.message.reply_text(error_message)
            return
    
    # If no amount provided, show the amount selection menu
    try:
        helix_rates = get_helix_rates()
        inj_data = helix_rates.get('INJ', {'open_interest': 0})
        open_interest = inj_data['open_interest']
    except Exception as e:
        logger.error(f"Error getting market data: {str(e)}")
        open_interest = "Unknown"
    
    message = (
        "<b>Delta Neutral Strategy: Amount Selection</b>\n\n"
        f"Current INJ/USDT open interest: ${open_interest:,}\n\n"
        "Enter the amount to invest:\n"
        "Example: /invest 10\n\n"
        "Note: Amounts >5% of open interest may cause market imbalance."
    )
    
    keyboard = [
        [
            InlineKeyboardButton("Invest 1 INJ", callback_data="invest_amount_1"),
            InlineKeyboardButton("Invest 5 INJ", callback_data="invest_amount_5"),
        ],
        [InlineKeyboardButton("Back", callback_data="back_to_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            message,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

async def execute_delta_neutral_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: float):
    """Execute the actual Delta Neutral Strategy with the specified amount"""
    try:
        # Initialize status message
        if update.callback_query:
            status_message = await update.callback_query.edit_message_text(
                f"Executing Delta Neutral Strategy with {amount} INJ..."
            )
        else:
            status_message = await update.message.reply_text(
                f"Executing Delta Neutral Strategy with {amount} INJ..."
            )
        
        # Initialize client
        client, composer, network, priv_key, pub_key, address = await setup_client()
        
        # Convert amount to smallest units (18 decimals for INJ)
        inj_amount = int(amount * 10**18)
        
        # 1. Deposit INJ collateral
        await status_message.edit_text(f"Step 1/4: Depositing {amount} INJ as collateral...")
        funds = [composer.coin(amount=inj_amount, denom="inj")]
        deposit_msg = '{"deposit_collateral": {"account_index": 0}}'
        
        deposit_result = await execute_contract_tx(
            client, composer, network, priv_key, pub_key, address,
            NEPTUNE_MARKET_CONTRACT, deposit_msg, funds
        )
        
        if not deposit_result:
            raise Exception("Deposit transaction failed")
        
        # 2. Query collateral and prices
        await status_message.edit_text("Step 2/4: Calculating optimal borrow amount...")
        
        # Query user accounts to get collateral
        user_query = f'{{"get_user_accounts": {{"addr": "{address.to_acc_bech32()}"}}}}'
        decoded_data = await query_contract_state(client, NEPTUNE_MARKET_CONTRACT, user_query)
        
        # Query prices
        price_query = json.dumps({
            "get_prices": {
                "assets": [
                    {"native_token": {"denom": "inj"}},
                    {"native_token": {"denom": "peggy0xdAC17F958D2ee523a2206206994597C13D831ec7"}}
                ]
            }
        })
        prices_data = await query_prices(client, NEPTUNE_ORACLE_ADDRESS, price_query)
        
        # Extract data from responses
        inj_collateral = await extract_inj_collateral(decoded_data)
        inj_price, usdt_price = await extract_prices(prices_data)
        
        # Calculate values for borrowing
        inj_collateral_value = inj_collateral * inj_price
        usdt_to_borrow = inj_collateral_value * 0.43  # Borrow 43% of collateral value
        usdt_to_borrow_amount = int(usdt_to_borrow * 10**6)  # Convert to USDT's smallest unit (6 decimals)
        
        # Calculate dynamic leverage
        position_value = inj_collateral * inj_price  # Position value in USD
        margin_value = usdt_to_borrow  # Margin value in USD (equal to borrowed USDT)
        dynamic_leverage = Decimal(str((position_value / margin_value)))
        
        # 3. Borrow USDT
        await status_message.edit_text(f"Step 3/4: Borrowing {usdt_to_borrow:.2f} USDT...")
        
        borrow_msg = {
            "borrow": {
                "account_index": 0,
                "amount": str(usdt_to_borrow_amount),
                "asset_info": {
                    "native_token": {
                        "denom": "peggy0xdAC17F958D2ee523a2206206994597C13D831ec7"
                    }
                }
            }
        }
        
        borrow_result = await execute_contract_tx(
            client, composer, network, priv_key, pub_key, address,
            NEPTUNE_MARKET_CONTRACT, borrow_msg
        )
        
        if not borrow_result:
            raise Exception("Borrow transaction failed")
        
        # 4. Create derivative market order
        await status_message.edit_text("Step 4/4: Creating short position on Helix...")
        
        # Get subaccount ID
        subaccount_id = get_subaccount_id(address.to_acc_bech32())
        
        # Calculate order parameters
        inj_quantity = inj_collateral  # Use the same amount as the deposited collateral
        notional_value = inj_quantity * inj_price
        min_notional = MIN_NOTIONAL_SMALLEST_UNITS / 10**6  # Convert to USDT
        
        # Check if notional value meets minimum requirement
        if notional_value < min_notional:
            inj_quantity = (MIN_NOTIONAL_SMALLEST_UNITS * 1.01) / 10**6 / inj_price
        
        # Convert to Decimal objects with appropriate precision
        inj_quantity_decimal = Decimal(str(round(inj_quantity, 6)))
        inj_price_decimal = Decimal(str(round(inj_price, 6)))
        
        # Create and execute the derivative market order
        order_result = await create_derivative_market_order(
            client, composer, network, priv_key, pub_key, address,
            INJ_PERP_MARKET_ID, subaccount_id, inj_price_decimal, inj_quantity_decimal, usdt_to_borrow_amount
        )
        
        if not order_result:
            raise Exception("Market order transaction failed")
        
        # Update message with success
        keyboard = [[InlineKeyboardButton("View Positions", callback_data="view_positions")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await status_message.edit_text(
            f"✅ Successfully executed Delta Neutral Strategy!\n\n"
            f"• Deposited: {amount} INJ\n"
            f"• Borrowed: {usdt_to_borrow:.2f} USDT\n"
            f"• Short Position: {inj_quantity:.6f} INJ\n"
            f"• Entry Price: ${inj_price:.2f}\n"
            f"• Leverage: {float(dynamic_leverage):.2f}x",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        error_message = f"Error executing strategy: {str(e)}"
        logger.error(error_message)
        
        keyboard = [[InlineKeyboardButton("Back to Menu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if 'status_message' in locals():
            await status_message.edit_text(
                f"❌ {error_message}",
                reply_markup=reply_markup
            )
        elif update.callback_query:
            await update.callback_query.edit_message_text(
                f"❌ {error_message}",
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                f"❌ {error_message}",
                reply_markup=reply_markup
            )

async def setup_client():
    """Initialize client and account"""
    # Load private key from .env file
    configured_private_key = os.getenv("INJECTIVE_PRIVATE_KEY")
    if not configured_private_key:
        raise ValueError("INJECTIVE_PRIVATE_KEY not found in environment variables")
        
    # Remove '0x' prefix if present and ensure the key is properly formatted
    if configured_private_key.startswith('0x'):
        configured_private_key = configured_private_key[2:]
    
    # Ensure the key is valid hex
    try:
        int(configured_private_key, 16)  # Validate hex string
        if len(configured_private_key) != 64:  # Private keys should be 32 bytes (64 hex chars)
            raise ValueError("Private key must be 32 bytes (64 hex characters)")
    except ValueError as e:
        raise ValueError(f"Invalid private key format: {str(e)}")
    
    # Initialize network and client
    network = Network.mainnet()
    client = AsyncClient(network)
    composer = await client.composer()
    await client.sync_timeout_height()
    
    # Load account
    try:
        priv_key = PrivateKey.from_hex(configured_private_key)
        pub_key = priv_key.to_public_key()
        address = pub_key.to_address()
        await client.fetch_account(address.to_acc_bech32())
    except Exception as e:
        raise ValueError(f"Error initializing account: {str(e)}")
    
    return client, composer, network, priv_key, pub_key, address

async def execute_contract_tx(client, composer, network, priv_key, pub_key, address, contract, msg_data, funds=None):
    """Execute a contract transaction with simulation and broadcasting"""
    if funds is None:
        funds = []
    
    # Always refresh account information to get the latest sequence number
    await client.fetch_account(address.to_acc_bech32())
        
    # Prepare transaction message
    msg = composer.MsgExecuteContract(
        sender=address.to_acc_bech32(),
        contract=contract,
        msg=msg_data if isinstance(msg_data, str) else json.dumps(msg_data),
        funds=funds,
    )
    
    # Build and simulate transaction
    tx = (
        Transaction()
        .with_messages(msg)
        .with_sequence(client.get_sequence())
        .with_account_num(client.get_number())
        .with_chain_id(network.chain_id)
    )
    sim_sign_doc = tx.get_sign_doc(pub_key)
    sim_sig = priv_key.sign(sim_sign_doc.SerializeToString())
    sim_tx_raw_bytes = tx.get_tx_data(sim_sig, pub_key)
    
    # Simulate transaction
    try:
        sim_res = await client.simulate(sim_tx_raw_bytes)
    except RpcError as ex:
        print(f"Simulation error: {ex}")
        return None
    
    # Build transaction with gas limit
    gas_price = GAS_PRICE
    gas_limit = int(sim_res["gasInfo"]["gasUsed"]) + GAS_BUFFER
    gas_fee = "{:.18f}".format((gas_price * gas_limit) / pow(10, 18)).rstrip("0")
    fee = [
        composer.coin(
            amount=gas_price * gas_limit,
            denom=network.fee_denom,
        )
    ]
    
    # Refresh account information again before broadcasting
    await client.fetch_account(address.to_acc_bech32())
    
    tx = (
        Transaction()
        .with_messages(msg)
        .with_sequence(client.get_sequence())
        .with_account_num(client.get_number())
        .with_chain_id(network.chain_id)
        .with_gas(gas_limit)
        .with_fee(fee)
        .with_memo("")
        .with_timeout_height(client.timeout_height)
    )
    sign_doc = tx.get_sign_doc(pub_key)
    sig = priv_key.sign(sign_doc.SerializeToString())
    tx_raw_bytes = tx.get_tx_data(sig, pub_key)
    
    # Broadcast transaction
    res = await client.broadcast_tx_sync_mode(tx_raw_bytes)
    logger.info(f"Transaction result: {res}")
    logger.info(f"Gas used: {gas_limit}, Gas fee: {gas_fee} INJ")
    
    # Wait for transaction to be included in a block
    sleep(3)
    
    return res

async def query_contract_state(client, contract_address, query_data):
    """Query a smart contract's state"""
    contract_state = await client.fetch_smart_contract_state(
        address=contract_address, 
        query_data=query_data
    )
    return json.loads(base64.b64decode(contract_state["data"]))

async def create_derivative_market_order(client, composer, network, priv_key, pub_key, address, 
                                      market_id, subaccount_id, price, quantity, usdt_to_borrow_amount, order_type="SELL"):
    """Create a derivative market order"""
    # Always refresh account information to get the latest sequence number
    await client.fetch_account(address.to_acc_bech32())
    await client.sync_timeout_height()
    
    # Prepare order message with 5% price buffer for better execution
    msg = composer.msg_create_derivative_market_order(
        sender=address.to_acc_bech32(),
        market_id=market_id,
        subaccount_id=subaccount_id,
        fee_recipient=FEE_RECIPIENT,
        price=price*Decimal(0.95),  # 5% price buffer for better execution
        quantity=quantity,
        margin=usdt_to_borrow_amount/Decimal(10**6),  # Convert from smallest units
        order_type=order_type,
        cid=str(uuid.uuid4()),
    )
    
    # Build and simulate transaction
    tx = (
        Transaction()
        .with_messages(msg)
        .with_sequence(client.get_sequence())
        .with_account_num(client.get_number())
        .with_chain_id(network.chain_id)
    )
    sim_sign_doc = tx.get_sign_doc(pub_key)
    sim_sig = priv_key.sign(sim_sign_doc.SerializeToString())
    sim_tx_raw_bytes = tx.get_tx_data(sim_sig, pub_key)
    
    # Simulate transaction
    try:
        sim_res = await client.simulate(sim_tx_raw_bytes)
        logger.info(f"Simulation successful. Gas used: {sim_res['gasInfo']['gasUsed']}")
    except RpcError as ex:
        print(f"Simulation failed: {ex}")
        return None
    
    # Build transaction with gas limit
    gas_price = GAS_PRICE
    gas_limit = int(sim_res["gasInfo"]["gasUsed"]) + GAS_BUFFER
    gas_fee = "{:.18f}".format((gas_price * gas_limit) / pow(10, 18)).rstrip("0")
    
    # Refresh account information again before broadcasting
    await client.fetch_account(address.to_acc_bech32())
    await client.sync_timeout_height()
    
    # Create transaction with latest sequence
    fee = [composer.coin(amount=gas_price * gas_limit, denom=network.fee_denom)]
    tx = (
        Transaction()
        .with_messages(msg)
        .with_sequence(client.get_sequence())
        .with_account_num(client.get_number())
        .with_chain_id(network.chain_id)
        .with_gas(gas_limit)
        .with_fee(fee)
        .with_memo("")
        .with_timeout_height(client.timeout_height)
    )
    sign_doc = tx.get_sign_doc(pub_key)
    sig = priv_key.sign(sign_doc.SerializeToString())
    tx_raw_bytes = tx.get_tx_data(sig, pub_key)
    
    # Execute transaction
    logger.info(f"Ready to execute transaction with gas fee: {gas_fee} INJ")
    res = await client.broadcast_tx_sync_mode(tx_raw_bytes)
    logger.info(f"Transaction result: {res}")
    
    # Wait for transaction to be included in a block
    sleep(3)
    
    return res

async def extract_inj_collateral(decoded_data):
    """Extract INJ collateral amount from contract query response"""
    inj_collateral = 0
    inj_collateral_data = decoded_data[0][1]
    
    # Check if there are collateral pool accounts
    if 'collateral_pool_accounts' in inj_collateral_data:
        for pool in inj_collateral_data['collateral_pool_accounts']:
            # Find the INJ token entry
            for entry in pool:
                if isinstance(entry, dict) and 'native_token' in entry and entry['native_token']['denom'] == 'inj':
                    # The next entry should contain the principal
                    inj_index = pool.index(entry)
                    if inj_index + 1 < len(pool) and 'principal' in pool[inj_index + 1]:
                        inj_collateral += float(pool[inj_index + 1]['principal']) / 10**18  # Convert from 18 decimals
    
    return inj_collateral

async def extract_prices(prices_data):
    """Extract asset prices from oracle query response"""
    inj_price = 0
    usdt_price = 0
    
    for asset_price_pair in prices_data:
        asset = asset_price_pair[0]
        price_info = asset_price_pair[1]
        
        if 'native_token' in asset and asset['native_token']['denom'] == 'inj':
            inj_price = float(price_info['price'])
        elif 'native_token' in asset and 'peggy' in asset['native_token']['denom']:
            usdt_price = float(price_info['price'])
    
    return inj_price, usdt_price

async def close_helix_position(client, composer, address, subaccount_id, market_id, network, priv_key, pub_key):
    position = await client.fetch_chain_subaccount_position_in_market(
        subaccount_id=subaccount_id, market_id=market_id
    )
    print("=== Position Info ===")
    print(position)

    # Get current asset prices from the Neptune Oracle
    price_query = json.dumps({
        "get_prices": {
            "assets": [
                {"native_token": {"denom": "inj"}},
                {"native_token": {"denom": "peggy0xdAC17F958D2ee523a2206206994597C13D831ec7"}}
            ]
        }
    })
    prices_data = await query_prices(client, NEPTUNE_ORACLE_ADDRESS, price_query)
    inj_price, usdt_price = await extract_prices(prices_data)
    print(f"Current INJ Price: ${inj_price:.4f}")
    print(f"Current USDT Price: ${usdt_price:.4f}")

    position_data = position.get("state", {})
    if not position_data:
        print("No active position found in this market")
        return

    is_long = position_data.get("isLong", False)
    quantity = float(position_data.get("quantity", "0")) / 10**18
    print(f"Position Direction: {'Long' if is_long else 'Short'}")
    print(f"Position Quantity: {quantity} INJ")
    order_type = "SELL" if is_long else "BUY"
    print(f"Order Type to Close: {order_type}")

    # Fetch market prices for the derivative
    prices = await client.fetch_derivative_mid_price_and_tob(market_id=market_id)
    best_sell_price = float(prices["bestSellPrice"]) / 10**24
    best_buy_price = float(prices["bestBuyPrice"]) / 10**24
    print(f"Best Sell Price: ${best_sell_price:.6f}")
    print(f"Best Buy Price: ${best_buy_price:.6f}")

    # Add a small buffer to prices to improve execution chances
    price_buffer = 0.001  # 0.1% buffer
    buffered_sell_price = best_sell_price * (1 + price_buffer)  # Lower sell price (better for closing longs)
    buffered_buy_price = best_buy_price * (1 - price_buffer)    # Higher buy price (better for closing shorts)
    print(f"Buffered Sell Price (-{price_buffer*100}%): ${buffered_sell_price:.6f}")
    print(f"Buffered Buy Price (+{price_buffer*100}%): ${buffered_buy_price:.6f}")

    fee_recipient = "inj1xwfmk0rxf5nw2exvc42u2utgntuypx3k3gdl90"
    execution_price = buffered_buy_price if is_long else buffered_sell_price
    price_decimal = Decimal(str(execution_price))
    quantity_decimal = Decimal(str(quantity))

    try:
        msg = composer.msg_create_derivative_market_order(
            sender=address.to_acc_bech32(),
            market_id=market_id,
            subaccount_id=subaccount_id,
            fee_recipient=fee_recipient,
            price=price_decimal,
            quantity=quantity_decimal,
            margin=composer.calculate_margin(
                quantity=quantity_decimal,
                price=price_decimal,
                leverage=Decimal(1),
                is_reduce_only=True
            ),
            order_type=order_type,
            cid=str(uuid.uuid4()),
        )
        tx = (
            Transaction()
            .with_messages(msg)
            .with_sequence(client.get_sequence())
            .with_account_num(client.get_number())
            .with_chain_id(network.chain_id)
        )
        sim_sign_doc = tx.get_sign_doc(pub_key)
        sim_sig = priv_key.sign(sim_sign_doc.SerializeToString())
        sim_tx_raw_bytes = tx.get_tx_data(sim_sig, pub_key)
        sim_res = await client.simulate(sim_tx_raw_bytes)
        print("Simulation successful")
        
        gas_price = GAS_PRICE
        gas_limit = int(sim_res["gasInfo"]["gasUsed"]) + 40000
        gas_fee = "{:.18f}".format((gas_price * gas_limit) / 10**18).rstrip("0")
        fee = [composer.coin(amount=gas_price * gas_limit, denom=network.fee_denom)]
        tx = tx.with_gas(gas_limit).with_fee(fee).with_memo("").with_timeout_height(client.timeout_height)
        sign_doc = tx.get_sign_doc(pub_key)
        sig = priv_key.sign(sign_doc.SerializeToString())
        tx_raw_bytes = tx.get_tx_data(sig, pub_key)
        res = await client.broadcast_tx_sync_mode(tx_raw_bytes)
        print("=== Transaction Details ===")
        print(res)
        print(f"Gas wanted: {gas_limit}")
        print(f"Gas fee: {gas_fee} INJ")
        
        # Check transaction status
        if 'txResponse' in res and res['txResponse'].get('code', 0) == 0:
            print("Position closed successfully!")
            return res
        else:
            if 'txResponse' in res and 'rawLog' in res['txResponse']:
                print(f"Transaction failed with error: {res['txResponse']['rawLog']}")
            else:
                print("Transaction status unclear. Please check manually.")
            return None
            
    except RpcError as ex:
        print(f"Transaction failed: {ex}")
        return None

async def query_market_state(client, contract_address, query_data):
    """Query a smart contract's state"""
    contract_state = await client.fetch_smart_contract_state(
        address=contract_address, query_data=query_data
    )
    json_data = json.loads(base64.b64decode(contract_state["data"]))
    result = {}
    if json_data and isinstance(json_data, list):
        for account in json_data:
            if len(account) < 2:
                continue
            account_data = account[1]
            debt = {}
            if "debt_pool_accounts" in account_data:
                for pool in account_data["debt_pool_accounts"]:
                    if len(pool) >= 2 and "native_token" in pool[0]:
                        denom = pool[0]["native_token"].get("denom", "")
                        debt[denom] = {
                            "principal": pool[1].get("principal", "0"),
                            "shares": pool[1].get("shares", "0")
                        }
            collateral = {}
            if "collateral_pool_accounts" in account_data:
                for pool in account_data["collateral_pool_accounts"]:
                    if len(pool) >= 2 and "native_token" in pool[0]:
                        denom = pool[0]["native_token"].get("denom", "")
                        collateral[denom] = {"principal": pool[1].get("principal", "0")}
            result = {"debt": debt, "collateral": collateral}
            break
    return result

async def execute_contract(msg, debt_info, client, composer, address, network, priv_key, pub_key, amount):
    """Execute a contract transaction with proper error handling and gas estimation"""
    try:
        # Prepare transaction message
        msg = composer.MsgExecuteContract(
            sender=address.to_acc_bech32(),
            contract=NEPTUNE_MARKET_CONTRACT,
            msg=msg,
            funds=[composer.coin(
                amount=int(amount),
                denom="peggy0xdAC17F958D2ee523a2206206994597C13D831ec7"
            )] if amount > 0 else []
        )

        # Build and simulate transaction
        tx = (
            Transaction()
            .with_messages(msg)
            .with_sequence(client.get_sequence())
            .with_account_num(client.get_number())
            .with_chain_id(network.chain_id)
        )
        sim_sign_doc = tx.get_sign_doc(pub_key)
        sim_sig = priv_key.sign(sim_sign_doc.SerializeToString())
        sim_tx_raw_bytes = tx.get_tx_data(sim_sig, pub_key)

        # Simulate transaction
        sim_res = await client.simulate(sim_tx_raw_bytes)
        print(f"Simulation successful. Gas used: {sim_res['gasInfo']['gasUsed']}")

        # Calculate gas and fee
        gas_price = GAS_PRICE
        gas_limit = int(sim_res["gasInfo"]["gasUsed"]) + GAS_FEE_BUFFER_AMOUNT
        gas_fee = "{:.18f}".format((gas_price * gas_limit) / pow(10, 18)).rstrip("0")
        fee = [composer.coin(amount=gas_price * gas_limit, denom=network.fee_denom)]

        # Build final transaction
        tx = (
            Transaction()
            .with_messages(msg)
            .with_sequence(client.get_sequence())
            .with_account_num(client.get_number())
            .with_chain_id(network.chain_id)
            .with_gas(gas_limit)
            .with_fee(fee)
            .with_memo("")
            .with_timeout_height(client.timeout_height)
        )
        sign_doc = tx.get_sign_doc(pub_key)
        sig = priv_key.sign(sign_doc.SerializeToString())
        tx_raw_bytes = tx.get_tx_data(sig, pub_key)

        # Broadcast transaction
        res = await client.broadcast_tx_sync_mode(tx_raw_bytes)
        print(f"Transaction result: {res}")
        print(f"Gas used: {gas_limit}, Gas fee: {gas_fee} INJ")

        return res

    except Exception as e:
        print(f"Error executing contract: {str(e)}")
        return None

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors in the bot."""
    logger.error(f"Update {update} caused error {context.error}")
    error_message = str(context.error)
    
    if "terminated by other getUpdates request" in error_message:
        logger.error("Another bot instance is already running. Please stop other instances first.")
        return
    
    try:
        if update and update.callback_query:
            await update.callback_query.edit_message_text(
                f"❌ An error occurred: {error_message}\n\nPlease try again or contact support if the issue persists."
            )
        elif update and update.effective_message:
            await update.effective_message.reply_text(
                f"❌ An error occurred: {error_message}\n\nPlease try again or contact support if the issue persists."
            )
        else:
            logger.error(f"Could not send error message to user. Error: {error_message}")
    except Exception as e:
        logger.error(f"Error in error handler: {str(e)}")

async def query_derivative_position(client, market_id, subaccount_id):
    """Query the derivative position for a specific market and subaccount"""
    try:
        positions = await client.fetch_chain_subaccount_position_in_market(
            market_id=market_id,
            subaccount_id=subaccount_id
        )
        
        if positions:
            if 'state' in positions:
                if isinstance(positions['state'], dict) and 'state' in positions['state']:
                    return positions['state']['state']
                return positions['state']
        return None
    except Exception as e:
        debug_print(f"Error querying derivative position: {e}")
        return None

async def extract_borrow_rate_from_interest_model(interest_data):
    """Extract borrow rate from Neptune interest model response"""
    usdt_borrow_rate = 0
    
    if isinstance(interest_data, (int, float)) or (isinstance(interest_data, str) and interest_data.replace('.', '', 1).isdigit()):
        usdt_borrow_rate = float(interest_data) * 100
    elif isinstance(interest_data, dict):
        if 'borrow_rate' in interest_data:
            usdt_borrow_rate = float(interest_data['borrow_rate']) * 100
        elif 'rate' in interest_data:
            usdt_borrow_rate = float(interest_data['rate']) * 100
    
    return usdt_borrow_rate

async def query_borrow_rate(client, contract_address, asset_denom="peggy0xdAC17F958D2ee523a2206206994597C13D831ec7"):
    """Query the borrow rate for a specific asset (default is USDT)"""
    try:
        interest_query = f'{{"get_borrow_rate": {{"asset": {{"native_token": {{"denom": "{asset_denom}"}}}}}}}}'
        interest_data = await query_contract_state(client, contract_address, interest_query)
        usdt_borrow_rate = await extract_borrow_rate_from_interest_model(interest_data)
        return usdt_borrow_rate
    except Exception as e:
        try:
            interest_query = '{"get_all_borrow_rates": {}}'
            interest_data = await query_contract_state(client, contract_address, interest_query)
            
            if isinstance(interest_data, list):
                for rate_pair in interest_data:
                    if isinstance(rate_pair, list) and len(rate_pair) >= 2:
                        asset = rate_pair[0]
                        if (isinstance(asset, dict) and 'native_token' in asset and 
                            'denom' in asset['native_token'] and 
                            'peggy' in asset['native_token']['denom']):
                            rate_info = rate_pair[1]
                            if isinstance(rate_info, dict) and 'rate' in rate_info:
                                seconds_in_year = 365 * 24 * 60 * 60
                                usdt_borrow_rate = float(rate_info['rate']) * seconds_in_year * 100
                                return usdt_borrow_rate
        except Exception as e:
            pass
        return 0

async def query_funding_rate(client, market_id):
    """Query the funding rate for a specific market"""
    try:
        funding_rates = await client.fetch_funding_rates(market_id=market_id)
        
        if funding_rates and 'fundingRates' in funding_rates and len(funding_rates['fundingRates']) > 0:
            rates_to_average = min(24, len(funding_rates['fundingRates']))
            total_rate = 0
            count = 0
            
            for i in range(rates_to_average):
                rate_obj = funding_rates['fundingRates'][i]
                if 'rate' in rate_obj:
                    total_rate += float(rate_obj['rate'])
                    count += 1
            
            if count > 0:
                avg_hourly_rate = total_rate / count
                hours_in_year = 365 * 24
                annual_rate = avg_hourly_rate * hours_in_year * 100
                return annual_rate
        
        try:
            market_info = await client.fetch_derivative_market(market_id=market_id)
            
            if market_info and hasattr(market_info, 'market') and hasattr(market_info.market, 'perpetualMarketInfo'):
                perp_info = market_info.market.perpetualMarketInfo
                if hasattr(perp_info, 'hourlyFundingRateCap'):
                    hours_in_year = 365 * 24
                    cap_rate = float(perp_info.hourlyFundingRateCap)
                    estimated_rate = cap_rate * 0.2
                    annual_rate = estimated_rate * hours_in_year * 100
                    return annual_rate
        except Exception as e:
            pass
        
        return 0
    except Exception as e:
        return 0

async def query_funding_payments(client, market_ids, subaccount_id, limit=10):
    """Query the recent funding payments for a specific market and subaccount"""
    try:
        funding_payments = await client.fetch_funding_payments(
            market_ids=market_ids, 
            subaccount_id=subaccount_id
        )
        
        total_payments = 0
        payment_details = []
        
        if funding_payments and 'payments' in funding_payments and funding_payments['payments']:
            for payment in funding_payments['payments']:
                amount = float(payment['amount']) / 10**6
                
                from datetime import datetime
                timestamp = int(payment['timestamp']) / 1000
                date_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
                
                total_payments += amount
                payment_details.append({
                    'date': date_time,
                    'amount': amount,
                    'market_id': payment['marketId']
                })
        
        return total_payments, payment_details
    except Exception as e:
        return 0, []

async def query_derivative_market_data(client, market_id):
    """Query derivative market data to get cumulative funding information"""
    try:
        derivative_markets = await client.fetch_chain_derivative_markets(
            status="Active",
            market_ids=[market_id],
        )
        
        cumulative_funding = None
        mark_price = None
        
        if derivative_markets and 'markets' in derivative_markets and derivative_markets['markets']:
            for market_data in derivative_markets['markets']:
                if 'market' in market_data and market_data['market']['marketId'] == market_id:
                    if 'markPrice' in market_data:
                        mark_price = float(market_data['markPrice']) / 10**24
                    
                    if ('perpetualInfo' in market_data and 
                        'fundingInfo' in market_data['perpetualInfo'] and 
                        'cumulativeFunding' in market_data['perpetualInfo']['fundingInfo']):
                        raw_cumulative_funding = market_data['perpetualInfo']['fundingInfo']['cumulativeFunding']
                        cumulative_funding = float(raw_cumulative_funding) / 10**18
                        break
        
        return cumulative_funding, mark_price
    except Exception as e:
        return None, None

async def query_collateral_params(client, contract_address):
    """Query the Neptune market for collateral parameters"""
    try:
        collateral_query = '{"get_all_collaterals": {}}'
        collateral_data = await query_contract_state(client, contract_address, collateral_query)
        
        inj_liquidation_ltv = None
        inj_allowable_ltv = None
        
        if isinstance(collateral_data, list):
            for i in range(0, len(collateral_data)):
                asset_details_pair = collateral_data[i]
                
                if isinstance(asset_details_pair, list) and len(asset_details_pair) >= 2:
                    asset = asset_details_pair[0]
                    details = asset_details_pair[1]
                    
                    if (isinstance(asset, dict) and 'native_token' in asset and 
                        'denom' in asset['native_token'] and 
                        asset['native_token']['denom'] == 'inj'):
                        
                        if (isinstance(details, dict) and 'collateral_details' in details and 
                            'liquidation_ltv' in details['collateral_details'] and
                            'allowable_ltv' in details['collateral_details']):
                            
                            inj_liquidation_ltv = float(details['collateral_details']['liquidation_ltv'])
                            inj_allowable_ltv = float(details['collateral_details']['allowable_ltv'])
                            break
        
        return inj_liquidation_ltv, inj_allowable_ltv
    except Exception as e:
        return None, None

async def extract_usdt_debt(decoded_data):
    """Extract USDT debt amount from contract query response"""
    usdt_debt = 0
    
    if isinstance(decoded_data, list) and len(decoded_data) > 0:
        if isinstance(decoded_data[0], list) and len(decoded_data[0]) > 1:
            user_data = decoded_data[0][1]
            
            if 'debt_pool_accounts' in user_data:
                for pool in user_data['debt_pool_accounts']:
                    for i, entry in enumerate(pool):
                        if (isinstance(entry, dict) and 
                            'native_token' in entry and 
                            'denom' in entry['native_token'] and 
                            'peggy' in entry['native_token']['denom']):
                            
                            if i + 1 < len(pool) and isinstance(pool[i+1], dict) and 'principal' in pool[i+1]:
                                usdt_debt += float(pool[i+1]['principal']) / 10**6
    
    return usdt_debt

async def extract_account_health(health_data):
    """Extract account health metrics from query response"""
    health_factor = 0
    liquidation_threshold = 0
    
    if isinstance(health_data, str):
        try:
            health_factor = float(health_data.strip('"'))
            liquidation_threshold = 1.0
        except (ValueError, TypeError):
            pass
    elif isinstance(health_data, dict):
        if 'health_factor' in health_data:
            health_factor = float(health_data['health_factor'])
        
        if 'liquidation_threshold' in health_data:
            liquidation_threshold = float(health_data['liquidation_threshold'])
    
    return health_factor, liquidation_threshold

def debug_print(*args, **kwargs):
    """Print only if DEBUG mode is enabled"""
    # Check both the DEBUG flag and the environment variable
    if os.environ.get('INJECTIVE_DEBUG', '0') == '1':
        print(*args, **kwargs)

if __name__ == '__main__':
    # Check for existing bot instances
    try:
        application = Application.builder().token(TOKEN).build()
        print("Starting bot...")
        
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("invest", execute_strategy))
        application.add_handler(CommandHandler("close", close_strategy))
        application.add_handler(CallbackQueryHandler(button_click))
        application.add_error_handler(error_handler)
        
        print("Handlers registered")
        
        application.run_polling(drop_pending_updates=True)  # Add drop_pending_updates=True
    except Exception as e:
        print(f"Failed to start bot: {str(e)}")
        if "Conflict: terminated by other getUpdates request" in str(e):
            print("ERROR: Another bot instance is already running. Please stop it first.")