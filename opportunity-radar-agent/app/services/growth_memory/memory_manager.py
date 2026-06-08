from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import MemoryReflection, Opportunity, UserEvent
from app.services.growth_memory.memory_retriever import MemoryRetriever
from app.services.growth_memory.memory_updater import MemoryUpdater
from app.services.growth_memory.reflection_generator import ReflectionGenerator


class GrowthMemoryManager:
    def __init__(self) -> None:
        self.updater = MemoryUpdater()
        self.generator = ReflectionGenerator()
        self.retriever = MemoryRetriever()

    def log_event(
        self,
        db: Session,
        user_id: int,
        event_type: str,
        event_target: str = "",
        event_metadata: dict[str, Any] | None = None,
    ) -> UserEvent:
        return self.updater.log_event(db, user_id, event_type, event_target, event_metadata)

    def generate_reflection(self, db: Session, user_id: int) -> list[MemoryReflection]:
        return self.generator.generate_reflection(db, user_id)

    def retrieve_relevant_memory(
        self,
        db: Session,
        user_id: int,
        opportunity: Opportunity,
        action: str = "recommend",
    ) -> list[MemoryReflection]:
        return self.retriever.retrieve_relevant_memory(db, user_id, opportunity, action)
