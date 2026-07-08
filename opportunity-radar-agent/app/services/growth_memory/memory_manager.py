from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import GrowthMemorySnapshot, MemoryReflection, Opportunity, UserEvent
from app.services.growth_memory.memory_retriever import MemoryRetriever
from app.services.growth_memory.snapshot_service import GrowthMemorySnapshotService
from app.services.growth_memory.memory_updater import MemoryUpdater


class GrowthMemoryManager:
    def __init__(self) -> None:
        self.updater = MemoryUpdater()
        self.retriever = MemoryRetriever()
        self.snapshots = GrowthMemorySnapshotService()

    def log_event(
        self,
        db: Session,
        user_id: int,
        event_type: str,
        event_target: str = "",
        event_metadata: dict[str, Any] | None = None,
    ) -> UserEvent:
        return self.updater.log_event(db, user_id, event_type, event_target, event_metadata)

    def latest_snapshot(self, db: Session, user_id: int) -> GrowthMemorySnapshot | None:
        return self.snapshots.latest(db, user_id)

    def generate_snapshot(self, db: Session, user_id: int) -> dict[str, Any]:
        return self.snapshots.generate(db, user_id)

    def retrieve_relevant_memory(
        self,
        db: Session,
        user_id: int,
        opportunity: Opportunity,
        action: str = "recommend",
    ) -> list[MemoryReflection]:
        return self.retriever.retrieve_relevant_memory(db, user_id, opportunity, action)
