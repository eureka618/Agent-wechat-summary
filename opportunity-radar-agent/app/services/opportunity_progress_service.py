from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.entities import (
    Article,
    GrowthMemorySnapshot,
    Opportunity,
    OpportunityProgressItem,
    OpportunityProgressPlan,
    OpportunityProgressTask,
    OpportunityProgressUpdate,
    OpportunityProgressVersion,
    OpportunityProgressWebCheck,
    Todo,
    UserEvent,
    UserProfile,
)
from app.services.json_utils import dumps, loads_dict, loads_list
from app.services.external_search_provider import BochaSearchProvider
from app.services.llm_gateway import LLMGateway
from app.services.todo_service import TodoService


MAX_ROUNDS = 3
logger = logging.getLogger(__name__)
ROUND_STAGE_LABELS = {
    "initial_analysis": "初步分析机会",
    "source_check": "检查现有信息来源",
    "similar_comparison": "比较本地同类机会",
    "user_fit": "评估与你的匹配程度",
    "risk_analysis": "分析风险与不确定信息",
    "action_planning": "整理推进方案",
    "final_summary": "形成最终结论",
}
ALLOWED_NEXT_FOCUS = set(ROUND_STAGE_LABELS)
FINAL_JUDGMENTS = {"值得优先推进", "可以推进", "建议观察", "暂不建议推进"}


class OpportunityProgressService:
    def __init__(self, gateway: LLMGateway | None = None) -> None:
        self.gateway = gateway or LLMGateway()

    def analyze(self, db: Session, user_id: int, opportunity_id: int) -> OpportunityProgressTask:
        task = self.start_analysis_task(db, user_id, opportunity_id)
        self.execute_analysis_task(task.id)
        refreshed = db.get(OpportunityProgressTask, task.id)
        if not refreshed:
            raise RuntimeError("AI 推进任务丢失")
        return refreshed

    def start_analysis_task(self, db: Session, user_id: int, opportunity_id: int) -> OpportunityProgressTask:
        user = db.get(UserProfile, user_id)
        if not user:
            raise ValueError("用户画像不存在")
        opportunity = db.get(Opportunity, opportunity_id)
        if not opportunity:
            raise ValueError("机会不存在")
        existing = (
            db.query(OpportunityProgressTask)
            .filter(
                OpportunityProgressTask.user_id == user_id,
                OpportunityProgressTask.opportunity_id == opportunity_id,
                OpportunityProgressTask.status.in_(["analyzing", "waiting_confirmation", "stopped"]),
            )
            .order_by(OpportunityProgressTask.updated_at.desc())
            .first()
        )
        if existing:
            return existing

        task = OpportunityProgressTask(
            user_id=user_id,
            opportunity_id=opportunity_id,
            current_stage="initial_analysis",
            status="analyzing",
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    def execute_analysis_task(self, task_id: int) -> None:
        started = time.perf_counter()
        db = SessionLocal()
        task = db.get(OpportunityProgressTask, task_id)
        if not task:
            db.close()
            return
        if task.status != "analyzing":
            db.close()
            return
        user = db.get(UserProfile, task.user_id)
        opportunity = db.get(Opportunity, task.opportunity_id)
        if not user or not opportunity:
            task.status = "failed"
            task.error_message = "用户画像或机会不存在"
            task.updated_at = datetime.utcnow()
            db.commit()
            db.close()
            return
        logger.info(
            "opportunity progress background started task_id=%s user_id=%s opportunity_id=%s",
            task.id,
            task.user_id,
            task.opportunity_id,
        )
        context = self._build_context(db, user, opportunity)
        db.close()

        rounds: list[dict[str, Any]] = []
        next_focus = "initial_analysis"
        try:
            for index in range(MAX_ROUNDS):
                try:
                    round_result = self._run_round(context, rounds, next_focus, index + 1)
                except Exception:
                    if rounds:
                        logger.exception("opportunity progress round failed after partial success task_id=%s rounds=%s", task_id, len(rounds))
                        break
                    raise
                rounds.append(round_result)
                db = SessionLocal()
                task = db.get(OpportunityProgressTask, task_id)
                if not task or task.status != "analyzing":
                    db.close()
                    return
                task.rounds = dumps(rounds)
                task.current_stage = str(round_result.get("stage") or next_focus)
                task.updated_at = datetime.utcnow()
                db.commit()
                db.close()
                if self._should_stop_rounds(rounds):
                    break
                next_focus = str(round_result.get("next_focus") or "action_planning")

            try:
                final_result = self._run_final(context, rounds)
            except Exception:
                if not rounds:
                    raise
                logger.exception("opportunity progress final failed; using partial fallback task_id=%s rounds=%s", task_id, len(rounds))
                final_result = self._fallback_final_from_rounds(context, rounds)
            db = SessionLocal()
            task = db.get(OpportunityProgressTask, task_id)
            if not task:
                db.close()
                return
            task.final_result = dumps(final_result)
            task.suggested_todos = dumps(final_result.get("suggested_todos") or [])
            task.status = "waiting_confirmation" if final_result.get("should_create_todos") else "stopped"
            task.current_stage = "final_summary"
            task.updated_at = datetime.utcnow()
            db.commit()
            logger.info(
                "opportunity progress background completed task_id=%s user_id=%s opportunity_id=%s rounds=%s status=%s elapsed_ms=%s",
                task.id,
                task.user_id,
                task.opportunity_id,
                len(rounds),
                task.status,
                int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            db = SessionLocal()
            task = db.get(OpportunityProgressTask, task_id)
            if not task:
                db.close()
                return
            logger.exception(
                "opportunity progress background failed task_id=%s user_id=%s opportunity_id=%s rounds=%s elapsed_ms=%s error_type=%s",
                task.id,
                task.user_id,
                task.opportunity_id,
                len(rounds),
                int((time.perf_counter() - started) * 1000),
                type(exc).__name__,
            )
            task.status = "failed"
            task.error_message = self._analysis_error_message(exc)
            task.updated_at = datetime.utcnow()
            db.commit()
        finally:
            db.close()

    def confirm_create_todo(self, db: Session, task_id: int) -> tuple[OpportunityProgressTask, Todo | None, str]:
        task = db.get(OpportunityProgressTask, task_id)
        if not task:
            raise ValueError("推进任务不存在")
        if task.status not in {"waiting_confirmation", "completed"}:
            raise ValueError("当前推进结果不建议加入待办")
        final_result = loads_dict(task.final_result)
        if not final_result.get("should_create_todos"):
            raise ValueError("当前推进结果不建议加入待办")

        existing = (
            db.query(Todo)
            .filter(
                Todo.user_id == task.user_id,
                Todo.opportunity_id == task.opportunity_id,
                Todo.status.in_(["pending", "in_progress"]),
            )
            .first()
        )
        if existing:
            task.confirmed = True
            task.status = "completed"
            task.created_todo_ids = dumps([existing.id])
            task.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(task)
            setattr(existing, "already_exists", True)
            return task, existing, "该待办已经存在，本次未重复添加。"

        todo = TodoService().create_from_opportunity(db, task.user_id, task.opportunity_id)
        task = db.get(OpportunityProgressTask, task_id)
        task.confirmed = True
        task.status = "completed"
        task.created_todo_ids = dumps([todo.id])
        task.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(task)
        return task, todo, "已加入待办。"

    def serialize_task(self, task: OpportunityProgressTask) -> dict[str, Any]:
        rounds = self._loads_list_of_dicts(task.rounds)
        final_result = loads_dict(task.final_result)
        suggested_todos = self._loads_list_of_dicts(task.suggested_todos)
        created_ids = []
        for item in self._loads_list(task.created_todo_ids):
            if str(item).isdigit():
                created_ids.append(int(item))
        return {
            "id": task.id,
            "user_id": task.user_id,
            "opportunity_id": task.opportunity_id,
            "current_stage": task.current_stage,
            "rounds": rounds,
            "final_result": final_result or None,
            "suggested_todos": suggested_todos,
            "confirmed": task.confirmed,
            "created_todo_ids": created_ids,
            "status": task.status,
            "error_message": task.error_message,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }

    def get_current_plan(self, db: Session, user_id: int, opportunity_id: int) -> OpportunityProgressPlan | None:
        return (
            db.query(OpportunityProgressPlan)
            .filter(
                OpportunityProgressPlan.user_id == user_id,
                OpportunityProgressPlan.opportunity_id == opportunity_id,
            )
            .order_by(OpportunityProgressPlan.updated_at.desc())
            .first()
        )

    def get_plan(self, db: Session, plan_id: int) -> OpportunityProgressPlan:
        plan = db.get(OpportunityProgressPlan, plan_id)
        if not plan:
            raise ValueError("推进计划不存在")
        return plan

    def create_plan_from_task(self, db: Session, task_id: int) -> OpportunityProgressPlan:
        task = db.get(OpportunityProgressTask, task_id)
        if not task:
            raise ValueError("推进分析任务不存在")
        existing = (
            db.query(OpportunityProgressPlan)
            .filter(
                OpportunityProgressPlan.user_id == task.user_id,
                OpportunityProgressPlan.opportunity_id == task.opportunity_id,
                OpportunityProgressPlan.system_status.in_(["active", "paused"]),
            )
            .order_by(OpportunityProgressPlan.updated_at.desc())
            .first()
        )
        if existing:
            return existing

        opportunity = db.get(Opportunity, task.opportunity_id)
        if not opportunity:
            raise ValueError("机会不存在")
        final_result = loads_dict(task.final_result)
        if not final_result:
            raise ValueError("推进分析结果不存在，请先完成 AI 分析")

        risks = self._string_list(final_result.get("risks_and_uncertainties"))
        plan = OpportunityProgressPlan(
            user_id=task.user_id,
            opportunity_id=task.opportunity_id,
            source_task_id=task.id,
            goal=f"推进当前机会：{opportunity.name}",
            overall_judgment=str(final_result.get("overall_judgment") or "建议观察"),
            current_stage="刚建立推进计划，正在确认下一步行动",
            plan_summary=str(final_result.get("recommended_strategy") or final_result.get("summary") or "先补齐关键信息，再决定推进节奏。"),
            next_focus=str(final_result.get("recommended_strategy") or "确认报名入口、关键要求和需要准备的材料。"),
            risks_json=dumps(risks),
            system_status="active",
            version_number=1,
        )
        db.add(plan)
        db.flush()

        todos = self._loads_list_of_dicts(task.suggested_todos) or self._loads_list_of_dicts(dumps(final_result.get("suggested_todos") or []))
        if not todos:
            todos = [{"title": "确认机会关键信息", "description": "阅读原文，确认报名入口、截止时间和申请要求。"}]
        for item in todos[:8]:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            db.add(
                OpportunityProgressItem(
                    plan_id=plan.id,
                    title=title[:255],
                    description=str(item.get("description") or "")[:1000],
                    priority="medium",
                    status="pending",
                    source="initial_analysis",
                )
            )
        db.flush()
        self._save_plan_version(db, plan, update_id=None)
        self._log_event(db, plan.user_id, "create_progress_plan", plan.opportunity_id, {"plan_id": plan.id, "version_number": 1})
        db.commit()
        db.refresh(plan)
        return plan

    def replan(self, db: Session, plan_id: int, progress_text: str) -> OpportunityProgressUpdate:
        progress_text = (progress_text or "").strip()
        if not progress_text:
            raise ValueError("请先输入最新进展")
        plan = self.get_plan(db, plan_id)
        if plan.system_status not in {"active", "paused"}:
            raise ValueError("已结束的推进计划不能继续更新")
        existing_pending = (
            db.query(OpportunityProgressUpdate)
            .filter(OpportunityProgressUpdate.plan_id == plan_id, OpportunityProgressUpdate.confirmation_status == "pending")
            .order_by(OpportunityProgressUpdate.created_at.desc())
            .first()
        )
        if existing_pending:
            raise ValueError("已有待确认的计划调整建议，请先确认或暂不修改")

        context = self._build_replan_context(db, plan, progress_text)
        try:
            proposed = self._run_replan(context)
        except Exception as exc:
            raise RuntimeError("重新规划失败，请稍后重试。") from exc

        update = OpportunityProgressUpdate(
            plan_id=plan.id,
            user_progress_text=progress_text,
            ai_assessment_json=dumps({"progress_assessment": proposed.get("progress_assessment", "")}),
            proposed_changes_json=dumps(proposed),
            confirmation_status="pending",
        )
        db.add(update)
        self._log_event(
            db,
            plan.user_id,
            "submit_progress_update",
            plan.opportunity_id,
            {
                "plan_id": plan.id,
                "changed_task_count": len(proposed.get("task_changes") or []),
                "new_task_count": len(proposed.get("new_tasks") or []),
            },
        )
        db.commit()
        db.refresh(update)
        return update

    def confirm_plan_update(self, db: Session, plan_id: int, update_id: int) -> OpportunityProgressPlan:
        plan = self.get_plan(db, plan_id)
        update = db.get(OpportunityProgressUpdate, update_id)
        if not update or update.plan_id != plan.id:
            raise ValueError("计划调整建议不存在")
        if update.confirmation_status != "pending":
            raise ValueError("该调整建议已经处理过")
        if plan.system_status not in {"active", "paused"}:
            raise ValueError("已结束的推进计划不能继续更新")

        proposed = loads_dict(update.proposed_changes_json)
        items = {item.id: item for item in self._plan_items(db, plan.id)}
        now = datetime.utcnow()
        suggested_stage = str(proposed.get("suggested_stage") or "").strip()
        if suggested_stage:
            plan.current_stage = suggested_stage[:1000]
        summary = str(proposed.get("user_facing_summary") or "").strip()
        if summary:
            plan.plan_summary = summary[:2000]
        next_focus = str(proposed.get("suggested_next_focus") or "").strip()
        if next_focus:
            plan.next_focus = next_focus[:1200]
        risks = self._string_list(proposed.get("updated_risks"))
        if risks:
            plan.risks_json = dumps(risks[:10])

        for change in proposed.get("task_changes") or []:
            if not isinstance(change, dict):
                continue
            task_id = self._int_or_none(change.get("task_id"))
            item = items.get(task_id or -1)
            if not item:
                continue
            change_type = str(change.get("change_type") or "").strip()
            if change_type == "modify":
                if change.get("new_title"):
                    item.title = str(change["new_title"])[:255]
                if change.get("new_description"):
                    item.description = str(change["new_description"])[:1000]
            elif change_type == "reprioritize" and change.get("new_priority") in {"high", "medium", "low"}:
                item.priority = change["new_priority"]
            elif change_type == "complete":
                item.status = "completed"
            elif change_type == "pause":
                item.status = "paused"
            elif change_type == "mark_no_longer_applicable":
                item.status = "no_longer_applicable"
            if change.get("new_priority") in {"high", "medium", "low"}:
                item.priority = change["new_priority"]
            item.updated_at = now

        for task in proposed.get("new_tasks") or []:
            if not isinstance(task, dict):
                continue
            title = str(task.get("title") or "").strip()
            if not title:
                continue
            if self._has_similar_plan_item(items.values(), title):
                continue
            db.add(
                OpportunityProgressItem(
                    plan_id=plan.id,
                    title=title[:255],
                    description=str(task.get("description") or task.get("reason") or "")[:1000],
                    priority=str(task.get("priority") or "medium") if task.get("priority") in {"high", "medium", "low"} else "medium",
                    status="pending",
                    source="replan",
                )
            )

        plan.system_status = "active" if plan.system_status == "paused" else plan.system_status
        plan.version_number = (plan.version_number or 1) + 1
        plan.updated_at = now
        update.confirmation_status = "confirmed"
        update.confirmed_at = now
        db.flush()
        self._save_plan_version(db, plan, update_id=update.id)
        self._log_event(
            db,
            plan.user_id,
            "confirm_plan_update",
            plan.opportunity_id,
            {
                "plan_id": plan.id,
                "version_number": plan.version_number,
                "changed_task_count": len(proposed.get("task_changes") or []),
                "new_task_count": len(proposed.get("new_tasks") or []),
            },
        )
        db.commit()
        db.refresh(plan)
        return plan

    def dismiss_plan_update(self, db: Session, plan_id: int, update_id: int) -> OpportunityProgressUpdate:
        plan = self.get_plan(db, plan_id)
        update = db.get(OpportunityProgressUpdate, update_id)
        if not update or update.plan_id != plan.id:
            raise ValueError("计划调整建议不存在")
        if update.confirmation_status != "pending":
            raise ValueError("该调整建议已经处理过")
        update.confirmation_status = "dismissed"
        update.confirmed_at = datetime.utcnow()
        db.commit()
        db.refresh(update)
        return update

    def sync_plan_items_to_todos(self, db: Session, plan_id: int, item_ids: list[int]) -> dict[str, Any]:
        plan = self.get_plan(db, plan_id)
        if plan.system_status not in {"active", "paused"}:
            raise ValueError("已结束的推进计划不能同步待办")
        selected_ids = {int(item_id) for item_id in item_ids if str(item_id).isdigit()}
        if not selected_ids:
            raise ValueError("请选择要同步的计划任务")
        items = [item for item in self._plan_items(db, plan.id) if item.id in selected_ids]
        created = []
        existing = []
        failed = []
        opportunity = db.get(Opportunity, plan.opportunity_id)
        if not opportunity:
            raise ValueError("机会不存在")

        for item in items:
            if item.synced_to_todo and item.todo_id:
                existing.append({"item_id": item.id, "title": item.title, "todo_id": item.todo_id, "reason": "已经同步过"})
                continue
            existing_todo = self._find_similar_todo(db, plan.user_id, plan.opportunity_id, item.title)
            if existing_todo:
                item.synced_to_todo = True
                item.todo_id = existing_todo.id
                existing.append({"item_id": item.id, "title": item.title, "todo_id": existing_todo.id, "reason": "相同机会已有待办"})
                continue
            try:
                todo = TodoService().create_from_opportunity(db, plan.user_id, plan.opportunity_id)
                todo.title = item.title[:255]
                todo.description = self._todo_description_from_plan_item(item, opportunity)
                todo.deadline_note = "来自推进计划，请按机会原文自行确认时间"
                todo.priority = item.priority if item.priority in {"high", "medium", "low"} else "medium"
                db.flush()
                item.synced_to_todo = True
                item.todo_id = todo.id
                created.append({"item_id": item.id, "title": item.title, "todo_id": todo.id})
                self._log_event(db, plan.user_id, "sync_plan_item_to_todo", plan.opportunity_id, {"plan_id": plan.id, "item_id": item.id, "todo_id": todo.id})
            except SQLAlchemyError as exc:
                db.rollback()
                failed.append({"item_id": item.id, "title": item.title, "error": "待办已存在或创建失败"})
                logger_exception = getattr(exc, "__class__", type(exc)).__name__
                self._log_event(db, plan.user_id, "sync_plan_item_to_todo", plan.opportunity_id, {"plan_id": plan.id, "item_id": item.id, "status": "failed", "error": logger_exception})
                db.flush()
        db.commit()
        return {"created": created, "existing": existing, "failed": failed, "synced_todo_count": len(created)}

    def set_plan_status(self, db: Session, plan_id: int, status: str) -> OpportunityProgressPlan:
        if status not in {"completed", "paused", "cancelled"}:
            raise ValueError("不支持的推进计划状态")
        plan = self.get_plan(db, plan_id)
        plan.system_status = status
        plan.version_number = (plan.version_number or 1) + 1
        plan.updated_at = datetime.utcnow()
        if status in {"completed", "cancelled"}:
            plan.completed_at = datetime.utcnow()
        event_type = {
            "completed": "complete_progress_plan",
            "paused": "pause_progress_plan",
            "cancelled": "close_progress_plan",
        }[status]
        self._save_plan_version(db, plan, update_id=None)
        self._log_event(db, plan.user_id, event_type, plan.opportunity_id, {"plan_id": plan.id, "version_number": plan.version_number})
        db.commit()
        db.refresh(plan)
        return plan

    def serialize_plan(self, db: Session, plan: OpportunityProgressPlan, include_pending_update: bool = True) -> dict[str, Any]:
        pending_update = None
        if include_pending_update:
            update = (
                db.query(OpportunityProgressUpdate)
                .filter(OpportunityProgressUpdate.plan_id == plan.id, OpportunityProgressUpdate.confirmation_status == "pending")
                .order_by(OpportunityProgressUpdate.created_at.desc())
                .first()
            )
            pending_update = self.serialize_update(update) if update else None
        return {
            "id": plan.id,
            "user_id": plan.user_id,
            "opportunity_id": plan.opportunity_id,
            "source_task_id": plan.source_task_id,
            "goal": plan.goal,
            "overall_judgment": plan.overall_judgment,
            "current_stage": plan.current_stage,
            "plan_summary": plan.plan_summary,
            "next_focus": plan.next_focus,
            "risks": self._loads_list(plan.risks_json),
            "system_status": plan.system_status,
            "version_number": plan.version_number,
            "items": [self.serialize_item(item) for item in self._plan_items(db, plan.id)],
            "pending_update": pending_update,
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
            "completed_at": plan.completed_at,
        }

    def serialize_item(self, item: OpportunityProgressItem) -> dict[str, Any]:
        return {
            "id": item.id,
            "title": item.title,
            "description": item.description,
            "priority": item.priority,
            "status": item.status,
            "source": item.source,
            "synced_to_todo": item.synced_to_todo,
            "todo_id": item.todo_id,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }

    def serialize_update(self, update: OpportunityProgressUpdate | None) -> dict[str, Any] | None:
        if not update:
            return None
        proposed = loads_dict(update.proposed_changes_json)
        return {
            "id": update.id,
            "plan_id": update.plan_id,
            "user_progress_text": update.user_progress_text,
            "progress_assessment": proposed.get("progress_assessment") or loads_dict(update.ai_assessment_json).get("progress_assessment", ""),
            "proposed_changes": proposed,
            "confirmation_status": update.confirmation_status,
            "created_at": update.created_at,
            "confirmed_at": update.confirmed_at,
        }

    def start_web_check_for_task(self, db: Session, task_id: int, request_id: str = "") -> OpportunityProgressWebCheck:
        task = db.get(OpportunityProgressTask, task_id)
        if not task:
            raise ValueError("推进任务不存在")
        existing = self._active_web_check(db, task_id=task.id, plan_id=None)
        if existing:
            return existing
        opportunity = db.get(Opportunity, task.opportunity_id)
        if not opportunity:
            raise ValueError("机会不存在")
        check = OpportunityProgressWebCheck(
            user_id=task.user_id,
            opportunity_id=task.opportunity_id,
            task_id=task.id,
            plan_id=None,
            query=self._web_check_query(opportunity),
            status="searching",
            request_id=request_id,
        )
        db.add(check)
        db.commit()
        db.refresh(check)
        return check

    def start_web_check_for_plan(self, db: Session, plan_id: int, request_id: str = "") -> OpportunityProgressWebCheck:
        plan = db.get(OpportunityProgressPlan, plan_id)
        if not plan:
            raise ValueError("推进计划不存在")
        existing = self._active_web_check(db, task_id=None, plan_id=plan.id)
        if existing:
            return existing
        opportunity = db.get(Opportunity, plan.opportunity_id)
        if not opportunity:
            raise ValueError("机会不存在")
        check = OpportunityProgressWebCheck(
            user_id=plan.user_id,
            opportunity_id=plan.opportunity_id,
            task_id=None,
            plan_id=plan.id,
            query=self._web_check_query(opportunity),
            status="searching",
            request_id=request_id,
        )
        db.add(check)
        db.commit()
        db.refresh(check)
        return check

    def execute_web_check(self, check_id: int) -> None:
        started = time.perf_counter()
        db = SessionLocal()
        check = db.get(OpportunityProgressWebCheck, check_id)
        if not check or check.status != "searching":
            db.close()
            return
        opportunity = db.get(Opportunity, check.opportunity_id)
        user = db.get(UserProfile, check.user_id)
        plan = db.get(OpportunityProgressPlan, check.plan_id) if check.plan_id else None
        task = db.get(OpportunityProgressTask, check.task_id) if check.task_id else None
        if not opportunity:
            check.status = "failed"
            check.error_message = "机会不存在"
            check.updated_at = datetime.utcnow()
            db.commit()
            db.close()
            return
        local_context = {
            "user_profile": self._profile_dict(user) if user else {},
            "opportunity": self._opportunity_dict(opportunity),
            "task": self.serialize_task(task) if task else {},
            "plan": self.serialize_plan(db, plan, include_pending_update=False) if plan else {},
        }
        query = check.query
        db.close()

        sources: list[dict[str, Any]] = []
        status = "completed"
        error_message = ""
        try:
            search_response = self._bocha_search_with_retry(query)
            if search_response.status == "success" and search_response.results:
                sources = [self._web_source_dict(item) for item in search_response.results[:7]]
                analysis = self._run_web_check_analysis(local_context, sources, has_plan=bool(plan))
            else:
                status = "failed"
                error_message = search_response.error_message or "联网搜索暂时失败，当前仍基于本地信息分析。"
                analysis = self._fallback_web_analysis(error_message)
        except Exception as exc:
            logger.exception(
                "opportunity progress web check failed request_id=%s check_id=%s user_id=%s opportunity_id=%s elapsed_ms=%s error_type=%s",
                getattr(check, "request_id", ""),
                check_id,
                getattr(check, "user_id", ""),
                getattr(check, "opportunity_id", ""),
                int((time.perf_counter() - started) * 1000),
                type(exc).__name__,
            )
            status = "failed"
            error_message = "联网搜索暂时失败，当前仍基于本地信息分析。"
            analysis = self._fallback_web_analysis(error_message)

        db = SessionLocal()
        check = db.get(OpportunityProgressWebCheck, check_id)
        if not check:
            db.close()
            return
        check.sources_json = dumps(sources)
        check.analysis_json = dumps(analysis)
        check.status = status if sources or status == "failed" else "partially_completed"
        check.error_message = error_message
        check.updated_at = datetime.utcnow()
        db.commit()
        logger.info(
            "opportunity progress web check finished request_id=%s check_id=%s status=%s source_count=%s elapsed_ms=%s",
            check.request_id,
            check.id,
            check.status,
            len(sources),
            int((time.perf_counter() - started) * 1000),
        )
        db.close()

    def get_web_check(self, db: Session, check_id: int) -> OpportunityProgressWebCheck:
        check = db.get(OpportunityProgressWebCheck, check_id)
        if not check:
            raise ValueError("联网核实任务不存在")
        return check

    def get_latest_web_check(self, db: Session, task_id: int | None = None, plan_id: int | None = None) -> OpportunityProgressWebCheck | None:
        query = db.query(OpportunityProgressWebCheck)
        if task_id:
            query = query.filter(OpportunityProgressWebCheck.task_id == task_id)
        if plan_id:
            query = query.filter(OpportunityProgressWebCheck.plan_id == plan_id)
        return query.order_by(OpportunityProgressWebCheck.updated_at.desc()).first()

    def serialize_web_check(self, check: OpportunityProgressWebCheck | None) -> dict[str, Any] | None:
        if not check:
            return None
        return {
            "id": check.id,
            "user_id": check.user_id,
            "opportunity_id": check.opportunity_id,
            "task_id": check.task_id,
            "plan_id": check.plan_id,
            "query": check.query,
            "status": check.status,
            "sources": self._loads_list(check.sources_json),
            "analysis": loads_dict(check.analysis_json),
            "error_message": check.error_message,
            "request_id": check.request_id,
            "created_at": check.created_at,
            "updated_at": check.updated_at,
        }

    def _build_replan_context(self, db: Session, plan: OpportunityProgressPlan, progress_text: str) -> dict[str, Any]:
        user = db.get(UserProfile, plan.user_id)
        opportunity = db.get(Opportunity, plan.opportunity_id)
        if not opportunity:
            raise ValueError("机会不存在")
        task = db.get(OpportunityProgressTask, plan.source_task_id) if plan.source_task_id else None
        versions = (
            db.query(OpportunityProgressVersion)
            .filter(OpportunityProgressVersion.plan_id == plan.id)
            .order_by(OpportunityProgressVersion.version_number.desc())
            .limit(3)
            .all()
        )
        synced_todos = (
            db.query(Todo)
            .filter(Todo.user_id == plan.user_id, Todo.opportunity_id == plan.opportunity_id)
            .order_by(Todo.updated_at.desc())
            .all()
        )
        latest_memory = (
            db.query(GrowthMemorySnapshot)
            .filter(GrowthMemorySnapshot.user_id == plan.user_id)
            .order_by(GrowthMemorySnapshot.created_at.desc())
            .first()
        )
        return {
            "user_profile": self._profile_dict(user) if user else {},
            "opportunity": self._opportunity_dict(opportunity),
            "growth_memory": self._memory_dict(latest_memory) if latest_memory else {},
            "initial_analysis": loads_dict(task.final_result) if task else {},
            "current_plan": self.serialize_plan(db, plan, include_pending_update=False),
            "current_tasks": [self.serialize_item(item) for item in self._plan_items(db, plan.id)],
            "recent_versions": [self._safe_version_summary(version) for version in versions],
            "user_progress_text": progress_text,
            "synced_todos": [{"id": todo.id, "title": todo.title, "status": todo.status} for todo in synced_todos],
            "similar_opportunities": [self._similar_dict(item) for item in self._similar_opportunities(db, opportunity)],
        }

    def _active_web_check(self, db: Session, task_id: int | None, plan_id: int | None) -> OpportunityProgressWebCheck | None:
        query = db.query(OpportunityProgressWebCheck).filter(OpportunityProgressWebCheck.status == "searching")
        if task_id:
            query = query.filter(OpportunityProgressWebCheck.task_id == task_id)
        if plan_id:
            query = query.filter(OpportunityProgressWebCheck.plan_id == plan_id)
        return query.order_by(OpportunityProgressWebCheck.created_at.desc()).first()

    def _web_check_query(self, opportunity: Opportunity) -> str:
        parts = [
            opportunity.name,
            opportunity.organizer,
            "官方报名",
            "截止时间",
            "最新通知",
        ]
        return " ".join(part for part in parts if part and part != "未注明")

    def _bocha_search_with_retry(self, query: str):
        provider = BochaSearchProvider()
        response = provider.search(query, count=7)
        if response.status == "success" and response.results:
            return response
        message = response.error_message or ""
        retryable = any(code in message for code in ["429", "502", "503", "504", "timeout", "超时"])
        if retryable or response.status == "failed":
            time.sleep(0.8)
            second = provider.search(query, count=7)
            if second.status == "success" or second.results:
                return second
        return response

    def _web_source_dict(self, item: Any) -> dict[str, Any]:
        raw = item.raw or {}
        return {
            "title": item.title,
            "source": raw.get("siteName") or item.source or "bocha",
            "url": item.url,
            "published_at": raw.get("datePublished") or raw.get("published_at") or "",
            "snippet": item.snippet,
        }

    def _run_web_check_analysis(self, local_context: dict[str, Any], sources: list[dict[str, Any]], has_plan: bool) -> dict[str, Any]:
        system = (
            "你是机会推进的信息核实助手。你只能基于本地信息和联网 sources 分析，不得编造外部事实。"
            "必须明确区分本地已有信息、联网补充信息、仍无法确认的信息。"
            "搜索结果不是官方事实，除非来源标题、域名或摘要能合理支持官方/主办方身份。"
            "如果已有推进计划，只能提出计划调整建议，不能直接修改正式计划。只返回严格 JSON。"
        )
        user = {
            "local_context": local_context,
            "web_sources": sources,
            "has_existing_plan": has_plan,
            "required_output": {
                "web_status": "used_web",
                "summary": "联网核实结论",
                "local_info": ["本地已有信息"],
                "web_info": ["联网补充信息"],
                "official_source_assessment": "是否找到更可靠官方来源",
                "registration_update": "报名入口是否有变化",
                "deadline_update": "截止时间是否更新",
                "opportunity_open_status": "机会是否仍开放",
                "conflicts": ["本地和联网信息冲突"],
                "unconfirmed": ["仍无法确认的信息"],
                "plan_adjustment_suggestion": "如果有计划，建议如何调整；否则为空",
                "recommended_next_action": "下一步建议",
            },
        }
        text = self.gateway.chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False, default=str)}],
            temperature=0.2,
            max_retries=1,
            response_format={"type": "json_object"},
        )
        parsed = self._parse_json_object(text, repair=True)
        return {
            "web_status": "used_web",
            "summary": str(parsed.get("summary") or "")[:1200],
            "local_info": self._string_list(parsed.get("local_info"))[:8],
            "web_info": self._string_list(parsed.get("web_info"))[:8],
            "official_source_assessment": str(parsed.get("official_source_assessment") or "")[:800],
            "registration_update": str(parsed.get("registration_update") or "")[:600],
            "deadline_update": str(parsed.get("deadline_update") or "")[:600],
            "opportunity_open_status": str(parsed.get("opportunity_open_status") or "")[:600],
            "conflicts": self._string_list(parsed.get("conflicts"))[:8],
            "unconfirmed": self._string_list(parsed.get("unconfirmed"))[:8],
            "plan_adjustment_suggestion": str(parsed.get("plan_adjustment_suggestion") or "")[:1000],
            "recommended_next_action": str(parsed.get("recommended_next_action") or "")[:800],
        }

    def _fallback_web_analysis(self, message: str) -> dict[str, Any]:
        return {
            "web_status": "failed",
            "summary": "联网搜索暂时失败，当前仍基于本地信息分析。",
            "local_info": [],
            "web_info": [],
            "official_source_assessment": "未能获取稳定联网结果，尚无法补充官方来源判断。",
            "registration_update": "无法确认报名入口是否变化。",
            "deadline_update": "无法确认截止时间是否更新。",
            "opportunity_open_status": "无法确认机会是否仍开放。",
            "conflicts": [],
            "unconfirmed": [message or "联网搜索暂时失败"],
            "plan_adjustment_suggestion": "",
            "recommended_next_action": "继续基于本地信息推进，稍后可重新联网核实。",
        }

    def _run_replan(self, context: dict[str, Any]) -> dict[str, Any]:
        system = (
            "你负责持续帮助用户推进当前机会。你不是普通问答助手，而是根据用户汇报的真实进展，判断当前计划是否需要调整。"
            "必须遵守：只围绕当前机会规划；用户刚提交的进展是本轮最重要的新信息；不要重复建议已经完成的任务；"
            "不要因为一次小变化就重写全部计划；优先进行最小且必要的调整；可以建议新增、修改、暂停或标记任务不再适用；"
            "不得直接删除任务；不得直接终止或放弃推进计划；可以建议完成、暂停或停止，但最终由用户决定；"
            "不得编造外部信息、时间、要求或完成状态；如果用户表达不明确，应保留不确定性；"
            "当前阶段可以自然描述，不限制固定名称；不加入邮件、日历、搜索或不存在的工具；不展示内部推理过程；"
            "输出严格 JSON，所有面向用户的内容使用自然中文。"
        )
        user = {
            "context": context,
            "required_output": {
                "progress_assessment": "对用户最新进展的简短判断",
                "suggested_stage": "AI 建议的自然中文当前阶段",
                "stage_change_reason": "阶段变化原因",
                "tasks_to_keep": [{"task_id": "已有任务 ID", "reason": "为什么继续保留"}],
                "task_changes": [
                    {
                        "task_id": "已有任务 ID",
                        "change_type": "modify | reprioritize | complete | pause | mark_no_longer_applicable",
                        "new_title": None,
                        "new_description": None,
                        "new_priority": None,
                        "reason": "调整原因",
                    }
                ],
                "new_tasks": [{"title": "建议新增任务", "description": "任务说明", "priority": "high | medium | low", "reason": "为什么需要新增"}],
                "suggested_next_focus": "当前最重要的下一步",
                "updated_risks": ["仍需注意的问题"],
                "completion_suggestion": {"suggest_complete": False, "suggest_pause": False, "suggest_stop": False, "reason": None},
                "user_facing_summary": "向用户展示的计划调整总结",
            },
        }
        text = self.gateway.chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False, default=str)}],
            temperature=0.25,
            max_retries=1,
            response_format={"type": "json_object"},
        )
        return self._normalize_replan(self._parse_json_object(text, repair=True), context)

    def _normalize_replan(self, value: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        known_ids = {int(item["id"]) for item in context.get("current_tasks") or [] if str(item.get("id")).isdigit()}
        task_changes = []
        for item in value.get("task_changes") or []:
            if not isinstance(item, dict):
                continue
            task_id = self._int_or_none(item.get("task_id"))
            if not task_id or task_id not in known_ids:
                continue
            change_type = str(item.get("change_type") or "")
            if change_type not in {"modify", "reprioritize", "complete", "pause", "mark_no_longer_applicable"}:
                continue
            task_changes.append(
                {
                    "task_id": task_id,
                    "change_type": change_type,
                    "new_title": str(item.get("new_title") or "")[:120] or None,
                    "new_description": str(item.get("new_description") or "")[:500] or None,
                    "new_priority": item.get("new_priority") if item.get("new_priority") in {"high", "medium", "low"} else None,
                    "reason": str(item.get("reason") or "")[:500],
                }
            )
        tasks_to_keep = []
        for item in value.get("tasks_to_keep") or []:
            if not isinstance(item, dict):
                continue
            task_id = self._int_or_none(item.get("task_id"))
            if task_id and task_id in known_ids:
                tasks_to_keep.append({"task_id": task_id, "reason": str(item.get("reason") or "")[:300]})
        new_tasks = []
        for item in value.get("new_tasks") or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            new_tasks.append(
                {
                    "title": title[:100],
                    "description": str(item.get("description") or "")[:500],
                    "priority": item.get("priority") if item.get("priority") in {"high", "medium", "low"} else "medium",
                    "reason": str(item.get("reason") or "")[:300],
                }
            )
        completion = value.get("completion_suggestion") if isinstance(value.get("completion_suggestion"), dict) else {}
        return {
            "progress_assessment": str(value.get("progress_assessment") or "")[:800],
            "suggested_stage": str(value.get("suggested_stage") or context.get("current_plan", {}).get("current_stage") or "")[:800],
            "stage_change_reason": str(value.get("stage_change_reason") or "")[:600],
            "tasks_to_keep": tasks_to_keep[:10],
            "task_changes": task_changes[:12],
            "new_tasks": new_tasks[:8],
            "suggested_next_focus": str(value.get("suggested_next_focus") or "")[:800],
            "updated_risks": self._string_list(value.get("updated_risks"))[:10],
            "completion_suggestion": {
                "suggest_complete": bool(completion.get("suggest_complete")),
                "suggest_pause": bool(completion.get("suggest_pause")),
                "suggest_stop": bool(completion.get("suggest_stop")),
                "reason": str(completion.get("reason") or "")[:500] or None,
            },
            "user_facing_summary": str(value.get("user_facing_summary") or "")[:1200],
        }

    def _plan_items(self, db: Session, plan_id: int) -> list[OpportunityProgressItem]:
        return (
            db.query(OpportunityProgressItem)
            .filter(OpportunityProgressItem.plan_id == plan_id)
            .order_by(OpportunityProgressItem.id.asc())
            .all()
        )

    def _save_plan_version(self, db: Session, plan: OpportunityProgressPlan, update_id: int | None) -> None:
        snapshot = {
            "plan": {
                "id": plan.id,
                "user_id": plan.user_id,
                "opportunity_id": plan.opportunity_id,
                "goal": plan.goal,
                "overall_judgment": plan.overall_judgment,
                "current_stage": plan.current_stage,
                "plan_summary": plan.plan_summary,
                "next_focus": plan.next_focus,
                "risks": self._loads_list(plan.risks_json),
                "system_status": plan.system_status,
                "version_number": plan.version_number,
            },
            "items": [
                {
                    "id": item.id,
                    "title": item.title,
                    "description": item.description,
                    "priority": item.priority,
                    "status": item.status,
                    "source": item.source,
                    "synced_to_todo": item.synced_to_todo,
                    "todo_id": item.todo_id,
                }
                for item in self._plan_items(db, plan.id)
            ],
        }
        db.add(
            OpportunityProgressVersion(
                plan_id=plan.id,
                version_number=plan.version_number or 1,
                snapshot_json=dumps(snapshot),
                update_id=update_id,
            )
        )

    def _safe_version_summary(self, version: OpportunityProgressVersion) -> dict[str, Any]:
        snapshot = loads_dict(version.snapshot_json)
        plan = snapshot.get("plan") if isinstance(snapshot.get("plan"), dict) else {}
        return {
            "version_number": version.version_number,
            "current_stage": plan.get("current_stage", ""),
            "next_focus": plan.get("next_focus", ""),
            "system_status": plan.get("system_status", ""),
            "created_at": version.created_at.isoformat() if version.created_at else "",
        }

    def _find_similar_todo(self, db: Session, user_id: int, opportunity_id: int, title: str) -> Todo | None:
        todos = db.query(Todo).filter(Todo.user_id == user_id, Todo.opportunity_id == opportunity_id).all()
        if not todos:
            return None
        title_norm = self._compact_text(title)
        for todo in todos:
            if self._compact_text(todo.title) == title_norm:
                return todo
        return todos[0]

    def _has_similar_plan_item(self, items: Any, title: str) -> bool:
        title_norm = self._compact_text(title)
        return any(self._compact_text(item.title) == title_norm for item in items)

    def _todo_description_from_plan_item(self, item: OpportunityProgressItem, opportunity: Opportunity) -> str:
        parts = [
            item.description or "来自推进计划的关键任务。",
            f"关联机会：{opportunity.name}",
            "说明：该待办由用户主动从推进计划同步，不代表官方新增要求。",
        ]
        return "\n".join(part for part in parts if part)

    def _log_event(self, db: Session, user_id: int, event_type: str, opportunity_id: int, metadata: dict[str, Any]) -> None:
        db.add(
            UserEvent(
                user_id=user_id,
                event_type=event_type,
                event_target=str(opportunity_id),
                event_metadata=dumps({"opportunity_id": opportunity_id, **metadata}),
            )
        )

    def _compact_text(self, value: str) -> str:
        return re.sub(r"\s+", "", (value or "").lower())

    def _int_or_none(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _build_context(self, db: Session, user: UserProfile, opportunity: Opportunity) -> dict[str, Any]:
        article = db.get(Article, opportunity.article_id) if opportunity.article_id else None
        latest_memory = (
            db.query(GrowthMemorySnapshot)
            .filter(GrowthMemorySnapshot.user_id == user.id)
            .order_by(GrowthMemorySnapshot.created_at.desc())
            .first()
        )
        return {
            "user_profile": self._profile_dict(user),
            "opportunity": self._opportunity_dict(opportunity),
            "source_article": self._source_article_dict(article, opportunity),
            "growth_memory": self._memory_dict(latest_memory) if latest_memory else {},
            "similar_opportunities": [self._similar_dict(item) for item in self._similar_opportunities(db, opportunity)],
        }

    def _run_round(self, context: dict[str, Any], rounds: list[dict[str, Any]], next_focus: str, round_index: int) -> dict[str, Any]:
        system = (
            "你是个人机会推进顾问。你只能基于输入中的项目已有数据做分析，不能声称访问了外部官网、搜索引擎或微信公众号。"
            "你需要连续分析当前机会，判断下一轮最值得分析什么，并决定是否停止。"
            "不要输出推理过程。只返回严格 JSON。"
        )
        user = {
            "round": round_index,
            "max_rounds": MAX_ROUNDS,
            "current_focus": next_focus,
            "context": context,
            "previous_rounds": rounds,
            "required_output": {
                "stage": "initial_analysis/source_check/similar_comparison/user_fit/risk_analysis/action_planning/final_summary",
                "stage_title": "中文阶段标题",
                "round_summary": "本轮得到的简短结论",
                "findings": ["面向用户的发现"],
                "uncertainties": ["尚不确定的信息"],
                "next_focus": "下一轮分析方向",
                "should_continue": True,
                "stop_reason": None,
            },
            "rules": [
                "最多 5 轮，信息足够时提前停止。",
                "信息源核查只能分析已有来源、链接、发布时间、字段一致性。",
                "如果只能确认文章来源，必须说明当前只能确认文章来源，尚无法确认官方信息。",
                "没有本地同类机会时跳过比较并说明数据不足。",
            ],
        }
        text = self.gateway.chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
            temperature=0.25,
            max_retries=1,
            response_format={"type": "json_object"},
        )
        parsed = self._parse_json_object(text, repair=True)
        return self._normalize_round(parsed, next_focus, round_index)

    def _run_final(self, context: dict[str, Any], rounds: list[dict[str, Any]]) -> dict[str, Any]:
        system = (
            "你是个人机会推进顾问。请基于已有多轮阶段结论，形成最终推进方案。"
            "不能编造外部核验结果，不能声称访问了输入之外的网站。只返回严格 JSON。"
        )
        user = {
            "context": context,
            "rounds": rounds,
            "required_output": {
                "overall_judgment": "值得优先推进 | 可以推进 | 建议观察 | 暂不建议推进",
                "summary": "整体判断",
                "source_assessment": "现有信息源可靠程度说明",
                "similar_opportunity_comparison": "本地同类机会比较，没有数据时说明无法比较",
                "user_fit": "用户匹配程度判断",
                "advantages": ["主要优势"],
                "risks_and_uncertainties": ["风险或尚未确认的信息"],
                "recommended_strategy": "整体推进策略",
                "suggested_todos": [{"title": "待办标题", "description": "待办说明"}],
                "should_create_todos": True,
                "stop_reason": None,
            },
        }
        text = self.gateway.chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
            temperature=0.25,
            max_retries=1,
            response_format={"type": "json_object"},
        )
        parsed = self._parse_json_object(text, repair=True)
        return self._normalize_final(parsed)

    def _normalize_round(self, value: dict[str, Any], fallback_stage: str, round_index: int) -> dict[str, Any]:
        stage = str(value.get("stage") or fallback_stage)
        if stage not in ALLOWED_NEXT_FOCUS:
            stage = fallback_stage if fallback_stage in ALLOWED_NEXT_FOCUS else "initial_analysis"
        next_focus = str(value.get("next_focus") or "action_planning")
        if next_focus not in ALLOWED_NEXT_FOCUS:
            next_focus = "action_planning"
        should_continue = bool(value.get("should_continue")) and round_index < MAX_ROUNDS
        return {
            "stage": stage,
            "stage_title": str(value.get("stage_title") or ROUND_STAGE_LABELS.get(stage) or "继续分析"),
            "round_summary": str(value.get("round_summary") or "")[:600],
            "findings": self._string_list(value.get("findings"))[:6],
            "uncertainties": self._string_list(value.get("uncertainties"))[:6],
            "next_focus": next_focus,
            "should_continue": should_continue,
            "stop_reason": str(value.get("stop_reason") or "") or None,
        }

    def _normalize_final(self, value: dict[str, Any]) -> dict[str, Any]:
        judgment = str(value.get("overall_judgment") or "建议观察")
        if judgment not in FINAL_JUDGMENTS:
            judgment = "建议观察"
        todos = []
        for item in value.get("suggested_todos") or []:
            if isinstance(item, dict):
                title = str(item.get("title") or "").strip()
                description = str(item.get("description") or "").strip()
                if title:
                    todos.append({"title": title[:80], "description": description[:240]})
        should_create = bool(value.get("should_create_todos")) and judgment in {"值得优先推进", "可以推进"} and bool(todos)
        return {
            "overall_judgment": judgment,
            "summary": str(value.get("summary") or "")[:900],
            "source_assessment": str(value.get("source_assessment") or "")[:700],
            "similar_opportunity_comparison": str(value.get("similar_opportunity_comparison") or "")[:700],
            "user_fit": str(value.get("user_fit") or "")[:700],
            "advantages": self._string_list(value.get("advantages"))[:6],
            "risks_and_uncertainties": self._string_list(value.get("risks_and_uncertainties"))[:6],
            "recommended_strategy": str(value.get("recommended_strategy") or "")[:900],
            "suggested_todos": todos[:6],
            "should_create_todos": should_create,
            "stop_reason": str(value.get("stop_reason") or "") or None,
        }

    def _profile_dict(self, user: UserProfile) -> dict[str, Any]:
        return {
            "专业方向": user.major_direction,
            "年级/身份": user.grade_identity,
            "当前目标": loads_list(user.current_goals),
            "技能": loads_list(user.skills),
            "感兴趣领域": loads_list(user.interested_fields),
            "不感兴趣内容": loads_list(user.disliked_contents),
            "详细需求": user.detailed_needs,
            "时间偏好": user.time_preference,
            "地点偏好": user.location_preference,
        }

    def _opportunity_dict(self, opportunity: Opportunity) -> dict[str, Any]:
        return {
            "id": opportunity.id,
            "名称": opportunity.name,
            "类别": opportunity.category,
            "主办方": opportunity.organizer,
            "活动时间": opportunity.event_time,
            "截止时间": opportunity.deadline,
            "地点": opportunity.location,
            "面向人群": opportunity.target_audience,
            "要求": opportunity.requirements,
            "摘要": opportunity.summary,
            "原文链接": opportunity.link,
            "官方链接": opportunity.official_url,
            "报名链接": opportunity.registration_url,
            "核验状态": opportunity.verification_status,
            "风险等级": opportunity.risk_level,
        }

    def _source_article_dict(self, article: Article | None, opportunity: Opportunity) -> dict[str, Any]:
        return {
            "文章标题": article.title if article else opportunity.name,
            "来源": article.source if article else "",
            "发布时间": article.published_at if article else "",
            "原文链接": (article.url if article else "") or opportunity.link,
            "正文节选": ((article.content if article else "") or opportunity.summary or "")[:1500],
        }

    def _memory_dict(self, memory: GrowthMemorySnapshot) -> dict[str, Any]:
        return {
            "当前阶段": memory.current_stage,
            "近期关注重点": memory.recent_focus,
            "判断标准与偏好变化": memory.preference_changes,
            "整体观察": memory.overall_observation,
            "下一阶段建议": memory.next_stage_advice,
        }

    def _similar_opportunities(self, db: Session, opportunity: Opportunity) -> list[Opportunity]:
        keywords = set(self._keywords(" ".join([opportunity.name or "", opportunity.summary or "", opportunity.requirements or ""])))
        candidates = (
            db.query(Opportunity)
            .filter(Opportunity.id != opportunity.id)
            .order_by(Opportunity.created_at.desc())
            .limit(60)
            .all()
        )
        scored = []
        for item in candidates:
            score = 2 if item.category == opportunity.category else 0
            item_keywords = set(self._keywords(" ".join([item.name or "", item.summary or "", item.requirements or ""])))
            score += len(keywords & item_keywords)
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:5]]

    def _similar_dict(self, opportunity: Opportunity) -> dict[str, Any]:
        return {
            "名称": opportunity.name,
            "类别": opportunity.category,
            "截止时间": opportunity.deadline,
            "面向人群": opportunity.target_audience,
            "要求": opportunity.requirements[:200] if opportunity.requirements else "",
            "摘要": opportunity.summary[:220] if opportunity.summary else "",
            "信息完整度": "较完整" if (opportunity.deadline and (opportunity.registration_url or opportunity.official_url or opportunity.link)) else "有限",
        }

    def _parse_json_object(self, text: str, repair: bool = False) -> dict[str, Any]:
        cleaned = (text or "").strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
            cleaned = re.sub(r"```$", "", cleaned).strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end >= start:
            cleaned = cleaned[start : end + 1]
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            if not repair:
                raise RuntimeError("model_json_error")
            fixed = self.gateway.chat(
                messages=[
                    {"role": "system", "content": "你是 JSON 格式修复器。只输出合法 JSON 对象，不要 Markdown，不要解释。"},
                    {"role": "user", "content": f"请修复下面内容为合法 JSON 对象，保留原字段含义：\n{cleaned[:4000]}"},
                ],
                temperature=0.0,
                max_retries=1,
                response_format={"type": "json_object"},
            )
            return self._parse_json_object(fixed, repair=False)
        if not isinstance(parsed, dict):
            raise RuntimeError("model_json_error")
        return parsed

    def _should_stop_rounds(self, rounds: list[dict[str, Any]]) -> bool:
        if not rounds:
            return False
        latest = rounds[-1]
        if not latest.get("should_continue") or latest.get("next_focus") == "final_summary":
            return True
        if len(rounds) >= MAX_ROUNDS:
            return True
        if len(rounds) >= 2:
            previous = rounds[-2]
            if previous.get("next_focus") == latest.get("next_focus") == latest.get("stage"):
                return True
            previous_findings = set(previous.get("findings") or [])
            latest_findings = set(latest.get("findings") or [])
            if latest_findings and latest_findings <= previous_findings:
                return True
        summary = " ".join([str(latest.get("round_summary") or ""), " ".join(latest.get("uncertainties") or [])])
        if "信息不足" in summary and not latest.get("findings"):
            return True
        return False

    def _analysis_error_message(self, exc: Exception) -> str:
        text = str(exc)
        if "model_json_error" in text or "JSON" in text:
            return "AI 返回格式异常，请稍后重试。"
        if "database is locked" in text.lower():
            return "系统正在处理其他任务，请稍后重试。"
        if "LLM" in text or "llm" in text:
            return "AI 服务暂时不可用，请稍后重试。"
        return "AI 推进分析失败，请稍后重试。"

    def _fallback_final_from_rounds(self, context: dict[str, Any], rounds: list[dict[str, Any]]) -> dict[str, Any]:
        findings = []
        risks = []
        for item in rounds:
            findings.extend(self._string_list(item.get("findings")))
            risks.extend(self._string_list(item.get("uncertainties")))
        opportunity = context.get("opportunity") or {}
        name = opportunity.get("名称") or "当前机会"
        return {
            "overall_judgment": "建议观察",
            "summary": f"已完成部分推进分析，但 AI 服务在最终汇总时暂时不可用。基于已完成阶段，{name} 可以先保留观察并补齐关键信息。",
            "source_assessment": "当前只能基于项目已有数据判断；如缺少官方链接或报名入口，应先确认信息来源。",
            "similar_opportunity_comparison": "本轮未能完成完整同类机会比较，可在服务稳定后重新分析。",
            "user_fit": "已完成阶段显示存在一定参考价值，但匹配程度仍建议结合个人目标再确认。",
            "advantages": list(dict.fromkeys(findings))[:5] or ["已获得部分阶段分析结论"],
            "risks_and_uncertainties": list(dict.fromkeys(risks))[:6] or ["最终汇总未完成，建议稍后重新分析"],
            "recommended_strategy": "先确认官方入口、截止时间和硬性要求，再决定是否建立推进计划。",
            "suggested_todos": [
                {"title": "确认机会关键信息", "description": "阅读原文，确认官方入口、截止时间和硬性要求。"}
            ],
            "should_create_todos": False,
            "stop_reason": "AI 服务在最终汇总时暂时不可用，已保留部分分析结果。",
        }

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _keywords(self, text: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}|[\u4e00-\u9fff]{2,}", text or "")
        stopwords = {"机会", "招募", "通知", "报名", "项目", "相关", "官方", "申请", "要求"}
        result = []
        for token in tokens:
            if token not in stopwords and token not in result:
                result.append(token)
        return result[:12]

    def _loads_list(self, value: str | None) -> list[Any]:
        try:
            parsed = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []

    def _loads_list_of_dicts(self, value: str | None) -> list[dict[str, Any]]:
        return [item for item in self._loads_list(value) if isinstance(item, dict)]
