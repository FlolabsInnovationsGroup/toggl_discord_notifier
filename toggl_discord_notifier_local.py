import json
import os
import base64
import requests
from datetime import datetime, timedelta, timezone

USERS_FILE = "toggl_users.json"
TOKENS_FILE = "toggl_api_tokens.json"
CACHE_FILE = "last_seen.json"
TIME_WINDOW_MINUTES = 3

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

def fetch_workspace_entries(api_token, workspace_id, since, until):
    url = "https://api.track.toggl.com/reports/api/v2/details"
    headers = {
        "Authorization": "Basic " + base64.b64encode(f"{api_token}:api_token".encode()).decode()
    }
    params = {
        "workspace_id": workspace_id,
        "since": since.strftime("%Y-%m-%d"),
        "until": until.strftime("%Y-%m-%d"),
        "user_agent": "toggl-discord-bot",
        "page": 1
    }

    all_entries = []

    while True:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code != 200:
            print(f"Failed to fetch report entries: {response.status_code} - {response.text}")
            break

        data = response.json()
        entries = data.get("data", [])
        if not entries:
            break

        all_entries.extend(entries)

        total_count = data.get("total_count", 0)
        per_page = len(entries)
        if params["page"] * per_page >= total_count:
            break

        params["page"] += 1

    return all_entries

def send_discord_message(webhook_url, message):
    payload = {"content": message}
    try:
        r = requests.post(webhook_url, json=payload)
        if r.status_code != 204:
            print(f"Failed to send to Discord: {r.status_code} - {r.text}")
    except Exception as e:
        print(f"Discord error: {e}")


def convert_ms_to_hms(milliseconds):
    minutes = milliseconds // 60000
    seconds = (milliseconds % 60000) // 1000
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m {seconds}s"


def main():
    toggl_users = load_json(USERS_FILE)
    toggl_tokens = load_json(TOKENS_FILE)
    last_seen = load_last_seen()

    user_map = {u["name"]: u["discord_webhook_url"] for u in toggl_users}
    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=TIME_WINDOW_MINUTES)

    for token_entry in toggl_tokens:
        token = token_entry["api_token"]

        # Optional: You can hardcode or fetch workspace ID via /workspaces endpoint
        workspace_id = fetch_workspace_id(token)
        if not workspace_id:
            print(f"⚠️ Could not determine workspace ID for token ending in ...{token[-4:]}")
            continue

        entries = fetch_workspace_entries(token, workspace_id, since, now)
        print(f"[DEBUG] Fetched {len(entries)} entries from workspace {workspace_id}")

        for entry in entries:
            entry_id = entry.get("id")
            user_name = entry.get("user")
            start_str = entry.get("start")
            description = entry.get("description", "No description")
            project = entry.get("project", "No project")
            tags = entry.get("tags", [])
            tag_str = ", ".join(tags) if tags else "No tags"
            duration_ms = entry.get("dur", 0)

            if not user_name or not entry_id:
                continue
            if user_name not in user_map:
                continue
            if str(entry_id) == last_seen.get(user_name):
                continue

            try:
                start_dt = datetime.strptime(start_str, "%Y-%m-%dT%H:%M:%S%z")
            except Exception:
                start_dt = now  # fallback if parsing fails
            
            duration_f = convert_ms_to_hms(duration_ms)
            
            message = (
                f"🕒 **New Time Entry Added**\n"
                f"👤 User: {user_name}\n"
                f"📁 Project: {project}\n"
                f"🏷️ Tags: {tag_str}\n"
                f"📝 Task: {description}\n"
                f"🕐 Time Tracked: {duration_f}\n"
                f"🗓️ Start: {start_dt.strftime('%Y-%m-%d %H:%M')}\n"
                f"_________\n"
            )

            print(f"Sending message for {user_name} - Entry ID {entry_id}")
            send_discord_message(user_map[user_name], message)
            update_last_seen(user_name, entry_id, last_seen)

def fetch_workspace_id(api_token):
    url = "https://api.track.toggl.com/api/v9/workspaces"
    headers = {
        "Authorization": "Basic " + base64.b64encode(f"{api_token}:api_token".encode()).decode()
    }
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            workspaces = response.json()
            if workspaces:
                return workspaces[0]["id"]  # use the first one
    except Exception as e:
        print(f"Error fetching workspace ID: {e}")
    return None

if __name__ == "__main__":
    main()
