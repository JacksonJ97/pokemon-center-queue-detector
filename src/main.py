import time
import json
import logging
import requests
from enum import StrEnum
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

URL = "https://www.pokemoncenter.com/en-ca"
INTERVAL = 120  # seconds
OBSERVATIONS_PATH = Path("logs/observations.jsonl")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


class QueueState(StrEnum):
    LIVE = "live"
    NOT_LIVE = "not_live"
    UNKNOWN = "unknown"


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
    x_iinfo = response.headers.get("X-Iinfo", "")

    # Include B12 if it's a confirmed stable marker for live queues
    if "0NNN" in x_iinfo:
        return QueueState.LIVE
    if "NNNN" in x_iinfo and "B12(14,0,0)" in x_iinfo:
        return QueueState.NOT_LIVE

    return QueueState.UNKNOWN


# Write a function that sends a message to discord.
def send_discord_message(message: str):
    # Placeholder implementation - replace with actual Discord message sending logic
    print(f"Sending message to Discord: {message}")


def main():
    print("Starting main function")
    previous_state = QueueState.UNKNOWN

    while True:
        response = fetch(URL)

        if response:
            state = detect_queue(response)

            if state == QueueState.LIVE and previous_state != QueueState.LIVE:
                print("Alert users")

            if state != QueueState.UNKNOWN:
                previous_state = state

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
