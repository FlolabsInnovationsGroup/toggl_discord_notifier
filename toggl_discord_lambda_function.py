import json
import os
import base64
import requests
import boto3
from datetime import datetime, timedelta, timezone

# Environment variables
S3_BUCKET = os.environ["S3_BUCKET"]
DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]
USERS_FILE = "toggl_users.json"
TOKENS_FILE = "toggl_api_tokens.json"
TIME_WINDOW_MINUTES = 3
MAX_SEEN_IDS = 50

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DYNAMODB_TABLE)

def get_s3_json(filename):
    obj = s3.get_object(Bucket=S3_BUCKET, Key=filename)
    return json.loads(obj["Body"].read().decode("utf-8"))

def get_last_seen(user_name):
    try:
        response = table.get_item(Key={"user_name": user_name})
        return response.get("Item", {}).get("last_seen", [])
    except Exception as e:
        print(f"[ERROR] DynamoDB get error for {user_name}: {e}")
        return []

def update_last_seen(user_name, entry_id):
    try:
        existing_ids = get_last_seen(user_name)
        if entry_id not in existing_ids:
            updated_ids = (existing_ids + [entry_id])[-MAX_SEEN_IDS:]
            table.put_item(Item={"user_name": user_name, "last_seen": updated_ids})
    except Exception as e:
        print(f"[ERROR] DynamoDB put error for {user_name}: {e}")

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
            print(f"[ERROR] Failed to fetch report entries: {response.status_code} - {response.text}")
            break

        data = response.json()
        entries = data.get("data", [])
        all_entries.extend(entries)

        if len(all_entries) >= data.get("total_count", 0):
            break
        params["page"] += 1

    return all_entries

def send_discord_message(webhook_url, message):
    payload = {"content": message}
    try:
        r = requests.post(webhook_url, json=payload)
        if r.status_code != 204:
            print(f"[ERROR] Failed to send to Discord: {r.status_code} - {r.text}")
    except Exception as e:
        print(f"[ERROR] Discord send failed: {e}")

def convert_ms_to_hms(milliseconds):
    minutes = milliseconds // 60000
    seconds = (milliseconds % 60000) // 1000
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m {seconds}s"

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
                return workspaces[0]["id"]
    except Exception as e:
        print(f"[ERROR] Workspace ID fetch failed: {e}")
    return None

def lambda_handler(event, context):
    toggl_users = get_s3_json(USERS_FILE)
    toggl_tokens = get_s3_json(TOKENS_FILE)

    user_map = {u["name"]: u["discord_webhook_url"] for u in toggl_users}
    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=TIME_WINDOW_MINUTES)

    for token_entry in toggl_tokens:
        token = token_entry["api_token"]
        workspace_id = fetch_workspace_id(token)
        if not workspace_id:
            print(f"⚠️ No workspace ID for token ending in ...{token[-4:]}")
            continue

        entries = fetch_workspace_entries(token, workspace_id, since, now)
        print(f"[DEBUG] Fetched {len(entries)} entries from workspace {workspace_id}")

        for entry in entries:
            entry_id = str(entry.get("id"))
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

            seen_ids = get_last_seen(user_name)
            if entry_id in seen_ids:
                continue

            try:
                start_dt = datetime.strptime(start_str, "%Y-%m-%dT%H:%M:%S%z")
            except Exception:
                start_dt = now

            duration_fmt = convert_ms_to_hms(duration_ms)
            message = (
                f"🕒 **New Time Entry Added**\n"
                f"👤 User: {user_name}\n"
                f"📁 Project: {project}\n"
                f"🏷️ Tags: {tag_str}\n"
                f"📝 Task: {description}\n"
                f"🕐 Time Tracked: {duration_fmt}\n"
                f"🗓️ Start: {start_dt.strftime('%Y-%m-%d %H:%M')}\n"
                f"_________\n"
            )

            print(f"[INFO] Notifying {user_name} for entry {entry_id}")
            send_discord_message(user_map[user_name], message)
            update_last_seen(user_name, entry_id)

    return {"status": "complete"}
