from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.entities import MemoryReflection, Opportunity


CATEGORY_MEMORY_KEYWORDS = {
    "科研": ["科研", "实验室", "导师", "论文", "研究", "助研"],
    "实验室招募": ["科研", "实验室", "导师", "论文", "研究", "助研"],
    "实习": ["实习", "就业", "简历", "业务", "岗位"],
    "竞赛": ["竞赛", "比赛", "作品", "Demo", "加分"],
    "夏令营": ["保研", "夏令营", "申请", "升学", "营员"],
    "奖学金": ["奖学金", "资助", "申请"],
    "讲座": ["讲座", "分享", "入门"],
    "课程": ["课程", "训练营", "学习", "入门"],
}


class MemoryRetriever:
    def retrieve_relevant_memory(
        self,
        db: Session,
        user_id: int,
        opportunity: Opportunity,
        action: str = "recommend",
        limit: int = 3,
    ) -> list[MemoryReflection]:
        reflections = (
            db.query(MemoryReflection)
            .filter(MemoryReflection.user_id == user_id)
            .order_by(MemoryReflection.updated_at.desc())
            .all()
        )
        if not reflections:
            return []
        keywords = CATEGORY_MEMORY_KEYWORDS.get(opportunity.category, []) + [opportunity.category, action]
        scored = []
        for reflection in reflections:
            text = f"{reflection.reflection_type}\n{reflection.summary}"
            score = sum(1 for keyword in keywords if keyword and keyword in text)
            if reflection.reflection_type == "development_direction":
                score += 1
            if score > 0:
                scored.append((score, reflection))
        scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        return [reflection for _, reflection in scored[:limit]]
