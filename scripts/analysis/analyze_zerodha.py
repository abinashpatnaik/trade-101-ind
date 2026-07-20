import asyncio
from config import get_india_config
from zerodha_connector import ZerodhaConnector

async def main():
    config = get_india_config()
    connector = ZerodhaConnector(config)
    
    try:
        orders = await connector.get_today_orders()
        print(f"Found {len(orders)} Zerodha orders today.")
        for o in orders:
            print(o)
            
        positions = await connector.get_positions()
        print("\nPositions:")
        for p in positions:
            print(p)
    except Exception as e:
        print(e)

if __name__ == "__main__":
    asyncio.run(main())
