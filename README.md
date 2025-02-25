## Setup
1. Copy .env.example to .env
2. Fill in your Telegram Bot Token and Ngrok Auth Token in .env
3. Install requirements: `pip install -r requirements.txt`
4. Run the web server: `python web_server.py`
5. In another terminal, run the bot: `python bot.py`

## Files
- bot.py: Main Telegram bot logic
- web_server.py: Web server for wallet connection and transaction signing
- wallet_storage.py: Handles wallet address storage
- connect.html: Wallet connection page
- transaction.html: Transaction signing page 