import httpx
import json
import asyncio
from app.core.config import settings

async def debug_fixtures():
    # သင်စစ်ချင်တဲ့ endpoint (ဥပမာ- live ပွဲတွေ သို့မဟုတ် နေ့စွဲအလိုက်)
    url = f"{settings.FOOTBALL_API_BASE_URL}/fixtures"
    
    # ဒီမှာ စမ်းသပ်လိုတဲ့ parameter ကို ပြောင်းလဲနိုင်ပါတယ်
    params = {"live": "all"} 
    
    headers = {
        "x-apisports-key": settings.FOOTBALL_API_KEY,
        "Content-Type": "application/json"
    }

    print(f"Requesting URL: {url} with params: {params}")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, params=params)
        data = response.json()
        
        # ပွဲစဉ်တစ်ခုချင်းစီရဲ့ Team ID ပါဝင်မှုကို စစ်ဆေးမယ်
        fixtures = data.get("response", [])
        if not fixtures:
            print("No fixtures found in response.")
            return

        for f in fixtures[:5]: # ပထမဆုံး ၅ ပွဲကိုပဲ နမူနာကြည့်မယ်
            teams = f.get("teams", {})
            home = teams.get("home", {})
            away = teams.get("away", {})
            print(f"Fixture ID: {f.get('fixture', {}).get('id')}")
            print(f"  Home: {home.get('name')} (ID: {home.get('id')})")
            print(f"  Away: {away.get('name')} (ID: {away.get('id')})")
            print("-" * 30)

if __name__ == "__main__":
    asyncio.run(debug_fixtures())