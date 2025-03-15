from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
from wallet_storage import save_wallet
import json
import logging
from pyngrok import ngrok
import os
from telegram import Bot
from telegram.error import TelegramError
import asyncio
from functools import partial
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Configure ngrok
ngrok_token = os.getenv('NGROK_AUTH_TOKEN')
if not ngrok_token:
    raise ValueError("Please set NGROK_AUTH_TOKEN environment variable")
ngrok.set_auth_token(ngrok_token)

app = Flask(__name__)
CORS(app)

# Initialize bot with your token
bot = Bot(token=os.getenv('TELEGRAM_BOT_TOKEN'))

def setup_ngrok():
    """Setup ngrok tunnel and return public URL"""
    try:
        # Kill any existing ngrok processes
        os.system('pkill ngrok')
        
        # Start new tunnel
        http_tunnel = ngrok.connect(5001, bind_tls=True)
        public_url = http_tunnel.public_url
        logger.info(f"ngrok tunnel created: {public_url}")
        
        # Store the public URL for use in bot.py
        with open('server_url.txt', 'w') as f:
            f.write(public_url)
            
        return public_url
    except Exception as e:
        logger.error(f"Error setting up ngrok: {str(e)}")
        raise

# Initialize ngrok when starting server
public_url = setup_ngrok()

@app.route('/')
def home():
    return send_file('connect.html')

@app.route('/connect-wallet', methods=['GET', 'POST'])
def connect_wallet():
    if request.method == 'GET':
        # Handle GET request - serve the connection page
        telegram_user_id = request.args.get('telegram_user_id')
        if not telegram_user_id:
            return "Missing telegram_user_id parameter", 400
        return send_file('connect.html')
    
    else:  # POST request
        # Handle POST request - process the wallet connection
        data = request.json
        logger.info(f"Received wallet connection request: {data}")
        wallet_address = data.get('wallet_address')
        telegram_user_id = data.get('telegram_user_id')
        
        logger.info(f"Processing wallet connection for address: {wallet_address}")
        
        if wallet_address and telegram_user_id:
            save_wallet(telegram_user_id, wallet_address)
            logger.info(f"Saved wallet connection for user {telegram_user_id}")
            return jsonify({'status': 'success'})
        logger.error("Missing required data for wallet connection")
        return jsonify({'status': 'error', 'message': 'Missing required data'}), 400

@app.route('/transaction')
def transaction():
    return send_file('transaction.html')

def send_telegram_message(chat_id: str, text: str):
    """Send message to Telegram using asyncio"""
    async def _send():
        try:
            await bot.send_message(chat_id=chat_id, text=text)
        except TelegramError as e:
            logger.error(f"Failed to send Telegram notification: {e}")

    # Get or create event loop
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    # Run the coroutine
    if loop.is_running():
        loop.call_soon_threadsafe(partial(asyncio.create_task, _send()))
    else:
        loop.run_until_complete(_send())

@app.route('/execute-transaction', methods=['POST'])
def execute_transaction():
    data = request.json
    logger.info(f"Received transaction execution request: {data}")
    try:
        transactions = data.get('transactions')
        user_id = data.get('user_id')
        
        if not transactions or not isinstance(transactions, list) or len(transactions) == 0:
            raise ValueError("Missing transaction data or signature")
        
        # Validate each transaction has required fields
        for tx in transactions:
            if not tx.get('typeUrl') or not tx.get('value'):
                raise ValueError("Invalid transaction format")
        
        # Log the transaction result
        logger.info(f"Executed transactions: {transactions}")
        
        # Notify user in Telegram
        if user_id:
            send_telegram_message(
                chat_id=user_id,
                text="✅ All transactions have been signed and broadcast successfully!"
            )
        
        return jsonify({
            'status': 'success',
            'transactions': transactions,
            'message': 'Transaction has been signed and broadcast'
        })
    except Exception as e:
        logger.error(f"Error executing transaction: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 400

if __name__ == '__main__':
    app.run(port=5001) 