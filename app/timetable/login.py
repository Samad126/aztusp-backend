"""Sign in to Telegram once and print the session string for SCHEDULE_TELEGRAM_SESSION.

Run it from the backend folder with `python -m app.timetable.login`. It asks for the API id and API hash (from
my.telegram.org, under API development tools), then the phone number, the login code and the two-step password if
the account has one. The printed string logs in as that account, so keep it in .env and out of git.
"""

import asyncio
import getpass

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    api_id = int(input("API id: ").strip())
    api_hash = getpass.getpass("API hash: ").strip()

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start()  # prompts for the phone number, the login code and the two-step password
    print(f"\nSCHEDULE_TELEGRAM_SESSION={client.session.save()}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
