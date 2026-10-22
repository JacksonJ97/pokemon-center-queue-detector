import json
import logging
import requests
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

URL = "https://www.pokemoncenter.com/en-ca"
OBSERVATIONS_PATH = Path("logs/observations.jsonl")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


def fetch(url: str):
    try:
        with requests.Session() as session:
            response = session.get(url, timeout=(10, 30))
            response.raise_for_status()
            return response
    except requests.Timeout:
        # Back off and retry later
        logger.exception("Request timed out")
    except requests.exceptions.SSLError:
        logger.exception("TLS error")
    except requests.ConnectionError:
        # Back off and retry later
        logger.exception("Connection failed")
    except requests.HTTPError as error:
        status = error.response.status_code
        logger.exception(f"{status} HTTP error")
        # 403: Suspend checks (stop sending requests)
        # 429: Respect Retry-After and back off
        # 5xx: Back off and retry later
    except requests.RequestException:
        logger.exception("Request failed")

    return None


def log_observation(response: requests.Response):
    observation = {
        "timestamp": datetime.now(ZoneInfo("America/Toronto")).isoformat(),
        "url": response.url,
        "status": response.status_code,
        "x_iinfo": response.headers.get("X-Iinfo"),
        "queue": "off",  # Update this depending on the state of a queue
    }

    with OBSERVATIONS_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(observation) + "\n")


def detect_queue(response: requests.models.Response):
    x_iinfo = response.headers.get("X-Iinfo")

    if x_iinfo:
        on = "B12(14,0,0)" not in x_iinfo and "0NNN" in x_iinfo

        off = "B12(14,0,0)" in x_iinfo and "NNNN" in x_iinfo

        if on:
            return "possibly_live"
        if off:
            return "possibly_off"
    return "unknown"


# Write a function that sends a message to discord.
def send_discord_message(message: str):
    # Placeholder implementation - replace with actual Discord message sending logic
    print(f"Sending message to Discord: {message}")


def main():
    print("Starting main function")

    response = fetch(URL)

    if response:
        state = detect_queue(response)


if __name__ == "__main__":
    main()
