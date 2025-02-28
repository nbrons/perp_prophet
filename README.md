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
6. Run the iAgent on port 5000: `docker run -d -p 5000:5000 -e OPENAI_API_KEY="$OPENAI_API_KEY" -v $(pwd)/agents_config.yaml:/app/agents_config.yaml --name injective-agent injectivelabs/iagent`
7. Run the web server on port 5001: `python web_server.py`
8. In another terminal, run the bot: `python bot.py`

## Files
- bot.py: Main Telegram bot logic
- web_server.py: Web server for wallet connection and transaction signing
- wallet_storage.py: Handles wallet address storage
- connect.html: Wallet connection page
- transaction.html: Transaction signing page

## Setting up iAgent

The bot integrates with Injective's iAgent for position analysis. To set up:

1. Clone the iAgent repository:
   ```
   git clone https://github.com/InjectiveLabs/iAgent.git
   ```

2. Add your OpenAI API key to .env:
   ```
   OPENAI_API_KEY=your_openai_api_key_here
   ```

3. Create a hello_main agent on mainnet:
   ```bash
   # Create agents_config.yaml
   echo "hello_main:
     network: mainnet" > agents_config.yaml

   # Run iAgent and create a new agent
   cd iAgent
   python quickstart.py
   
   # In the quickstart CLI, enter these commands:
   # switch_network mainnet
   # create_agent hello_main
   # The agent's private key and address will be displayed - copy these to your agents_config.yaml
   ```

4. Run the iAgent Docker container:
   ```bash
   cd iAgent
   docker build -t injective-agent .
   docker run -d -p 5000:5000 \
     -e OPENAI_API_KEY="$OPENAI_API_KEY" \
     -v $(pwd)/agents_config.yaml:/app/agents_config.yaml \
     --name injective-agent \
     injective-agent
   ```

5. Or you can run the prebuilt image:
   ```bash
   docker run -d -p 5000:5000 \
     -e OPENAI_API_KEY="$OPENAI_API_KEY" \
     -v $(pwd)/agents_config.yaml:/app/agents_config.yaml \
     --name injective-agent \
     ghcr.io/injectivelabs/iagent:latest
   ```

6. Verify the container is running:
   ```
   docker ps | grep injective-agent
   ```

7. Verify the agent is working properly:
   ```bash
   curl -X POST http://localhost:5000/chat \
     -H "Content-Type: application/json" \
     -d '{"message":"Hello, how are you?", "session_id":"test", "agent_id":"hello_main", "agent_key":"YOUR_PRIVATE_KEY_HERE", "environment":"mainnet"}'
   ```

> ⚠️ **Security Warning**: Keep your private keys secure! Never commit agents_config.yaml to Git.

Once set up, users can analyze their positions by clicking the "Analyze with iAgent" button in the positions view. 