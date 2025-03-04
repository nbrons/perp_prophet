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
from pyinjective.core.network import Network  # Add back for positions
from pyinjective.async_client import AsyncClient  # Add back for positions
from eth_utils import remove_0x_prefix
from bech32 import bech32_decode, convertbits
import base64
import requests
from agent_client import AgentClient

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
NEPTUNE_MARKET_CONTRACT="inj1nc7gjkf2mhp34a6gquhurg8qahnw5kxs5u3s4u"
HELIX_MARKET_CONTRACT="inj1q8qk6c7n44gf4e6jlhpvpwujdz0qm5hc4vuwhs"
INJ_PERP_MARKET_ID="0x9b9980167ecc3645ff1a5517886652d94a0825e54a77d2057cbbe3ebee015963"

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
    
    # Check if user has already connected wallet
    if not is_wallet_connected(user_id):
        keyboard = [
            [InlineKeyboardButton("Connect Wallet", callback_data="connect_wallet")],
            [InlineKeyboardButton("What opportunities are available today?", callback_data="opportunities")]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("View Positions", callback_data="view_positions")],
            [InlineKeyboardButton("What opportunities are available today?", callback_data="opportunities")],
            [InlineKeyboardButton("Disconnect Wallet", callback_data="disconnect_wallet")]
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text("Hello! How can I help you?", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Hello! How can I help you?", reply_markup=reply_markup)


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
            
        wallet_address = get_wallet(user_id)
        # wallet_address = "inj142aemh62w2fpqjws0yre5936ts9x9e93fj8322"
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
        if helix_positions:
            positions_msg += "\n📊 *Helix Positions:*\n"
            for pos in helix_positions:
                positions_msg += f"- {pos['market_id']}: {pos['type']} {pos['quantity']:.4f} @ {pos['entry_price']:.2f}\n"
                positions_msg += f"  Margin: {pos['margin']:.2f} USDT | Funding: {pos['funding']:.6f}\n"
        else:
            positions_msg += "\n🔄 No active Helix positions\n\n"
        
        # Add Neptune positions
        if neptune_positions:
            positions_msg += "\n💰 Neptune Lending Positions:\n"
            for pos in neptune_positions:
                positions_msg += f"- {pos['type']} {pos['amount']} {pos['token']}\n"
                positions_msg += f"  Rate: {pos['rate']}%\n"
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
        keyboard = [
            [InlineKeyboardButton("Update Positions", callback_data="view_positions")],
            [InlineKeyboardButton("Back to Menu", callback_data="back_to_menu")],
            [InlineKeyboardButton("Analyze With iAgent", callback_data="analyze_positions")]
        ]
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

async def show_opportunities(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

        message = (
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

        # Create buttons for each strategy
        keyboard = [
            [InlineKeyboardButton("Execute Strategy A", callback_data="execute_a")],
            [InlineKeyboardButton("Execute Strategy B", callback_data="execute_b")],
            [InlineKeyboardButton("Execute Lending", callback_data="execute_lending")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await update.callback_query.edit_message_text(text=message, reply_markup=reply_markup)
        else:
            await update.message.reply_text(text=message, reply_markup=reply_markup)

    except Exception as e:
        error_msg = f"Error fetching opportunities: {str(e)}"
        logger.error(error_msg)
        if update.callback_query:
            await update.callback_query.edit_message_text(text=error_msg)
        else:
            await update.message.reply_text(error_msg)

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button clicks"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "connect_wallet":
        # Create connect wallet URL
        base_url = get_server_url()
        connect_url = f"{base_url}?telegram_user_id={query.from_user.id}"
        
        keyboard = [[InlineKeyboardButton("Connect in Browser", url=connect_url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text="Please connect your wallet using the button below:",
            reply_markup=reply_markup
        )
    elif query.data == "disconnect_wallet":
        disconnect_wallet(query.from_user.id)
        await query.edit_message_text("Wallet disconnected successfully!")
    elif query.data == "opportunities":
        await show_opportunities(update, context)
    elif query.data == "view_positions":
        await show_positions(update, context)
    elif query.data == "back_to_menu":
        # Back to menu - call the same function as /start
        await start(update, context)
    elif query.data in ["execute_a", "execute_b", "execute_lending"]:
        strategy = {
            "execute_a": "Strategy A",
            "execute_b": "Strategy B",
            "execute_lending": "Lending"
        }[query.data]
        
        user_id = update.effective_user.id
        
        if not is_wallet_connected(user_id):
            await query.edit_message_text("Please connect your wallet first using /start")
            return
        
        if strategy == "Lending":
            message = (
                "Enter amount to lend:\n"
                "Use /lend <amount>\n\n"
            )
        else:
            helix_rates = get_helix_rates()
            inj_data = helix_rates.get('INJ', {'open_interest': 0})
            open_interest = inj_data['open_interest']
            
            message = (
                f"Enter amount for {strategy}:\n"
                f"Use /invest_{strategy[-1].lower()} <amount>\n\n"
                f"Note: Current open interest is ${open_interest:,.2f}\n"
                "Amounts >10% of open interest may cause market imbalance."
            )
        
        await query.edit_message_text(text=message)
    elif query.data == "refresh_positions":
        await show_positions(update, context)
    elif query.data == "analyze_positions":
        await analyze_with_iagent(update, context)

async def strategy_a(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_wallet_connected(user_id):
            await update.message.reply_text("Please connect your wallet first using /start")
            return
        
        wallet_address = get_wallet(user_id)
        amount = float(context.args[0])
        
        logger.info(f"Creating Strategy A transactions for wallet {wallet_address} with amount {amount}")

        prices = await client.fetch_derivative_mid_price_and_tob(
        market_id=INJ_PERP_MARKET_ID,
        )

        amount_inj = amount * float(prices['midPrice'])
        # Create transaction sequence for Strategy A
        tx_sequence = [
            {
            # 1. Lend INJ on Neptune
            "typeUrl": "/cosmwasm.wasm.v1.MsgExecuteContract",
            "value": {
                "sender": wallet_address,
                "contract": NEPTUNE_MARKET_CONTRACT,
                "msg": {
                    "lend": {}
                },
                "funds": str(int(amount_inj))+"inj"
            }
            },
            {
            # 2. Deposit INJ as collateral on Neptune
            "typeUrl": "/cosmwasm.wasm.v1.MsgExecuteContract",
            "value": {
                "sender": wallet_address,
                "contract": NEPTUNE_MARKET_CONTRACT,
                "msg": {
                    "deposit_collateral": {
                        "amount": str(int(amount_inj))+"inj",
                        "asset_info": {
                            "native_token": {
                                "denom": "inj"
                            }
                        }
                    }
                }
            }
            },
            {
            # 1. Borrow USDT from Neptune
            "typeUrl": "/cosmwasm.wasm.v1.MsgExecuteContract",
            "value": {
                "sender": wallet_address,
                "contract": NEPTUNE_MARKET_CONTRACT,
                "msg": {
                    "borrow": {
                        "account_index": 0,
                        "amount": str(int(amount * 1e6)),
                        "asset_info": {
                            "native_token": {
                                "denom": "peggy0xdAC17F958D2ee523a2206206994597C13D831ec7"
                            }
                        }
                    }
                }
            }
        }, {
            # 2. Open Short position on Helix using borrowed USDT
            "typeUrl": "/cosmwasm.wasm.v1.MsgCreateDerivativeMarketOrder",
            "value": {
                "sender": wallet_address,
                "order": {
                    "market_id": INJ_PERP_MARKET_ID,
                    "order_info": {
                        "subaccount_id": get_subaccount_id(wallet_address),
                        "fee_recipient": wallet_address, # TODO: change to personal address to collect fees
                        "price": prices['bestSellPrice'],
                        "quantity": str(int(amount * 1e6)) * 3, # 3x leverage
                    },
                    "order_type": "SELL",
                    "trigger_price": "0.000000000000000000"
                },
            }
        }]
        
        strategy_explanation = (
            "Strategy A - Delta Neutral INJ Short\n\n"
            "This will execute the following transactions:\n"
            "1. Lend INJ on Neptune\n"
            "2. Deposit nINJ as collateral on Neptune\n"
            "3. Borrow USDT from Neptune lending\n"
            "4. Use borrowed USDT as collateral on Helix\n"
            "5. Open an INJ short position on Helix\n\n"
            f"Borrow Amount: {amount} USDT\n"
            f"Position Size: {amount * 3} USDT worth of INJ\n"
            "Please review and confirm:"
        )
        
        tx_url = create_transaction_url(tx_sequence, user_id)
        keyboard = [
            [InlineKeyboardButton("Execute in Keplr", url=tx_url)],
            [InlineKeyboardButton("Cancel", callback_data="cancel_strategy")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(strategy_explanation, reply_markup=reply_markup)
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")

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
            "typeUrl": "/cosmwasm.wasm.v1.MsgExecuteContract",
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
            "typeUrl": "/cosmwasm.wasm.v1.MsgExecuteContract",
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
            "typeUrl": "/cosmwasm.wasm.v1.MsgExecuteContract",
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
            "typeUrl": "/cosmwasm.wasm.v1.MsgCreateSpotMarketOrder",
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
            "typeUrl": "/cosmwasm.wasm.v1.MsgCreateDerivativeMarketOrder",
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
            [InlineKeyboardButton("Cancel", callback_data="cancel_strategy")]
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
                "funds": str(amount_inj)+"inj"
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
            [InlineKeyboardButton("Back to Menu", callback_data="back_to_menu")]
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

if __name__ == '__main__':
    application = Application.builder().token(TOKEN).build()
    print("Starting bot...")
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("invest_a", strategy_a))
    application.add_handler(CommandHandler("invest_b", strategy_b))
    application.add_handler(CommandHandler("lend", lend))
    application.add_handler(CallbackQueryHandler(button_click))
    application.add_error_handler(error_handler)
    
    print("Handlers registered")
    
    application.run_polling() 