import argparse
import asyncio
import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient


async def purge() -> None:
    load_dotenv()
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    legacy_filter = {"encryption_version": {"$ne": "nacl-secretbox-v1"}}
    legacy_count = await db.encrypted_messages.count_documents(legacy_filter)
    result = await db.encrypted_messages.delete_many(legacy_filter)
    await db.reports.update_many(
        {"target_type": "transaction_messages"},
        {
            "$unset": {"evidence_messages": ""},
            "$set": {
                "evidence_status": "legacy_evidence_purged",
                "details": "Legacy evidence removed during E2EE migration",
            },
        },
    )
    client.close()
    print(f"Found {legacy_count} legacy messages; deleted {result.deleted_count}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Permanently remove pre-E2EE chat content before production startup."
    )
    parser.add_argument(
        "--confirm-permanent-delete",
        action="store_true",
        help="Required safety switch.",
    )
    args = parser.parse_args()
    if not args.confirm_permanent_delete:
        parser.error("--confirm-permanent-delete is required")
    asyncio.run(purge())
