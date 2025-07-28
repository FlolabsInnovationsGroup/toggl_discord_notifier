import boto3
import json
import requests
from datetime import datetime, timedelta
import base64

S3_BUCKET = "my-config-bucket"  # 🔁 Replace with your bucket
USERS_FILE = "toggl_users.json"
TOKENS_FILE = "toggl_api_tokens.json"
DYNAMODB_TABLE = "TogglLastSeenEntries"

s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE)


def get_s3_json(filename):
    obj = s3.get_object(Bucket=S3_BUCKET, Key=filename)
    return json.loads(obj['Body'].read().decode('utf-8'))


def get_time_entries(api_token, since):
    url = f"https://api.track.toggl.com/api/v9/me/time_entries?start_date={since.isoformat()}Z"
    headers = {
        "Authorization": "Basic " + base64.b64encode(f"{api_token}:api_token".encode()).decode()
    }
    response = requests.get(url, headers=headers)
    return response.json() if response.ok else []


def send_discord_message(webhook_url, message):
    payload = {"content": message}
    try:
        requests.post(webhook_url, json=payload)
    except Exception as e:
        print(f"Failed to send message: {e}")


def get_last_seen(user_name):
    try:
        response = table.get_item(Key={'user_name': user_name})
        return response.get('Item', {}).get('last_seen')
    except Exception as e:
        print(f"Error reading DynamoDB: {e}")
        return None


def update_last_seen(user_name, entry_id):
    try:
        table.put_item(Item={'user_name': user_name, 'last_seen': entry_id})
    except Exception as e:
        print(f"Error updating DynamoDB: {e}")


def lambda_handler(event, context):
    toggl_users = get_s3_json(USERS_FILE)
    toggl_tokens = get_s3_json(TOKENS_FILE)

    user_map = {u["name"]: u["discord_webhook_url"] for u in toggl_users}
    since = datetime.utcnow() - timedelta(minutes=5)

    for token_entry in toggl_tokens:
        token = token_entry["api_token"]
        entries = get_time_entries(token, since)

        for entry in entries:
            user_name = entry.get("user", {}).get("fullname")
            entry_id = entry.get("id")
            if not user_name or not entry_id:
                continue

            if user_name not in user_map:
                continue

            last_seen_id = get_last_seen(user_name)
            if str(entry_id) == str(last_seen_id):
                continue  # Already seen

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

            send_discord_message(user_map[user_name], message)
            update_last_seen(user_name, entry_id)

    return {"status": "completed"}
