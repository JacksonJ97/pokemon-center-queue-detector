import os
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
ALERT_WEBHOOK_URL = os.environ["ALERT_WEBHOOK_URL"]
ERROR_WEBHOOK_URL = os.environ["ERROR_WEBHOOK_URL"]

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


def log_observation(response: requests.Response, queue_state: QueueState) -> None:
    observation = {
        "timestamp": datetime.now(TIMEZONE).isoformat(),
        "url": response.url,
        "status": response.status_code,
        "x_iinfo": response.headers.get("X-Iinfo", ""),
        "queue": queue_state,
    }

    OBSERVATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)

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


def notify_queue_live() -> bool | None:
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(
                ALERT_WEBHOOK_URL,
                json={
                    "content": "@everyone 🚨 Pokémon Center queue is live! https://www.pokemoncenter.com/en-ca",
                    "allowed_mentions": {"parse": ["everyone"]},
                },
                timeout=10,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as error:
            logger.error("Failed to send queue alert: %s", type(error).__name__)
            logger.warning("Queue alert attempt %s/%s", attempt, max_attempts)

            delay = 2 ** (attempt - 1)

            if isinstance(error, requests.HTTPError):
                response = error.response

                if isinstance(response, requests.Response):
                    status = response.status_code

                    if status != 429 and status < 500:
                        return False

                    delay = max(delay, retry_after_delay(response))

            if attempt == max_attempts:
                return False

            time.sleep(delay)


def notify_monitor_stopped() -> bool | None:
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(
                ERROR_WEBHOOK_URL,
                json={
                    "content": "⚠️ Pokémon Center monitoring stopped due to an error. Check the logs and restart the monitor.",
                    "allowed_mentions": {"parse": []},
                },
                timeout=10,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as error:
            logger.error("Failed to send error alert: %s", type(error).__name__)
            logger.warning("Error alert attempt %s/%s", attempt, max_attempts)

            delay = 2 ** (attempt - 1)

            if isinstance(error, requests.HTTPError):
                response = error.response

                if isinstance(response, requests.Response):
                    status = response.status_code

                    if status != 429 and status < 500:
                        return False

                    delay = max(delay, retry_after_delay(response))

            if attempt == max_attempts:
                return False

            time.sleep(delay)


def main() -> None:
    logger.info("Starting monitor")

    consecutive_fails = 0
    previous_state = QueueState.UNKNOWN

    while START_TIME <= datetime.now(TIMEZONE).time() < END_TIME:
        try:
            with requests.Session() as session:
                response = session.get(URL, timeout=(10, 30))

                if response.status_code >= 400:
                    log_observation(response=response, queue_state=QueueState.UNKNOWN)

                response.raise_for_status()
        except requests.RequestException as error:
            consecutive_fails += 1
            logger.exception("Request failed: %s", error)

            if consecutive_fails >= MAX_CONSECUTIVE_FAILURES:
                logger.error("Exited after %s consecutive failures", consecutive_fails)
                notify_monitor_stopped()
                break

            retry_delay = INTERVAL * consecutive_fails

            if isinstance(error, requests.HTTPError):
                response = error.response

                if isinstance(response, requests.Response):
                    status = response.status_code

                    if status not in (408, 429) and status < 500:
                        logger.error("Exited after HTTP %s", status)
                        notify_monitor_stopped()
                        break

                    retry_delay = max(retry_delay, retry_after_delay(response))

            logger.warning("Retrying in %s seconds", retry_delay)
            time.sleep(retry_delay)
            continue

        consecutive_fails = 0
        state = detect_queue(response)

        if state == QueueState.LIVE:
            if previous_state != QueueState.LIVE:
                if not notify_queue_live():
                    time.sleep(INTERVAL)
                    continue

                log_observation(response=response, queue_state=state)
                time.sleep(COOLDOWN - INTERVAL)

            previous_state = state
        elif state == QueueState.NOT_LIVE:
            log_observation(response=response, queue_state=state)
            previous_state = state
        else:
            log_observation(response=response, queue_state=state)
            logger.info("Queue state unknown")

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
