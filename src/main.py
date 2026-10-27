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
INTERVAL = 90  # 1 min 30 secs
COOLDOWN = 60 * 60  # 1 hour
MAX_CONSECUTIVE_FAILURES = 3
START_TIME = datetime_time(8, 59)  # 8:59 am (added a buffer to account Railway startup)
END_TIME = datetime_time(17)  # 5:00 pm
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


def send_discord_message(
    alert_type: str,
    session: requests.Session,
    webhook_url: str,
    message: str,
    mention_everyone: bool,
) -> bool:
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        try:
            response = session.post(
                webhook_url,
                json={
                    "content": message,
                    "allowed_mentions": {
                        "parse": ["everyone"] if mention_everyone else []
                    },
                },
                timeout=10,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as error:
            logger.warning(
                "Discord %s alert attempt failed: %s", alert_type, type(error).__name__
            )

            retry_delay = 2 ** (attempt - 1)

            if isinstance(error, requests.HTTPError) and error.response is not None:
                status_code = error.response.status_code

                if status_code != 429 and status_code < 500:
                    logger.error(
                        "Discord %s alert delivery failed: HTTP %s",
                        alert_type,
                        status_code,
                    )
                    return False

                retry_delay = max(retry_delay, retry_after_delay(error.response))

            if attempt == max_attempts:
                logger.error(
                    "Discord %s alert delivery failed after %s attempts",
                    alert_type,
                    max_attempts,
                )
                return False

            time.sleep(retry_delay)

    return False


def notify_queue_live(session: requests.Session, webhook_url: str) -> bool:
    return send_discord_message(
        alert_type="queue",
        session=session,
        webhook_url=webhook_url,
        message="@everyone 🚨 Pokémon Center queue is live!",
        mention_everyone=True,
    )


def notify_monitor_stopped(session: requests.Session, webhook_url: str) -> bool:
    return send_discord_message(
        alert_type="error",
        session=session,
        webhook_url=webhook_url,
        message="⚠️ Pokémon Center monitoring stopped due to an error. Check the logs and restart the monitor.",
        mention_everyone=False,
    )


def main() -> None:
    logger.info("Starting monitor")

    alert_webhook_url = os.environ["ALERT_WEBHOOK_URL"]
    error_webhook_url = os.environ["ERROR_WEBHOOK_URL"]

    consecutive_fails = 0
    previous_state = QueueState.UNKNOWN

    with requests.Session() as monitor_session, requests.Session() as discord_session:
        while START_TIME <= datetime.now(TIMEZONE).time() < END_TIME:
            try:
                response = monitor_session.get(URL, timeout=10)

                if response.status_code >= 400:
                    log_observation(response, QueueState.UNKNOWN)

                response.raise_for_status()
            except requests.RequestException as error:
                consecutive_fails += 1
                logger.exception("Request failed: %s", error)

                if consecutive_fails >= MAX_CONSECUTIVE_FAILURES:
                    logger.error("Stopped after %s failures", consecutive_fails)
                    notify_monitor_stopped(discord_session, error_webhook_url)
                    break

                retry_delay = INTERVAL * consecutive_fails

                if isinstance(error, requests.HTTPError) and error.response is not None:
                    status_code = error.response.status_code

                    if status_code not in (408, 429) and status_code < 500:
                        logger.error("Stopped after HTTP %s", status_code)
                        notify_monitor_stopped(discord_session, error_webhook_url)
                        break

                    retry_delay = max(retry_delay, retry_after_delay(error.response))

                logger.warning("Retrying in %s seconds", retry_delay)
                time.sleep(retry_delay)
                continue

            consecutive_fails = 0
            state = detect_queue(response)

            if state == QueueState.LIVE:
                if previous_state != QueueState.LIVE:
                    log_observation(response, state)

                    if not notify_queue_live(discord_session, alert_webhook_url):
                        time.sleep(INTERVAL)
                        continue

                    time.sleep(COOLDOWN - INTERVAL)

                previous_state = state
            elif state == QueueState.NOT_LIVE:
                log_observation(response, state)
                previous_state = state
            else:
                log_observation(response, state)
                logger.info("Unknown queue state")

            time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
