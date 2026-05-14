"""forms collection persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

FORMS_COLLECTION = "forms"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def upsert_form(
    db: Any,
    *,
    scan_id: str,
    form_doc: dict[str, Any],
) -> None:
    form_hash = form_doc["form_hash"]
    now = _utcnow()
    insert_only = {"scan_id": scan_id, "form_hash": form_hash, "first_seen_at": now}
    mutable_keys = ("page_url", "action", "method", "fields", "buttons", "csrf_detected")
    mutable = {key: form_doc.get(key) for key in mutable_keys}
    mutable["updated_at"] = now

    await db[FORMS_COLLECTION].update_one(
        {"scan_id": scan_id, "form_hash": form_hash},
        {"$setOnInsert": insert_only, "$set": mutable},
        upsert=True,
    )


async def count_forms(db: Any, *, scan_id: str) -> int:
    return await db[FORMS_COLLECTION].count_documents({"scan_id": scan_id})
