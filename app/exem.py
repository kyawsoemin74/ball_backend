import requests
import json

headers = {
    "x-apisports-key": "e89fd88f38b122b80806a155b161572f"
}

r = requests.get(
    "https://v3.football.api-sports.io/odds?fixture=1489372",
    headers=headers
)

data = r.json()

asian_handicap = []

for bookmaker in data["response"][0]["bookmakers"]:
    if bookmaker["name"] != "1xBet":
        continue

    for bet in bookmaker["bets"]:
        if bet["name"] == "Asian Handicap":
            asian_handicap.append(bet)

print(json.dumps(asian_handicap, indent=2))