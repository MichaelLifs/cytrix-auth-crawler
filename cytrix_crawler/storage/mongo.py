"""MongoDB connection helpers for crawler runtime."""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase


def get_mongo_client(uri: str) -> AsyncIOMotorClient:
    return AsyncIOMotorClient(uri)


def get_database(client: AsyncIOMotorClient, db_name: str) -> AsyncIOMotorDatabase:
    return client[db_name]


async def ping_database(db: AsyncIOMotorDatabase) -> None:
    await db.command("ping")


def close_mongo_client(client: AsyncIOMotorClient) -> None:
    client.close()
