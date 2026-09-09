"""DigitalOcean worker entry point: python ev-calc.py."""

import logging
import os

from dotenv import load_dotenv

from discord_bot import create_bot
from user_settings import SettingsStore


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN is required")
    logging.info("Starting probit-pm-v1; revision=%s", os.getenv("APP_REVISION", "unspecified"))
    store = SettingsStore(os.getenv("USER_DATA_FILE", "user_data.json"))
    create_bot(store).run(token)


if __name__ == "__main__":
    main()
