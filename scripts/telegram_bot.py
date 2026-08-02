"""Run the Specula Telegram bot (commands + Claude Haiku Q&A).

Usage:
    uv run python scripts/telegram_bot.py
"""

import os
import sys

import anthropic
from dotenv import load_dotenv

from specula import bot


def main() -> int:
    load_dotenv(override=True)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not token or not chat_id or not api_key:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / ANTHROPIC_API_KEY "
              "missing in .env", file=sys.stderr)
        return 1
    client = anthropic.Anthropic(api_key=api_key)
    bot.run(token, chat_id, client)
    return 0


if __name__ == "__main__":
    sys.exit(main())
