# claude_usage.py - Check Claude plan usage from CLI

from curl_cffi import requests
from dotenv import load_dotenv
from datetime import datetime, timezone
import time
import os

load_dotenv()

ORG_ID     = os.getenv("CLAUDE_ORG_ID")
SESSION_KEY = os.getenv("CLAUDE_SESSION_KEY")
DEVICE_ID  = os.getenv("CLAUDE_DEVICE_ID")
ANON_ID    = os.getenv("CLAUDE_ANON_ID")

def get_usage():
    url = f"https://claude.ai/api/organizations/{ORG_ID}/usage"
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "anthropic-anonymous-id": ANON_ID,
        "anthropic-client-platform": "web_claude_ai",
        "anthropic-client-version": "1.0.0",
        "anthropic-device-id": DEVICE_ID,
        "content-type": "application/json",
        "referer": "https://claude.ai/settings/usage",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
    }
    cookies = {
        "sessionKey": SESSION_KEY,
        "anthropic-device-id": DEVICE_ID,
        "lastActiveOrg": ORG_ID,
    }
    resp = requests.get(url, headers=headers, cookies=cookies, impersonate="chrome110", timeout=15)
    resp.raise_for_status()
    return resp.json()

def render_bar(pct, width=28):
    pct = pct or 0
    filled = int(width * pct / 100)
    bar = "▓" * filled + "░" * (width - filled)
    color = "\033[92m" if pct < 50 else "\033[93m" if pct < 80 else "\033[91m"
    reset = "\033[0m"
    return f"{color}{bar}{reset} {pct:.0f}%"

def fmt_reset(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = dt - now
        total = int(diff.total_seconds())
        if total <= 0:
            return "resetting soon"
        h, rem = divmod(total, 3600)
        m = rem // 60
        if h > 24:
            d = h // 24
            return f"in {d}d {h % 24}h"
        return f"in {h}h {m}m"
    except:
        return iso

def display(data):
    W = 50
    print()
    print(f"  \033[1m\033[96m{'Claude Plan Usage':^{W}}\033[0m")
    print(f"  {'─' * W}")
    print()

    sections = {
        "five_hour":        ("⏱️  Current Session",  "5hr window"),
        "seven_day":        ("📅  Weekly · All Models", "7 day window"),
        "seven_day_sonnet": ("✦  Weekly · Sonnet",   "7 day window"),
    }

    for key, (label, window) in sections.items():
        val = data.get(key)
        if not val:
            continue
        pct   = val.get("utilization", 0) or 0
        reset = val.get("resets_at", "")
        resets_in = fmt_reset(reset) if reset else "unknown"

        print(f"  \033[1m{label}\033[0m  \033[90m({window})\033[0m")
        print(f"  {render_bar(pct)}")
        print(f"  \033[90mResets {resets_in}\033[0m")
        print()

    print(f"  {'─' * W}")
    print(f"  \033[90mLast checked: {datetime.now().strftime('%b %d, %Y  %H:%M:%S')}\033[0m")
    print()

if __name__ == "__main__":
    os.system('title Claude Usage Checker')
    if not all([ORG_ID, SESSION_KEY, DEVICE_ID, ANON_ID]):
        print("✗ Missing env vars — check your .env file")
        exit(1)

    INTERVAL = 20  # seconds between checks

    while True:
        os.system("cls" if os.name == "nt" else "clear")
        try:
            data = get_usage()
            display(data)
        except Exception as e:
            if hasattr(e, 'response') and e.response is not None:
                print(f"✗ HTTP {e.response.status_code} — session may be expired")
            else:
                print(f"✗ Error: {e}")

        print(f"  \033[90mRefreshing in {INTERVAL}s... (Ctrl+C to exit)\033[0m\n")
        time.sleep(INTERVAL)
