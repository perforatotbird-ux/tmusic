"""
T-Music Telegram Session Generator
Interactive CLI tool to generate an independent Pyrogram session string for T-Music.
Ensures zero conflict (AuthKeyDuplicated) with CloudByte or other clients.
"""
import os
import sys
import asyncio
from pathlib import Path
from dotenv import dotenv_values, set_key
from pyrogram import Client

SCRIPT_DIR = Path(__file__).resolve().parent
TMUSIC_ROOT = SCRIPT_DIR.parent
ENV_FILE = TMUSIC_ROOT / "music.env"
ENV_EXAMPLE = TMUSIC_ROOT / "music.env.example"


async def main():
    print("=" * 65)
    print("  T-Music: Telegram Session String Generator")
    print("=" * 65)

    if not ENV_FILE.exists():
        if ENV_EXAMPLE.exists():
            import shutil
            shutil.copy2(ENV_EXAMPLE, ENV_FILE)
            print(f"Created {ENV_FILE} from template.")
        else:
            ENV_FILE.touch()

    env = dotenv_values(ENV_FILE)

    api_id = env.get("TG_API_ID") or input("Enter Telegram API ID: ").strip()
    api_hash = env.get("TG_API_HASH") or input("Enter Telegram API HASH: ").strip()

    while not str(api_id).isdigit():
        print("Error: API ID must contain digits only.")
        api_id = input("Enter Telegram API ID (digits only): ").strip()

    if not api_id or not api_hash:
        print("Error: API ID and API HASH are required.")
        sys.exit(1)

    print("\nConnecting to Telegram to generate string session...")
    print("Note: You can log into the SAME account as CloudByte without conflict,")
    print("because Telegram allows multiple independent session keys.\n")

    client = Client(
        name=":memory:",
        api_id=int(api_id),
        api_hash=api_hash,
        in_memory=True
    )

    await client.start()
    session_string = await client.export_session_string()
    me = await client.get_me()
    await client.stop()

    print("\n" + "=" * 65)
    print(f"  Successfully authenticated as: {me.first_name} (@{me.username or 'no-username'})")
    print("=" * 65)

    set_key(str(ENV_FILE), "TG_API_ID", str(api_id))
    set_key(str(ENV_FILE), "TG_API_HASH", str(api_hash))
    set_key(str(ENV_FILE), "TG_SESSION_STRING", session_string)

    print(f"\nSaved TG_SESSION_STRING to {ENV_FILE}")
    print("T-Music is now equipped with its own dedicated session!")


if __name__ == "__main__":
    asyncio.run(main())
