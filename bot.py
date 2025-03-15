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
from wallet_storage import (
    is_wallet_connected, 
    get_wallet, 
    disconnect_wallet,
)
import signal
import aiohttp
from datetime import datetime, timedelta
import asyncio
import logging
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
        
        # Strategy A calculations
        neptune_borrow_rate = neptune_borrow.get('USDT', 0) / 100  # Convert percentage to decimal
        collateral_interest_rate = neptune_lend.get('INJ', 0) / 100
        strategy_borrowed_value = amount * avg_ltv
        amt_paid_neptune_interest = amount * neptune_borrow_rate
        funding_rate = helix_rates.get('INJ', {}).get('funding_rate', 0) * HOURS_PER_YEAR
        amt_earned_helix_funding = strategy_borrowed_value * implied_helix_leverage * funding_rate
        amt_earned_collateral = amount * collateral_interest_rate
        profits_a = amt_earned_helix_funding - amt_paid_neptune_interest + amt_earned_collateral
        apy_a = (profits_a / amount) * 100

        # Strategy B calculations
        strategy_supplied_value = -1 * amount
        neptune_borrow_rate_inj = neptune_borrow.get('INJ', 0) / 100
        collateral_interest_rate_usdt = neptune_lend.get('USDT', 0) / 100
        strategy_borrowed_value_b = strategy_supplied_value * avg_ltv
        amount_paid_neptune_interest = strategy_borrowed_value_b * neptune_borrow_rate_inj
        amount_earned_helix_funding_rates = strategy_supplied_value * implied_helix_leverage * funding_rate
        amount_earned_collateral = strategy_supplied_value * collateral_interest_rate_usdt
        profits_b = amount_earned_helix_funding_rates - amount_paid_neptune_interest + amount_earned_collateral
        apy_b = (profits_b / amount) * 100

        # Lending APY
        lending_apy = neptune_lend.get('USDT', 0)

        message += (
            "Available Opportunities:\n\n"
            f"Strategy A - Delta Neutral INJ Short:\n"
            f"• Expected APY: {apy_a:.2f}%\n"
            f"• Funding Rate (Annual): {funding_rate * 100:.4f}%\n"
            f"• Borrow Rate: {neptune_borrow_rate * 100:.2f}%\n"
            f"• Collateral Rate: {collateral_interest_rate * 100:.2f}%\n\n"
            f"Strategy B - Delta Neutral INJ Long:\n"
            f"• Expected APY: {apy_b:.2f}%\n"
            f"• Funding Rate (Annual): {funding_rate * 100:.4f}%\n"
            f"• Borrow Rate: {neptune_borrow_rate_inj * 100:.2f}%\n"
            f"• Collateral Rate: {collateral_interest_rate_usdt * 100:.2f}%\n\n"
            f"Lending Opportunity:\n"
            f"• Lending APY: {lending_apy:.2f}%\n"
            f"\n📊 *Recommendation*:\n"
        )
        
        # Determine the best strategy based on APY
        best_strategy = ""
        if apy_a >= apy_b and apy_a >= lending_apy:
            best_strategy = f"Strategy A (Delta Neutral INJ Short) with {apy_a:.2f}% APY"
        elif apy_b >= apy_a and apy_b >= lending_apy:
            best_strategy = f"Strategy B (Delta Neutral INJ Long) with {apy_b:.2f}% APY"
        else:
            best_strategy = f"Simple INJ Lending with {lending_apy:.2f}% APY"
        
        message += (
            f"Based on current market conditions, the best opportunity is *{best_strategy}*.\n\n"
            f"Always consider your risk tolerance and portfolio diversification when selecting a strategy."
        )

    except Exception as e:
        error_msg = f"Error fetching opportunities: {str(e)}"
        logger.error(error_msg)
        if update.callback_query:
            await update.callback_query.edit_message_text(text=error_msg)
        else:
            await update.message.reply_text(error_msg)
    
    # Check if user has already connected wallet
    if not is_wallet_connected(user_id):
        keyboard = [
            [InlineKeyboardButton("Connect Wallet", callback_data="connect_wallet")],
            [InlineKeyboardButton("Explain Strategy A", callback_data="explain_a")],
            [InlineKeyboardButton("Explain Strategy B", callback_data="explain_b")],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("Execute Strategy A", callback_data="execute_a")],
            [InlineKeyboardButton("Execute Strategy B", callback_data="execute_b")],
            [InlineKeyboardButton("Explain Strategy A", callback_data="explain_a")],
            [InlineKeyboardButton("Explain Strategy B", callback_data="explain_b")],
            [InlineKeyboardButton("Execute Lending", callback_data="execute_lending")],
            [InlineKeyboardButton("View Positions", callback_data="view_positions")],
            [InlineKeyboardButton("Change Strategy (Coming Soon)", callback_data="change_strategy")],
            [InlineKeyboardButton("Disconnect Wallet", callback_data="disconnect_wallet")]
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

async def show_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's positions and balances"""
    try:
        user_id = update.effective_user.id
        if not is_wallet_connected(user_id):
            await update.message.reply_text("Please connect your wallet first using /start")
            return
            
        # wallet_address = get_wallet(user_id)
        wallet_address = "inj142aemh62w2fpqjws0yre5936ts9x9e93fj8322"
        # Get positions and balances
        helix_positions = await get_helix_positions(wallet_address)
        neptune_positions = await get_neptune_positions(wallet_address)
        balances = await get_wallet_balances(wallet_address)
        
        print(f"Helix positions: {helix_positions}")
        print(f"Neptune positions: {neptune_positions}")
        print(f"Balances: {balances}")
        
        # Format positions message
        positions_msg = "Your Current Positions:\n\n"
        
        # Add Helix positions
        has_short_position = False
        if helix_positions:
            positions_msg += "\n📊 *Helix Positions:*\n"
            for pos in helix_positions:
                positions_msg += f"- {pos['market_id']}: {pos['type']} {pos['quantity']:.4f} @ {pos['entry_price']:.2f}\n"
                positions_msg += f"  Margin: {pos['margin']:.2f} USDT | Funding: {pos['funding']:.6f}\n"
                if pos['type'] == "SHORT":
                    has_short_position = True
        else:
            positions_msg += "\n🔄 No active Helix positions\n\n"
        
        # Add Neptune positions
        has_inj_lending = False
        if neptune_positions:
            positions_msg += "\n💰 Neptune Lending Positions:\n"
            for pos in neptune_positions:
                positions_msg += f"- {pos['type']} {pos['amount']} {pos['token']}\n"
                positions_msg += f"  Rate: {pos['rate']}%\n"
                if pos['token'] == 'INJ':
                    has_inj_lending = True
        else:
            positions_msg += "\nNo active Neptune positions\n\n"
        
        # Add balances
        if balances:
            positions_msg += "\n💵 Wallet Balances:\n"
            for token, amount in balances.items():
                positions_msg += f"{token}: {amount}\n"
        else:
            positions_msg += "\nNo balances found"
        
        # Add buttons for position actions including iAgent analysis
        keyboard = []
        
        # Add Close Strategy A button if we detect the relevant positions
        if has_short_position and has_inj_lending:
            keyboard.append([InlineKeyboardButton("Close Strategy A", callback_data="close_a")])
        
        keyboard.extend([
            [InlineKeyboardButton("Update Positions", callback_data="view_positions")],
            [InlineKeyboardButton("Back to Menu", callback_data="back_to_menu")],
            [InlineKeyboardButton("Analyze With iAgent", callback_data="analyze_positions")]
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Add a timestamp to ensure content is different on each refresh
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        positions_msg += f"\n\nLast updated: {timestamp}"
        
        if update.callback_query:
            await update.callback_query.edit_message_text(positions_msg, reply_markup=reply_markup)
        else:
            await update.message.reply_text(positions_msg, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error fetching positions: {str(e)}")
        error_message = f"Error fetching positions: {str(e)}"
        await update.callback_query.edit_message_text(
            text=error_message,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Back to Menu", callback_data="back_to_menu")
            ]])
        )

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button clicks from inline keyboards."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "connect_wallet":
        await connect_wallet_callback(update, context)
    elif query.data == "disconnect_wallet":
        disconnect_wallet(query.from_user.id)
        await query.edit_message_text("Wallet disconnected successfully!")
    elif query.data == "view_positions":
        await show_positions(update, context)
    elif query.data == "explain_a":
        await explain_strategy_a(update, context)
    elif query.data == "explain_b":
        await explain_strategy_b(update, context)
    elif query.data == "execute_a":
        await execute_a(update, context)
    elif query.data == "execute_b":
        await execute_b(update, context)
    elif query.data == "close_a":
        await close_strategy_a(update, context)
    elif query.data.startswith("invest_a_"):
        amount = query.data.split("_")[2]
        context.args = [amount]
        await strategy_a(update, context)
    elif query.data.startswith("invest_b_"):
        amount = query.data.split("_")[2]
        context.args = [amount]
        await strategy_b(update, context)
    elif query.data == "show_math_a":
        await show_strategy_a_math(update, context)
    elif query.data == "show_math_b":
        await show_strategy_b_math(update, context)
    elif query.data == "back_to_menu":
        await start(update, context)
    elif query.data == "cancel_strategy":
        await cancel_strategy(update, context)
    elif query.data == "analyze_positions":
        await analyze_with_iagent(update, context)
    else:
        await query.edit_message_text(f"Unsupported button: {query.data}")

async def strategy_a(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for Strategy A using direct private key signing."""
    try:
        # Check if this is a callback query or a direct command
        is_callback = hasattr(update, 'callback_query') and update.callback_query is not None
        message_obj = update.callback_query.message if is_callback else update.message
        
        if not context.args or len(context.args) == 0:
            reply_text = "Please specify an amount to invest. Usage: /invest_a <amount>"
            if is_callback:
                await update.callback_query.edit_message_text(reply_text)
            else:
                await message_obj.reply_text(reply_text)
            return
            
        amount = float(context.args[0])
        INJ_AMOUNT = int(amount * 1e18)  # Convert to INJ's smallest unit (18 decimals)
        
        # Initialize progress message
        progress_msg = await message_obj.reply_text(
            "🔄 Executing Strategy A...\n"
            "1. Setting up connection..."
        )
        
        # Setup client and account
        client, composer, network, priv_key, pub_key, address = await setup_client()
        
        await progress_msg.edit_text(
            progress_msg.text + "\n✅ Connection established\n"
            "2. Depositing INJ collateral..."
        )
        
        # 1. Deposit INJ collateral
        funds = [composer.coin(amount=INJ_AMOUNT, denom="inj")]
        deposit_msg = '{"deposit_collateral": {"account_index": 0}}'
        
        deposit_result = await execute_contract_tx(
            client, composer, network, priv_key, pub_key, address,
            NEPTUNE_MARKET_CONTRACT, deposit_msg, funds
        )
        
        if not deposit_result:
            await progress_msg.edit_text(
                progress_msg.text + "\n❌ Deposit failed. Operation aborted."
            )
            return
            
        await progress_msg.edit_text(
            progress_msg.text + "\n✅ INJ deposited\n"
            "3. Calculating borrow amount..."
        )
        
        # 2. Query collateral and prices
        user_query = f'{{"get_user_accounts": {{"addr": "{address.to_acc_bech32()}"}}}}'
        decoded_data = await query_contract_state(client, NEPTUNE_MARKET_CONTRACT, user_query)
        
        price_query = '{"get_prices": {"assets": [{"native_token": {"denom": "inj"}}, {"native_token": {"denom": "peggy0xdAC17F958D2ee523a2206206994597C13D831ec7"}}]}}'
        prices_data = await query_contract_state(client, NEPTUNE_ORACLE_ADDRESS, price_query)
        
        # Extract data from responses
        inj_collateral = await extract_inj_collateral(decoded_data)
        inj_price, usdt_price = await extract_prices(prices_data)
        
        # Calculate values for borrowing
        inj_collateral_value = inj_collateral * inj_price
        usdt_to_borrow = inj_collateral_value * 0.43  # Borrow 43% of collateral value
        usdt_to_borrow_amount = int(usdt_to_borrow * 10**6)  # Convert to USDT's smallest unit (6 decimals)
        
        await progress_msg.edit_text(
            progress_msg.text + f"\n✅ Calculations complete\n"
            f"   • INJ Collateral: {inj_collateral:.6f} INJ\n"
            f"   • INJ Price: ${inj_price:.2f}\n"
            f"   • USDT to Borrow: ${usdt_to_borrow:.2f}\n"
            "4. Borrowing USDT..."
        )
        
        # 3. Borrow USDT
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
            await progress_msg.edit_text(
                progress_msg.text + "\n❌ USDT borrow failed. Operation aborted."
            )
            return
            
        await progress_msg.edit_text(
            progress_msg.text + "\n✅ USDT borrowed\n"
            "5. Opening short position..."
        )
        
        # 4. Create derivative market order
        subaccount_id = address.get_subaccount_id(index=0)
        
        # Calculate order parameters
        inj_quantity = inj_collateral
        notional_value = inj_quantity * inj_price
        min_notional = MIN_NOTIONAL_SMALLEST_UNITS / 10**6
        
        # Adjust quantity if needed to meet minimum notional
        if notional_value < min_notional:
            inj_quantity = (MIN_NOTIONAL_SMALLEST_UNITS * 1.01) / 10**6 / inj_price
        
        # Convert to Decimal objects
        inj_quantity_decimal = Decimal(str(round(inj_quantity, 6)))
        inj_price_decimal = Decimal(str(round(inj_price, 6)))
        
        # Calculate leverage
        position_value = inj_collateral * inj_price
        margin_value = usdt_to_borrow
        dynamic_leverage = Decimal(str((position_value / margin_value)))
        
        # Create and execute the derivative market order
        order_result = await create_derivative_market_order(
            client, composer, network, priv_key, pub_key, address,
            INJ_MARKET_ID, subaccount_id, inj_price_decimal, inj_quantity_decimal, dynamic_leverage
        )
        
        if not order_result:
            await progress_msg.edit_text(
                progress_msg.text + "\n❌ Short position creation failed. Operation aborted."
            )
            return
        
        # Final success message
        final_message = (
            "✅ Strategy A executed successfully!\n\n"
            f"Position Details:\n"
            f"• INJ Collateral: {inj_collateral:.6f} INJ (${inj_collateral_value:.2f})\n"
            f"• USDT Borrowed: ${usdt_to_borrow:.2f}\n"
            f"• Short Position: {inj_quantity_decimal} INJ @ ${inj_price_decimal}\n"
            f"• Leverage: {dynamic_leverage:.2f}x\n\n"
            f"Use /view_positions to monitor your position."
        )
        
        keyboard = [
            [InlineKeyboardButton("View Positions", callback_data="view_positions")],
            [InlineKeyboardButton("Back to Menu", callback_data="start")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await progress_msg.edit_text(final_message, reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"Error in strategy_a: {str(e)}")
        error_message = f"❌ Error executing Strategy A: {str(e)}"
        if 'progress_msg' in locals():
            await progress_msg.edit_text(error_message)
        else:
            await message_obj.reply_text(error_message)

async def strategy_b(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_wallet_connected(user_id):
            await update.message.reply_text("Please connect your wallet first using /start")
            return
        
        wallet_address = get_wallet(user_id)
        amount = float(context.args[0])
        
        logger.info(f"Creating Strategy B transactions for wallet {wallet_address} with amount {amount}")

        prices = await client.fetch_derivative_mid_price_and_tob(
        market_id=INJ_PERP_MARKET_ID,
        )
        
        # Create transaction sequence for Strategy B
        tx_sequence = [
            {
            # 1. Lend UST on Neptune
            "typeUrl": "/injective.wasmx.v1beta1.MsgExecuteContractCompat",
            "value": {
                "sender": wallet_address,
                "contract": NEPTUNE_MARKET_CONTRACT,
                "msg": {
                    "lend": {}
                },
                "funds": str(int(amount * 1e6))+"peggy0xdAC17F958D2ee523a2206206994597C13D831ec7"
            }
            },
            {
            # 2. Deposit nUSDT as collateral on Neptune
            "typeUrl": "/injective.wasmx.v1.MsgExecuteContractCompat",
            "value": {
                "sender": wallet_address,
                "contract": NEPTUNE_MARKET_CONTRACT,
                "msg": {
                    "deposit_collateral": { 
                        "amount": str(int(amount * 1e6))+"peggy0xdAC17F958D2ee523a2206206994597C13D831ec7",
                        "asset_info": {
                            "native_token": {
                                "denom": "peggy0xdAC17F958D2ee523a2206206994597C13D831ec7"
                            }
                        }
                    }
                }
            }
            },
            {
            # 1. Borrow INJ from Neptune
            "typeUrl": "/injective.wasmx.v1.MsgExecuteContractCompat",
            "value": {
                "sender": wallet_address,
                "contract": NEPTUNE_MARKET_CONTRACT,
                "msg": {
                    "borrow": {
                        "account_index": 0,
                        "amount": str(int(amount * 1e18)),  # INJ has 18 decimals
                        "asset_info": {
                            "native_token": {
                                "denom": "inj"
                            }
                        },
                    }
                }
            }
        }, {
            # 2. Swap INJ to USDT on Helix
            "typeUrl": "/injective.exchange.v1beta1.MsgCreateSpotMarketOrder",
            "value": {
                "sender": wallet_address,
                "order": {
                    "market_id": INJ_PERP_MARKET_ID,
                    "order_info": {
                        "subaccount_id": get_subaccount_id(wallet_address),
                        "fee_recipient": wallet_address, # TODO: change to personal address to collect fees
                        "price": prices['midPrice'],
                        "quantity": str(int(amount * 1e18))
                    },
                    "order_type": "SELL",
                    "trigger_price": "0.000000000000000000"
                }
            }
        }, {
            # 3. Open Long position using swapped USDT
            "typeUrl": "/injective.exchange.v1beta1.MsgCreateDerivativeMarketOrder",
            "value": {
                "sender": wallet_address,
                "order": {
                    "market_id": INJ_PERP_MARKET_ID,
                    "order_info": {
                        "subaccount_id": get_subaccount_id(wallet_address),
                        "fee_recipient": wallet_address, # TODO: change to personal address to collect fees
                        "price": prices['bestBuyPrice'],
                        "quantity": str(int(amount * 1e6)) * 3, # 3x leverage
                    },
                    "order_type": "BUY",
                    "trigger_price": "0.000000000000000000"
                },
            }
        }]

        strategy_explanation = (
            "Strategy B - Delta Neutral INJ Long\n\n"
            "This will execute the following transactions:\n"
            "1. Lend USDT on Neptune\n"
            "2. Deposit nUSDT as collateral on Neptune\n"
            "3. Borrow INJ from Neptune\n"
            "4. Swap borrowed INJ to USDT\n"
            "5. Use USDT as collateral to open INJ long on Helix\n\n"
            f"Borrow Amount: {amount} INJ\n"
            f"Expected Position Size: {amount * 3} USDT worth of INJ\n"
            "Please review and confirm:"
        )
        
        tx_url = create_transaction_url(tx_sequence, user_id)
        keyboard = [
            [InlineKeyboardButton("Execute in Keplr", url=tx_url)],
            [InlineKeyboardButton("Cancel", callback_data="cancel_strategy")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(strategy_explanation, reply_markup=reply_markup)
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")

async def lend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle lending on Neptune"""
    try:
        # Get user info
        user_id = update.effective_user.id
        
        # Check if wallet is connected
        if not is_wallet_connected(user_id):
            await update.message.reply_text("Please connect your wallet first using /start")
            return
        
        wallet_address = get_wallet(user_id)
        if not context.args or len(context.args) == 0:
            await update.message.reply_text("Please specify an amount to lend. Usage: /lending <amount>")
            return
            
        amount = float(context.args[0])
        
        logger.info(f"Creating Lending transaction for wallet {wallet_address} with amount {amount}")
        
        amount_inj = int(amount * 1e18)  # Convert to atomic units with proper int conversion
        
        # Create proper JSON-encoded message (Not base64)
        lend_msg = json.dumps({"lend": {}})
        
        # Create a properly structured transaction following Injective SDK pattern
        transactions = [{
            "typeUrl": "/injective.wasmx.v1.MsgExecuteContractCompat",
            "value": {
                "sender": wallet_address,
                "contract": NEPTUNE_MARKET_CONTRACT,
                "msg": lend_msg,
                "funds": [
                    {
                        "denom": "inj",
                        "amount": str(amount_inj)
                    }
                ]
            }
        }]
        
        # Print transaction for debugging
        print(f"Lend transaction: {json.dumps(transactions, indent=2)}")
        
        data = {
            'transactions': transactions,
            'user_id': user_id
        }
        
        # URL encode the data
        encoded_data = quote(json.dumps(data))
        tx_url = f"{get_server_url()}/transaction?data={encoded_data}"
        
        keyboard = [
            [InlineKeyboardButton("Execute in Keplr", url=tx_url)],
            [InlineKeyboardButton("Cancel", callback_data="cancel_strategy")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"Please sign and execute Lending transaction in Keplr\nAmount: {amount} INJ",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error in lend command: {str(e)}")
        await update.message.reply_text(f"Error: {str(e)}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log the error and send a message to the user."""
    logger.error(f"Exception while handling an update: {context.error}")
    
    if update and update.effective_message:
        error_message = "Sorry, something went wrong. Please try again later."
        await update.effective_message.reply_text(error_message)

async def analyze_with_iagent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Analyze positions using iAgent"""
    try:
        user_id = update.effective_user.id
        if not is_wallet_connected(user_id):
            await update.callback_query.answer("Please connect your wallet first")
            return
            
        wallet_address = get_wallet(user_id)
        await update.callback_query.answer("Analyzing with iAgent...")
        
        # Get positions and market data
        helix_positions = await get_helix_positions(wallet_address)
        neptune_positions = await get_neptune_positions(wallet_address)
        helix_rates = get_helix_rates()
        neptune_borrow = get_neptune_borrow_rates()
        neptune_lend = get_neptune_lend_rates()
        
        # Prepare market data
        market_data = {
            'helix_rates': helix_rates,
            'neptune_borrow': neptune_borrow,
            'neptune_lend': neptune_lend
        }
        
        # Send to iAgent and get analysis
        analysis = await agent_client.analyze_positions(
            helix_positions, 
            neptune_positions, 
            market_data
        )
        
        # Send analysis to user
        keyboard = [
            [InlineKeyboardButton("Update Positions", callback_data="view_positions")],
            [InlineKeyboardButton("Back to Menu", callback_data="start")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            text=f"🔍 *iAgent Analysis*\n\n{analysis}",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error analyzing with iAgent: {str(e)}")
        await update.callback_query.edit_message_text(
            text=f"❌ Error: {str(e)}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Back to Positions", callback_data="view_positions")]
            ])
        )

async def explain_strategy_a(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Explain Strategy A to the user."""
    explanation = (
        "*Strategy A: Borrow → Short*\n\n"
        "This strategy involves borrowing INJ from Neptune Markets, selling it on Helix, "
        "and then placing a short position to profit from funding rates while remaining delta-neutral.\n\n"
        "Steps:\n"
        "1. Borrow INJ from Neptune Markets\n"
        "2. Sell the borrowed INJ on Helix\n"
        "3. Create a short position on INJ/USDT perpetual market\n"
        "4. Collect negative funding rates while maintaining delta neutrality\n\n"
        "To execute this strategy, use the /invest_a command followed by the amount you wish to invest."
    )
    
    keyboard = [
        [InlineKeyboardButton("Show Math", callback_data="show_math_a")],
        [InlineKeyboardButton("Execute Strategy A", callback_data="execute_a")],
        [InlineKeyboardButton("Back to Opportunities", callback_data="show_opportunities")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(explanation, reply_markup=reply_markup, parse_mode="HTML")

async def explain_strategy_b(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Explain Strategy B to the user."""
    explanation = (
        "<b>Strategy B: Lend → Borrow → Long</b>\n\n"
        "This strategy involves lending USDT to Neptune Markets, borrowing INJ, "
        "and then placing a long position on Helix to profit from funding rates while remaining delta-neutral.\n\n"
        "Steps:\n"
        "1. Lend USDT to Neptune Markets (earning interest)\n"
        "2. Use the nUSDT as collateral to borrow INJ\n"
        "3. Create a long position on INJ/USDT perpetual market\n"
        "4. Collect positive funding rates while maintaining delta neutrality\n\n"
        "To execute this strategy, use the /invest_b command followed by the amount you wish to invest."
    )
    
    keyboard = [
        [InlineKeyboardButton("Show Math", callback_data="show_math_b")],
        [InlineKeyboardButton("Execute Strategy B", callback_data="execute_b")],
        [InlineKeyboardButton("Back to Opportunities", callback_data="show_opportunities")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(explanation, reply_markup=reply_markup, parse_mode="HTML")

async def show_strategy_a_math(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the mathematical formulas behind Strategy A."""
    math_explanation = (
        "<b>Strategy A: Mathematical Breakdown</b>\n\n"
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
        [InlineKeyboardButton("Back to Strategy A", callback_data="explain_a")],
        [InlineKeyboardButton("Execute Strategy A", callback_data="execute_a")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(math_explanation, reply_markup=reply_markup, parse_mode="HTML")

async def show_strategy_b_math(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the mathematical formulas behind Strategy B."""
    math_explanation = (
        "<b>Strategy B: Mathematical Breakdown</b>\n\n"
        "This strategy profits from the spread between lending rates and borrowing costs while remaining delta neutral.\n\n"
        "<b>Key Variables:</b>\n"
        "• LF = Lent Funds (e.g., 100 USDT)\n"
        "• LR = Neptune Lending Rate (APY)\n"
        "• BF = Borrowed Funds (e.g., 1 INJ)\n"
        "• BR = Neptune Borrow Rate (APY)\n"
        "• FR = Helix Funding Rate (annual equivalent)\n"
        "• TPS = Total Position Size (typically 1x leverage)\n\n"
        
        "<b>Cost Structure:</b>\n"
        "Borrowing Cost (BC) = BF × BR\n\n"
        
        "<b>Revenue:</b>\n"
        "Lending Earnings (LE) = LF × LR\n"
        "Funding Rate Earnings (FRE) = TPS × Helix Funding Rate\n"
        "Total Revenue = LE + FRE\n\n"
        
        "<b>Profitability Formula:</b>\n"
        "Net APY = (LF × LR) + (TPS × Helix Funding Rate) - (BF × BR)\n\n"
        
        "<b>Risk Management:</b>\n"
        "• Delta Neutral: Long position matches borrowed amount\n"
        "• Liquidation Risk: Monitor collateral requirements on Neptune\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("Back to Strategy B", callback_data="explain_b")],
        [InlineKeyboardButton("Execute Strategy B", callback_data="execute_b")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(math_explanation, reply_markup=reply_markup, parse_mode="HTML")

async def execute_a(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt user for an amount to execute Strategy A."""
    user_id = update.effective_user.id
    
    # Check if wallet is connected
    if not is_wallet_connected(user_id):
        message = "Please connect your wallet first using /start"
        await update.callback_query.edit_message_text(message)
        return
    
    # Get market information for the message
    try:
        helix_rates = get_helix_rates()
        inj_data = helix_rates.get('INJ', {'open_interest': 0})
        open_interest = inj_data['open_interest']
        
    except Exception as e:
        logger.error(f"Error getting market data: {str(e)}")
    
    # Create a message asking for the amount
    message = (
        "<b>Strategy A: Amount Selection</b>\n\n"
        f"Current INJ/USDT open interest: ${open_interest:,}\n\n"
        "Enter the amount to invest in Strategy A:\n"
        f"Example: /invest_a 10\n\n"
        "Note: Amounts >5% of open interest may cause market imbalance."
    )
    
    # Create buttons to quickly execute with common amounts
    keyboard = [
        [
            InlineKeyboardButton(f"Invest 1 INJ", callback_data="invest_a_1"),
            InlineKeyboardButton(f"Invest 5 INJ", callback_data="invest_a_5"),
        ],
        [InlineKeyboardButton("Back", callback_data="explain_a")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        message, 
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def execute_b(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt user for an amount to execute Strategy B."""
    user_id = update.effective_user.id
    
    # Check if wallet is connected
    if not is_wallet_connected(user_id):
        message = "Please connect your wallet first using /start"
        await update.callback_query.edit_message_text(message)
        return
    
    # Get market information for the message
    try:
        helix_rates = get_helix_rates()
        inj_data = helix_rates.get('INJ', {'open_interest': 0})
        open_interest = inj_data['open_interest']
        
        # Suggest a reasonable default amount (1% of open interest)
    except Exception as e:
        logger.error(f"Error getting market data: {str(e)}")
        open_interest = "Unknown"
    
    # Create a message asking for the amount
    message = (
        "<b>Strategy B: Amount Selection</b>\n\n"
        f"Current INJ/USDT open interest: ${open_interest:,}\n\n"
        "Enter the amount to invest in Strategy B:\n"
        f"Example: /invest_b 20\n\n"
        "Note: Amounts >5% of open interest may cause market imbalance."
    )
    
    # Create buttons to quickly execute with common amounts
    keyboard = [
        [
            InlineKeyboardButton(f"Invest 1 INJ", callback_data="invest_b_1"),
            InlineKeyboardButton(f"Invest 5 INJ", callback_data="invest_b_5"),
        ]
        [InlineKeyboardButton("Back", callback_data="explain_b")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        message, 
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def setup_client():
    """Initialize client and account"""
    # Load private key from .env file
    configured_private_key = os.getenv("INJECTIVE_PRIVATE_KEY")
    
    # Initialize network and client
    network = Network.mainnet()
    client = AsyncClient(network)
    composer = await client.composer()
    await client.sync_timeout_height()
    
    # Load account
    priv_key = PrivateKey.from_hex(configured_private_key)
    pub_key = priv_key.to_public_key()
    address = pub_key.to_address()
    await client.fetch_account(address.to_acc_bech32())
    
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
                                      market_id, subaccount_id, price, quantity, leverage, order_type="SELL"):
    """Create a derivative market order"""
    # Always refresh account information to get the latest sequence number
    await client.fetch_account(address.to_acc_bech32())
    await client.sync_timeout_height()
    
    # Prepare order message
    msg = composer.msg_create_derivative_market_order(
        sender=address.to_acc_bech32(),
        market_id=market_id,
        subaccount_id=subaccount_id,
        fee_recipient=FEE_RECIPIENT,
        price=price,
        quantity=quantity,
        margin=composer.calculate_margin(
            quantity=quantity, 
            price=price, 
            leverage=leverage, 
            is_reduce_only=False
        ),
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

async def close_strategy_a(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Close Strategy A positions using direct private key signing."""
    try:
        # Check if this is a callback query or a direct command
        is_callback = hasattr(update, 'callback_query') and update.callback_query is not None
        message_obj = update.callback_query.message if is_callback else update.message
        
        # Initialize progress message
        progress_msg = await message_obj.reply_text(
            "🔄 Closing Strategy A positions...\n"
            "1. Setting up connection..."
        )
        
        # Setup client and account
        client, composer, network, priv_key, pub_key, address = await setup_client()
        subaccount_id = address.get_subaccount_id(index=0)
        market_id = INJ_MARKET_ID
        
        await progress_msg.edit_text(
            progress_msg.text + "\n✅ Connection established\n"
            "2. Closing Helix position..."
        )
        
        # 1. Close Helix position
        helix_result = await close_helix_position(
            client, composer, address, subaccount_id, market_id, 
            network, priv_key, pub_key
        )
        
        if not helix_result:
            await progress_msg.edit_text(
                progress_msg.text + "\n❌ Failed to close Helix position. Operation aborted."
            )
            return
            
        await progress_msg.edit_text(
            progress_msg.text + "\n✅ Helix position closed\n"
            "3. Checking Neptune positions..."
        )
        
        # 2. Query user account state
        user_query_data = json.dumps({"get_user_accounts": {"addr": address.to_acc_bech32()}})
        market_state = await query_market_state(client, NEPTUNE_MARKET_CONTRACT, user_query_data)
        
        # 3. Handle USDT debt repayment
        if "debt" in market_state and "peggy0xdAC17F958D2ee523a2206206994597C13D831ec7" in market_state["debt"]:
            await progress_msg.edit_text(
                progress_msg.text + "\n✅ Found USDT debt\n"
                "4. Repaying USDT debt..."
            )
            
            user_usdt_debt = market_state["debt"]["peggy0xdAC17F958D2ee523a2206206994597C13D831ec7"]
            user_shares = int(user_usdt_debt["shares"])
            user_actual_debt = int(user_usdt_debt["principal"])
            
            if user_shares > 0 and user_actual_debt > 0:
                msg = '{"return": {"account_index": 0}}'
                repay_result = await execute_contract(
                    msg, user_usdt_debt, client, composer, address, 
                    network, priv_key, pub_key, user_actual_debt
                )
                
                if not repay_result:
                    await progress_msg.edit_text(
                        progress_msg.text + "\n❌ Failed to repay USDT debt. Operation aborted."
                    )
                    return
                
                # Verify debt repayment
                await asyncio.sleep(3)
                updated_market_state = await query_market_state(client, NEPTUNE_MARKET_CONTRACT, user_query_data)
                
                if ("debt" in updated_market_state and 
                    "peggy0xdAC17F958D2ee523a2206206994597C13D831ec7" in updated_market_state["debt"]):
                    updated_debt = updated_market_state["debt"]["peggy0xdAC17F958D2ee523a2206206994597C13D831ec7"]
                    updated_shares = int(updated_debt.get("shares", "0"))
                    updated_principal = int(updated_debt.get("principal", "0"))
                    
                    if updated_shares > 0 or updated_principal > 0:
                        if updated_principal <= 10:
                            await progress_msg.edit_text(
                                progress_msg.text + f"\n⚠️ Tiny debt remaining: {updated_principal} USDT\n"
                                "5. Attempting final repayment..."
                            )
                            
                            msg = '{"return": {"account_index": 0}}'
                            await execute_contract(
                                msg, updated_debt, client, composer, address, 
                                network, priv_key, pub_key, updated_principal
                            )
                            await asyncio.sleep(3)
                
                await progress_msg.edit_text(
                    progress_msg.text + "\n✅ USDT debt repaid\n"
                    "6. Withdrawing collateral..."
                )
                market_state = updated_market_state
        
        # 4. Withdraw INJ collateral
        if "collateral" in market_state and "inj" in market_state["collateral"]:
            inj_collateral = market_state["collateral"]["inj"]
            inj_shares = int(inj_collateral["principal"])
            
            if inj_shares > 0:
                withdraw_result = await withdraw_collateral(
                    client, composer, address, network, priv_key, pub_key, 
                    "inj", inj_shares
                )
                
                if not withdraw_result or withdraw_result.get("txResponse", {}).get("code", -1) != 0:
                    await progress_msg.edit_text(
                        progress_msg.text + "\n❌ Failed to withdraw INJ collateral."
                    )
                    return
        
        # Final success message
        final_message = (
            "✅ Strategy A positions closed successfully!\n\n"
            "Summary:\n"
            "• Helix short position closed\n"
            "• USDT debt repaid\n"
            "• INJ collateral withdrawn\n\n"
            "Use /view_positions to verify all positions are closed."
        )
        
        keyboard = [
            [InlineKeyboardButton("View Positions", callback_data="view_positions")],
            [InlineKeyboardButton("Back to Menu", callback_data="start")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await progress_msg.edit_text(final_message, reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"Error in close_strategy_a: {str(e)}")
        error_message = f"❌ Error closing Strategy A positions: {str(e)}"
        if 'progress_msg' in locals():
            await progress_msg.edit_text(error_message)
        else:
            await message_obj.reply_text(error_message)

async def connect_wallet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle wallet connection callback"""
    user_id = update.effective_user.id
    
    # Create a unique session ID for this connection attempt
    session_id = str(uuid.uuid4())
    
    # Get server URL
    server_url = get_server_url()
    
    # Create connection URL with correct endpoint and parameters
    connection_url = f"{server_url}/connect-wallet?telegram_user_id={user_id}"
    
    # Create inline keyboard with connection button
    keyboard = [
        [InlineKeyboardButton("Connect with Keplr", url=connection_url)],
        [InlineKeyboardButton("Cancel", callback_data="cancel_connect")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Send message with connection button
    await update.callback_query.edit_message_text(
        text="Click below to connect your Keplr wallet:",
        reply_markup=reply_markup
    )

if __name__ == '__main__':
    application = Application.builder().token(TOKEN).build()
    print("Starting bot...")
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("invest_a", strategy_a))
    application.add_handler(CommandHandler("invest_b", strategy_b))
    application.add_handler(CommandHandler("lend", lend))
    application.add_handler(CommandHandler("close_a", close_strategy_a))
    application.add_handler(CallbackQueryHandler(button_click))
    application.add_error_handler(error_handler)
    
    print("Handlers registered")
    
    application.run_polling() 