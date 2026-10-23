import time
import json
import logging
import requests
from math import ceil
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, time as datetime_time

URL = "https://www.pokemoncenter.com/en-ca"
INTERVAL = 120  # 2 minutes in seconds
COOLDOWN = 60 * 60  # 1 hour in seconds
MAX_CONSECUTIVE_FAILURES = 3
START_TIME = datetime_time(10)
END_TIME = datetime_time(16)
TIMEZONE = ZoneInfo("America/Toronto")
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


def retry_after_delay(response: requests.Response) -> int:
    value = response.headers.get("Retry-After", "").strip()

    if not value:
        return 0

    try:
        if value.isascii() and value.isdecimal():
            return int(value)

        retry_at = parsedate_to_datetime(value).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        remaining_seconds = (retry_at - now).total_seconds()

        return max(0, ceil(remaining_seconds))
    except (ValueError, TypeError, OverflowError):
        logger.warning("Invalid Retry-After header: %r", value)
        return 0


def log_observation(response: requests.Response):
    observation = {
        "timestamp": datetime.now(TIMEZONE).isoformat(),
        "url": response.url,
        "status": response.status_code,
        "x_iinfo": response.headers.get("X-Iinfo"),
        "queue": "off",  # Update this depending on the state of a queue
    }

    with OBSERVATIONS_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(observation) + "\n")


def detect_queue(response: requests.Response) -> QueueState:
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


def main() -> None:
    logger.info("Starting monitor")

    consecutive_fails = 0
    previous_state = QueueState.UNKNOWN

    while START_TIME <= datetime.now(TIMEZONE).time() < END_TIME:
        try:
            with requests.Session() as session:
                response = session.get(URL, timeout=(10, 30))
                response.raise_for_status()
        except requests.RequestException as error:
            consecutive_fails += 1
            logger.exception("Request failed: %s", error)

            if consecutive_fails >= MAX_CONSECUTIVE_FAILURES:
                logger.error("Exited after %s consecutive failures", consecutive_fails)
                # Alert that the program exited
                break

            retry_delay = INTERVAL * consecutive_fails

            if isinstance(error, requests.HTTPError):
                response = error.response

                if isinstance(response, requests.Response):
                    status = response.status_code

                    if status not in (408, 429) and status < 500:
                        logger.error("Exited after HTTP %s", status)
                        # Alert that the program exited
                        break

                    retry_delay = max(retry_delay, retry_after_delay(response))

            logger.warning("Retrying in %s seconds", retry_delay)
            time.sleep(retry_delay)
            continue

        consecutive_fails = 0
        state = detect_queue(response)

        if state == QueueState.LIVE and previous_state != QueueState.LIVE:
            # Alert users
            time.sleep(COOLDOWN - INTERVAL)

        if state == QueueState.UNKNOWN:
            # Log a response observation
            logger.info("Queue state unknown")
        else:
            previous_state = state

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
