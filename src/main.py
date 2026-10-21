import json
import requests
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

URL = "https://www.pokemoncenter.com/en-ca"
OBSERVATIONS_PATH = Path("logs/observations.jsonl")


def log_observation(response: requests.Response):
    observation = {
        "timestamp": datetime.now(ZoneInfo("America/Toronto")).isoformat(),
        "url": response.url,
        "status": response.status_code,
        "x_iinfo": response.headers.get("X-Iinfo"),
        "queue": "off",
    }

    with OBSERVATIONS_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(observation) + "\n")


def detect_queue(response: requests.models.Response):
    print("Response Status:", response.status_code)
    print("--------------------------------------------------------")
    print("Response Set Cookies:", response.headers["Set-Cookie"])
    print("--------------------------------------------------------")
    print("Response X_Iinfo:", response.headers["X-Iinfo"])
    print("--------------------------------------------------------")
    print("Response Text:", response.text)
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

    try:
        with requests.Session() as session:
            response = session.get(URL, timeout=(10, 30))
            response.raise_for_status()
            state = detect_queue(response)
            if state == "possibly_live":
                send_discord_message("Queue is live!")
            if state == "possibly_off":
                print("Retry again after a minute")
            if state == "unknown":
                raise Exception("Queue state is unknown")
    except requests.Timeout:
        # Back off and retry later
        print("Request timed out")
    except requests.exceptions.SSLError as error:
        # Log it and keep certificate verification enabled
        print(f"TLS error: {error}")
    except requests.ConnectionError as error:
        # Back off and retry later
        print(f"Connection failed: {error}")
    except requests.HTTPError as error:
        status = error.response.status_code
        print(f"HTTP error: {status}")
        # 403: suspend checks
        # 429: respect Retry-After and back off
        # 5xx: retry later with backoff
    except requests.RequestException as error:
        # Log the failure
        print(f"Request failed: {error}")
    except Exception as error:
        # Queue errors
        print("Queue state:", error)


if __name__ == "__main__":
    main()
