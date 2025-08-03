import os
from dotenv import load_dotenv
from hyperliquid.utils import constants

load_dotenv()

class Config:
    ACCOUNT_ADDRESS = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")
    PRIVATE_KEY = os.getenv("HYPERLIQUID_PRIVATE_KEY")
    USE_TESTNET = os.getenv("USE_TESTNET", "true").lower() == "true"
    
    API_URL = constants.TESTNET_API_URL if USE_TESTNET else constants.MAINNET_API_URL
    
    DEFAULT_SLIPPAGE = 0.1
    MAX_POSITION_SIZE = 1000
    
    @classmethod
    def validate(cls):
        if not cls.ACCOUNT_ADDRESS:
            raise ValueError("HYPERLIQUID_ACCOUNT_ADDRESS not found in environment")
        if not cls.PRIVATE_KEY:
            raise ValueError("HYPERLIQUID_PRIVATE_KEY not found in environment")