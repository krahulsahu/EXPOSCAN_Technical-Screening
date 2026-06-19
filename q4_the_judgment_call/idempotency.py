import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
from fastapi import Request, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

# idempotency manager class
class IdempotencyManager:
    def __init__(self, db: AsyncIOMotorDatabase, collection_name: str = "idempotency_keys"):
        self.db = db
        self.collection = db[collection_name]

    # set up key expiration (24h)
    async def setup_indexes(self):
        await self.collection.create_index("created_at", expireAfterSeconds=86400)

    # start request locking
    async def start_request(self, key: str) -> Optional[Dict[str, Any]]:
        now = datetime.now(timezone.utc)
        record = {
            "_id": key,
            "status": "processing",
            "created_at": now,
            "response_body": None,
            "response_status_code": None
        }

        try:
            # atomic lock
            await self.collection.insert_one(record)
            return None
        except DuplicateKeyError:
            existing = await self.collection.find_one({"_id": key})
            if not existing:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="lock conflict"
                )

            if existing["status"] == "processing":
                raise HTTPException(
                    status_code=status.HTTP_425_TOO_EARLY,
                    detail="duplicate request"
                )
            
            return {
                "body": existing["response_body"],
                "status_code": existing["response_status_code"]
            }

    # save response
    async def complete_request(self, key: str, response_body: Any, response_status_code: int):
        await self.collection.update_one(
            {"_id": key},
            {
                "$set": {
                    "status": "completed",
                    "response_body": response_body,
                    "response_status_code": response_status_code,
                    "completed_at": datetime.now(timezone.utc)
                }
            }
        )

    # delete lock on error
    async def fail_request(self, key: str):
        await self.collection.delete_one({"_id": key})

# dependency injector
async def enforce_idempotency(request: Request, db: AsyncIOMotorDatabase) -> Optional[IdempotencyManager]:
    return IdempotencyManager(db)
