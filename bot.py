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

# Contract addresses and network config
NEPTUNE_LENDING_CONTRACT="inj1xemdknj74p3qsgxs47n9c7e4u2wnxc0cpv3dyz"
HELIX_MARKET_CONTRACT="inj1q8qk6c7n44gf4e6jlhpvpwujdz0qm5hc4vuwhs"
HELIX_MARKET_ID= "0x" + "0611780ba69656949525013d947713937e9171af326052c86f471ddbb759c747" 

# Add new constants for close commands
CLOSE_COMMANDS = {
    'a': '/close_a',
    'b': '/close_b',
    'lend': '/close_lending'
}

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
    await update.message.reply_text("Hello! How can I help you?", reply_markup=reply_markup)

async def get_wallet_balances(wallet_address: str) -> dict:
    """Get INJ and USDT balances for a wallet"""
    try:
        network = Network.mainnet()
        client = AsyncClient(network)
        
        # Get bank balances
        bank_balances = await client.fetch_bank_balances(wallet_address)
        
        print(f"Raw bank balances response: {bank_balances}")  # Debug log
        
        balances = {
            'INJ': '0',
            'USDT': '0'
        }
        
        # Parse balances
        for balance in bank_balances.get('balances', []):
            if balance.get('denom') == 'inj':
                balances['INJ'] = str(float(balance.get('amount', '0')) / 1e18)  # Convert from wei
            elif balance.get('denom') == 'peggy0xdAC17F958D2ee523a2206206994597C13D831ec7':
                balances['USDT'] = str(float(balance.get('amount', '0')) / 1e6)  # Convert from micro
                
        return balances
    except Exception as e:
        logger.error(f"Error fetching balances: {str(e)}")
        raise

async def get_helix_positions(wallet_address: str) -> list:
    """Get open Helix positions for a wallet"""
    try:
        network = Network.mainnet()
        client = AsyncClient(network)
        
        # Convert Injective address to subaccount ID
        subaccount_id = address_to_subaccount_id(wallet_address)
        
        # Get positions from Helix contract
        positions = await client.fetch_derivative_positions_v2(
            market_ids=[HELIX_MARKET_ID],
            subaccount_id=subaccount_id,
            subaccount_total_positions=True
        )
        
        formatted_positions = []
        for pos in positions.get('positions', []):
            formatted_positions.append({
                'type': 'LONG' if pos['position_type'] == 'LONG' else 'SHORT',
                'size': str(float(pos['quantity']) / 1e6),
                'margin': str(float(pos['margin']) / 1e6),
                'leverage': str(float(pos['effective_leverage']))
            })
            
        return formatted_positions
    except Exception as e:
        logger.error(f"Error fetching Helix positions: {str(e)}")
        raise

async def get_neptune_positions(wallet_address: str) -> list:
    """Get Neptune lending positions"""
    try:
        network = Network.mainnet()
        client = AsyncClient(network)
        
        # Create raw query string
        query_data = f'{{"user_positions":{{"user":"{wallet_address}"}}}}'
        
        # Query Neptune contract for lending positions using smart contract state
        response = await client.fetch_smart_contract_state(
            address=NEPTUNE_LENDING_CONTRACT,
            query_data=query_data
        )
        
        positions = []
        if response and 'data' in response:
            data = json.loads(response['data'])
            for position in data.get('user_positions', []):  # Updated response field
                positions.append({
                    'type': 'Lending',
                    'amount': str(float(position['amount']) / 1e6),
                    'apy': position.get('apy', 'N/A')
                })
                
        return positions
    except Exception as e:
        logger.error(f"Error fetching Neptune positions: {str(e)}")
        raise

async def show_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's positions and balances"""
    try:
        user_id = update.effective_user.id
        if not is_wallet_connected(user_id):
            await update.message.reply_text("Please connect your wallet first using /start")
            return
            
        wallet_address = get_wallet(user_id)
        
        # Get positions and balances
        helix_positions = await get_helix_positions(wallet_address)
        # neptune_positions = await get_neptune_positions(wallet_address)
        balances = await get_wallet_balances(wallet_address)
        
        # Format positions message
        positions_msg = "Your Current Positions:\n\n"
        
        # Add Helix positions
        if helix_positions:
            positions_msg += "🔄 Helix Positions:\n"
            for pos in helix_positions:
                positions_msg += (
                    f"Type: {pos['type']}\n"
                    f"Size: {pos['size']} USDT\n"
                    f"Margin: {pos['margin']} USDT\n"
                    f"Leverage: {pos['leverage']}x\n\n"
                )
        
        # # Add Neptune positions
        # if neptune_positions:
        #     positions_msg += "💰 Neptune Lending Positions:\n"
        #     for pos in neptune_positions:
        #         positions_msg += (
        #             f"Amount: {pos['amount']} USDT\n"
        #             f"APY: {pos['apy']}%\n\n"
        #         )
        
        # Add balances
        positions_msg += "💼 Wallet Balances:\n"
        positions_msg += f"INJ: {balances['INJ']}\n"
        positions_msg += f"USDT: {balances['USDT']}\n"
        
        # Add refresh button
        keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="refresh_positions")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.edit_message_text(positions_msg, reply_markup=reply_markup)
        else:
            await update.message.reply_text(positions_msg, reply_markup=reply_markup)
            
    except Exception as e:
        error_msg = f"Error fetching positions: {str(e)}"
        logger.error(error_msg)
        if update.callback_query:
            await update.callback_query.edit_message_text(error_msg)
        else:
            await update.message.reply_text(error_msg)

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
        amount_paid_neptune_interest = strategy_borrowed_value_b * -1 * neptune_borrow_rate_inj
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

async def strategy_a(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not is_wallet_connected(user_id):
            await update.message.reply_text("Please connect your wallet first using /start")
            return
        
        wallet_address = get_wallet(user_id)
        amount = float(context.args[0])
        
        logger.info(f"Creating Strategy A transactions for wallet {wallet_address} with amount {amount}")
        
        # Create transaction sequence for Strategy A
        tx_sequence = [{
            # 1. Borrow USDT from Neptune
            "typeUrl": "/cosmwasm.wasm.v1.MsgExecuteContract",
            "value": {
                "sender": wallet_address,
                "contract": NEPTUNE_LENDING_CONTRACT,
                "msg": {
                    "borrow": {
                        "asset": {
                            "native_token": {
                                "denom": "peggy0xdAC17F958D2ee523a2206206994597C13D831ec7"
                            }
                        },
                        "amount": str(int(amount * 1e6))
                    }
                }
            }
        }, {
            # 2. Open Short position on Helix using borrowed USDT
            "typeUrl": "/cosmwasm.wasm.v1.MsgExecuteContract",
            "value": {
                "sender": wallet_address,
                "contract": HELIX_MARKET_CONTRACT,
                "msg": {
                    "open_position": {
                        "position_type": "SHORT",
                        "market_id": HELIX_MARKET_ID,
                        "margin_amount": str(int(amount * 1e6)),
                        "leverage": "5"
                    }
                },
                "funds": [{
                    "denom": "peggy0xdAC17F958D2ee523a2206206994597C13D831ec7",
                    "amount": str(int(amount * 1e6))
                }]
            }
        }]

        strategy_explanation = (
            "Strategy A - Delta Neutral INJ Short\n\n"
            "This will execute the following transactions:\n"
            "1. Borrow USDT from Neptune lending\n"
            "2. Use borrowed USDT as collateral on Helix\n"
            "3. Open an INJ short position on Helix\n\n"
            f"Borrow Amount: {amount} USDT\n"
            f"Position Size: {amount * 5} USDT worth of INJ\n"
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
        
        # Create transaction sequence for Strategy B
        tx_sequence = [{
            # 1. Borrow INJ from Neptune
            "typeUrl": "/cosmwasm.wasm.v1.MsgExecuteContract",
            "value": {
                "sender": wallet_address,
                "contract": NEPTUNE_LENDING_CONTRACT,
                "msg": {
                    "borrow": {
                        "asset": {
                            "native_token": {
                                "denom": "inj"
                            }
                        },
                        "amount": str(int(amount * 1e18))  # INJ has 18 decimals
                    }
                }
            }
        }, {
            # 2. Swap INJ to USDT on Helix
            "typeUrl": "/cosmwasm.wasm.v1.MsgExecuteContract",
            "value": {
                "sender": wallet_address,
                "contract": HELIX_MARKET_CONTRACT,
                "msg": {
                    "swap_exact_in": {
                        "input_coin": {
                            "denom": "inj",
                            "amount": str(int(amount * 1e18))
                        },
                        "output_denom": "peggy0xdAC17F958D2ee523a2206206994597C13D831ec7",
                        "slippage": "0.01"  # 1% slippage
                    }
                },
                "funds": [{
                    "denom": "inj",
                    "amount": str(int(amount * 1e18))
                }]
            }
        }, {
            # 3. Open Long position using swapped USDT
            "typeUrl": "/cosmwasm.wasm.v1.MsgExecuteContract",
            "value": {
                "sender": wallet_address,
                "contract": HELIX_MARKET_CONTRACT,
                "msg": {
                    "open_position": {
                        "position_type": "LONG",
                        "market_id": HELIX_MARKET_ID,
                        "margin_amount": str(int(amount * 1e6)),
                        "leverage": "5"
                    }
                },
                "funds": [{
                    "denom": "peggy0xdAC17F958D2ee523a2206206994597C13D831ec7",
                    "amount": str(int(amount * 1e6))
                }]
            }
        }]

        strategy_explanation = (
            "Strategy B - Delta Neutral INJ Long\n\n"
            "This will execute the following transactions:\n"
            "1. Borrow INJ from Neptune lending\n"
            "2. Swap borrowed INJ to USDT\n"
            "3. Use USDT as collateral to open INJ long on Helix\n\n"
            f"Borrow Amount: {amount} INJ\n"
            f"Expected Position Size: {amount * 5} USDT worth of INJ\n"
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
    try:
        user_id = update.effective_user.id
        if not is_wallet_connected(user_id):
            await update.message.reply_text("Please connect your wallet first using /start")
            return
        
        wallet_address = get_wallet(user_id)
        if not context.args or len(context.args) == 0:
            await update.message.reply_text("Please specify an amount to lend. Usage: /lending <amount>")
            return
        
        amount = float(context.args[0])
        
        logger.info(f"Creating Lending transaction for wallet {wallet_address} with amount {amount}")
        
        transactions = [{
            "typeUrl": "/cosmwasm.wasm.v1.MsgExecuteContract",
            "value": {
                "sender": wallet_address,
                "contract": NEPTUNE_LENDING_CONTRACT,
                "msg": {
                    "lend": {
                        "amount": str(int(amount * 1e6))
                    }
                },
                "funds": [{
                    "denom": "peggy0xdAC17F958D2ee523a2206206994597C13D831ec7",
                    "amount": str(int(amount * 1e6))
                }]
            }
        }]
        
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
            f"Please sign and execute Lending transaction in Keplr\nAmount: {amount} USDT",
            reply_markup=reply_markup
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log the error and send a message to the user."""
    logger.error(f"Exception while handling an update: {context.error}")
    
    if update and update.effective_message:
        error_message = "Sorry, something went wrong. Please try again later."
        await update.effective_message.reply_text(error_message)

def address_to_subaccount_id(address: str) -> str:
    """Convert Injective address to subaccount ID"""
    # Decode bech32 address
    _, data = bech32_decode(address)
    if data is None:
        raise ValueError(f"Invalid address: {address}")
    
    # Convert 5-bit to 8-bit encoding
    data = convertbits(data, 5, 8, False)
    if data is None:
        raise ValueError(f"Could not convert address: {address}")
    
    # Convert to hex and pad to 64 characters
    hex_address = ''.join(f'{x:02x}' for x in data)
    padded_hex = hex_address.ljust(64, '0')
    
    return f"0x{padded_hex}"

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