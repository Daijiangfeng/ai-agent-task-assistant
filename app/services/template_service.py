"""
任务模板服务（Agent Skill / Workflow Template）。

提供内置模板（市场调研 / 文档分析 / 代码审查 / 通用）与自定义模板的 CRUD，
以及"模板 -> 任务"的渲染创建能力。

存储：进程内（内置模板随代码分发；自定义模板可后续扩展持久化后端）。
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from app.config.logging import get_logger
from app.models.api_schemas import (
    AgentTemplateCreate,
    AgentTemplateResponse,
    AgentTemplateUpdate,
)
from app.models.template import AgentTemplate
from app.services.task_service import TaskService

logger = get_logger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# 内置模板种子数据（{var} 为创建任务时由用户注入的变量）
BUILTIN_TEMPLATES: list[dict] = [
    {
        "id": "market_research",
        "name": "市场调研",
        "description": "搜索行业资料 -> 整理数据 -> 分析趋势 -> 生成结构化调研报告",
        "category": "market_research",
        "goal_template": (
            "针对「{topic}」开展市场调研：搜索行业最新资料并整理关键数据，"
            "分析「{aspect}」发展趋势，最终生成一份结构化调研报告（Markdown，"
            "含市场规模、竞争格局、趋势研判与结论建议）。"
        ),
        "context_template": (
            "调研背景：{background}\n"
            "重点关注：{focus}；报告语言：{language}（默认中文）。"
        ),
        "tags": ["调研", "报告"],
        "is_builtin": True,
    },
    {
        "id": "document_analysis",
        "name": "文档分析",
        "description": "读取文档 -> RAG 知识增强 -> 摘要 -> 提取实体 -> 风险分析",
        "category": "document_analysis",
        "goal_template": (
            "分析文档「{document_path}」：读取并解析文档内容，结合知识库检索增强理解，"
            "生成文档摘要，提取关键实体，并输出「{risk_focus}」相关的风险点分析。"
        ),
        "context_template": "文档路径：{document_path}\n分析重点：{risk_focus}",
        "tags": ["文档", "摘要", "风险"],
        "is_builtin": True,
    },
    {
        "id": "code_review",
        "name": "代码审查",
        "description": "读取代码 -> 静态分析 -> 安全分析 -> 测试建议 -> Review 结论",
        "category": "code_review",
        "goal_template": (
            "审查代码「{code_path}」：读取并理解代码结构与逻辑，进行静态分析与"
            "安全隐患排查，给出测试补充建议，最终输出 Review 结论与改进清单。"
        ),
        "context_template": "审查范围：{code_path}\n特别关注：{focus_area}",
        "tags": ["代码", "审查", "安全"],
        "is_builtin": True,
    },
    {
        "id": "general",
        "name": "通用任务",
        "description": "标准 Agent 流程：规划 -> 执行 -> 反思，适用于任意目标",
        "category": "general",
        "goal_template": "{goal}",
        "context_template": "{context}",
        "tags": ["通用"],
        "is_builtin": True,
    },
]


class TemplateService:
    """任务模板管理服务（内置模板 + 自定义模板）。"""

    def __init__(self) -> None:
        self._templates: dict[str, AgentTemplate] = {}
        self._lock = threading.Lock()
        now = _utcnow_iso()
        for data in BUILTIN_TEMPLATES:
            template = AgentTemplate(
                **data,
                created_at=now,
                updated_at=now,
            )
            self._templates[template.id] = template

    # ---- CRUD ----

    def list_templates(self, category: str | None = None) -> list[AgentTemplate]:
        """列出全部模板，可按类别过滤。"""
        with self._lock:
            templates = list(self._templates.values())
        templates.sort(key=lambda t: (not t.is_builtin, t.name))
        if category:
            templates = [t for t in templates if t.category == category]
        return templates

    def get_template(self, template_id: str) -> AgentTemplate | None:
        """按 ID 获取模板。"""
        with self._lock:
            return self._templates.get(template_id)

    def create_template(self, request: AgentTemplateCreate) -> AgentTemplate:
        """创建自定义模板。"""
        now = _utcnow_iso()
        template = AgentTemplate(
            id=f"custom_{uuid.uuid4().hex[:12]}",
            name=request.name,
            description=request.description,
            category=request.category,
            goal_template=request.goal_template,
            context_template=request.context_template,
            tags=list(request.tags),
            is_builtin=False,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._templates[template.id] = template
        logger.info("TemplateService: 模板创建", template_id=template.id)
        return template

    def update_template(
        self, template_id: str, request: AgentTemplateUpdate
    ) -> AgentTemplate | None:
        """更新模板（内置模板禁止更新，返回 None）。"""
        with self._lock:
            template = self._templates.get(template_id)
            if template is None or template.is_builtin:
                return None
            data = request.model_dump(exclude_unset=True)
            for key, value in data.items():
                if value is not None:
                    setattr(template, key, value)
            template.updated_at = _utcnow_iso()
            return template

    def delete_template(self, template_id: str) -> bool:
        """删除自定义模板（内置模板禁止删除）。"""
        with self._lock:
            template = self._templates.get(template_id)
            if template is None or template.is_builtin:
                return False
            del self._templates[template_id]
            return True

    # ---- 渲染与建任务 ----

    async def create_task_from_template(
        self,
        template_id: str,
        inputs: dict[str, str],
        task_service: TaskService,
        owner_id: str = "anonymous",
        tenant_id: str = "default",
    ) -> str:
        """
        基于模板创建任务。

        Args:
            template_id: 模板 ID。
            inputs: 模板变量值，渲染 goal/context。
            task_service: 任务服务。
            owner_id / tenant_id: 任务归属。

        Returns:
            新任务 ID。

        Raises:
            ValueError: 模板不存在。
        """
        template = self.get_template(template_id)
        if template is None:
            raise ValueError(f"任务模板 {template_id} 不存在")
        goal, context = template.render(inputs or {})
        if not goal:
            missing = ", ".join(template.variables())
            raise ValueError(f"模板变量缺失，无法渲染目标: {missing}")
        task_id = await task_service.create_task(
            goal=goal,
            context=context,
            owner_id=owner_id,
            tenant_id=tenant_id,
        )
        logger.info(
            "TemplateService: 模板创建任务",
            template_id=template_id,
            task_id=task_id,
        )
        return task_id

    # ---- 响应转换 ----

    def to_response(self, template: AgentTemplate) -> AgentTemplateResponse:
        return AgentTemplateResponse(
            id=template.id,
            name=template.name,
            description=template.description,
            category=template.category,
            goal_template=template.goal_template,
            context_template=template.context_template,
            tags=list(template.tags),
            variables=template.variables(),
            is_builtin=template.is_builtin,
            created_at=template.created_at,
            updated_at=template.updated_at,
        )


_service: TemplateService | None = None
_service_lock = threading.Lock()


def get_template_service() -> TemplateService:
    """获取模板服务单例。"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = TemplateService()
    return _service
