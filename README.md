## Getting Your Telegram Bot Token
1. Open Telegram and search for "@BotFather"
2. Start a chat with BotFather and send `/newbot`
3. Follow the prompts:
   - Enter a name for your bot
   - Enter a username for your bot (must end in 'bot')
4. BotFather will give you a token like this: `123456789:ABCdefGHIjklmNOPQrstUVwxyz`
5. Copy this token to your .env file

Note: Keep your token secure! Anyone with your token can control your bot.

## Setup
1. Copy .env.example to .env
2. Fill in your Telegram Bot Token and Ngrok Auth Token in .env
3. setup your venv: `python3 -m venv bot-venv`
4. activate your venv: `source bot-venv/bin/activate`
5. Install requirements: `pip install -r requirements.txt`
6. Run the web server: `python web_server.py`
7. In another terminal, run the bot: `python bot.py`

## Files
- bot.py: Main Telegram bot logic
- web_server.py: Web server for wallet connection and transaction signing
- wallet_storage.py: Handles wallet address storage
- connect.html: Wallet connection page
- transaction.html: Transaction signing page 