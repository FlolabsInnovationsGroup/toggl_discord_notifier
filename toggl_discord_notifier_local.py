import json
import os
import base64
import requests
from datetime import datetime, timedelta, timezone

# Config file paths
USERS_FILE = "toggl_users.json"
TOKENS_FILE = "toggl_api_tokens.json"
CACHE_FILE = "last_seen.json"

# Time window to check (minutes)
TIME_WINDOW_MINUTES = 30


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_last_seen():
    if not os.path.exists(CACHE_FILE):
        return {}
    return load_json(CACHE_FILE)


def update_last_seen(user_name, entry_id, cache):
    cache[user_name] = str(entry_id)
    save_json(CACHE_FILE, cache)


def get_time_entries(api_token, since):
    until = datetime.now(timezone.utc)

    # Format both timestamps as RFC3339 with 'Z' UTC suffix
    start_str = since.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    end_str = until.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    #print(f"[DEBUG] start_date={start_str}, end_date={end_str}")

    url = (
        f"https://api.track.toggl.com/api/v9/me/time_entries"
        f"?start_date={start_str}&end_date={end_str}"
    )
    headers = {
        "Authorization": "Basic " + base64.b64encode(f"{api_token}:api_token".encode()).decode()
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Failed to fetch entries: {response.status_code} - {response.text}")
        return []


def send_discord_message(webhook_url, message):
    payload = {"content": message}
    try:
        r = requests.post(webhook_url, json=payload)
        if r.status_code != 204:
            print(f"Failed to send to Discord: {r.status_code} - {r.text}")
    except Exception as e:
        print(f"Discord error: {e}")


def main():
    toggl_users = load_json(USERS_FILE)
    toggl_tokens = load_json(TOKENS_FILE)
    last_seen = load_last_seen()

    user_map = {u["name"]: u["discord_webhook_url"] for u in toggl_users}
    since = datetime.now(timezone.utc) - timedelta(minutes=TIME_WINDOW_MINUTES)

    for token_entry in toggl_tokens:
        token = token_entry["api_token"]
        entries = get_time_entries(token, since)
        
        print (f"[DEBUG] Fetched {len(entries)} entries for token {token}")

        for entry in entries:
            user = entry.get("user", {})
            user_name = user.get("fullname")
            entry_id = entry.get("id")
            
            print(f"[DEBUG] Entry by user: {entry.get('user', {}).get('fullname')} | ID: {entry.get('id')}")

            if not user_name or not entry_id:
                print("[DEBUG] Skipping entry due to missing user or ID.")
                print(json.dumps(entry, indent=2))
                continue
            if user_name not in user_map:
                continue
            if str(entry_id) == last_seen.get(user_name):
                continue

            description = entry.get("description", "No description")
            start = entry.get("start", "")[:19].replace("T", " ")
            duration_sec = entry.get("duration", 0)
            duration_min = max(1, duration_sec // 60)

            message = (
                f"🕒 **New Time Entry Added**\n"
                f"👤 User: {user_name}\n"
                f"📝 Task: {description}\n"
                f"🕐 Time Tracked: {duration_min} min\n"
                f"🗓️ Start: {start}"
            )

            print(f"Sending message for {user_name} - Entry ID {entry_id}")
            send_discord_message(user_map[user_name], message)
            update_last_seen(user_name, entry_id, last_seen)


if __name__ == "__main__":
    main()
