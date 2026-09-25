"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json.
"""
import json
import os

from moz.domains import overview
from moz.mozcast import weather
from moz.rankings import top_domains

os.makedirs("output", exist_ok=True)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # a domain or any link on it: DA, spam score, top pages, linking domains, 60-day link history
    save("overview_tesla.com.json", overview("tesla.com"))

    # Moz Top 500, 50 per page
    save("top_domains_page_1.json", top_domains(page=1))

    # the last 7 days of Google ranking volatility
    save("mozcast_weather.json", weather(days=7))
