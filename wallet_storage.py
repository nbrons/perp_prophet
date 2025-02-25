import json
import os
import re

STORAGE_FILE = 'connected_wallets.json'

def is_valid_injective_address(address: str) -> bool:
    """Validate Injective address format"""
    return bool(re.match(r'^inj[a-zA-Z0-9]{39}$', address))

def load_wallets():
    if os.path.exists(STORAGE_FILE):
        with open(STORAGE_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_wallet(telegram_user_id, wallet_address):
    """Save wallet with validation and private key"""
    if not is_valid_injective_address(wallet_address):
        raise ValueError("Invalid Injective address format")
        
    wallets = load_wallets()
    wallets[str(telegram_user_id)] = wallet_address
    with open(STORAGE_FILE, 'w') as f:
        json.dump(wallets, f)

def get_wallet(telegram_user_id):
    """Get wallet with validation"""
    wallets = load_wallets()
    wallet = wallets.get(str(telegram_user_id))
    if wallet and not is_valid_injective_address(wallet):
        return None
    return wallet

def is_wallet_connected(telegram_user_id):
    """Check if valid wallet is connected"""
    wallet = get_wallet(telegram_user_id)
    return bool(wallet and is_valid_injective_address(wallet))

def disconnect_wallet(user_id: str) -> None:
    """Remove wallet connection for a user"""
    try:
        with open(STORAGE_FILE, 'r') as f:
            wallets = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        wallets = {}  # Start fresh if file doesn't exist or is invalid
    
    if str(user_id) in wallets:
        del wallets[str(user_id)]
    
    with open(STORAGE_FILE, 'w') as f:
        json.dump(wallets, f) 