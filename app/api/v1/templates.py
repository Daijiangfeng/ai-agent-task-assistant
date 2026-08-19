"""
任务模板 / Agent Skill API 路由。

提供内置与自定义模板的 CRUD，以及"模板 -> 创建任务"能力。
用户可直接选择模板（市场调研 / 文档分析 / 代码审查 / 通用），
无需每次从零规划。
"""

from fastapi import APIRouter, Depends, Query

from app.api.auth import get_current_user
from app.api.deps import (
    get_task_queue,
    get_task_service,
    get_template_service_dep,
    require_ready,
)
from app.api.errors import (
    QueueUnavailableException,
    TemplateNotFoundException,
)
from app.config.logging import get_logger
from app.models.api_schemas import (
    AgentTemplateCreate,
    AgentTemplateResponse,
    AgentTemplateUpdate,
    TaskResponse,
    TemplateListResponse,
    TemplateRunRequest,
)
from app.queue.base import QueueFullError, TaskMessage, TaskQueue
from app.services.task_service import TaskService
from app.services.template_service import TemplateService
from app.tools.security import ToolContext

logger = get_logger(__name__)

router = APIRouter(prefix="/templates", tags=["templates"])


def _to_response(
    template_service: TemplateService, template
) -> AgentTemplateResponse:
    return template_service.to_response(template)


@router.get("/", response_model=TemplateListResponse)
async def list_templates(
    category: str | None = Query(
        default=None,
        description=(
            "按类别过滤"
            "（market_research/document_analysis/code_review/general 等）"
        ),
    ),
    template_service: TemplateService = Depends(get_template_service_dep),
    user: ToolContext = Depends(get_current_user),
):
    """列出全部任务模板（内置 + 自定义）。"""
    templates = template_service.list_templates(category=category)
    return TemplateListResponse(
        total=len(templates),
        templates=[_to_response(template_service, t) for t in templates],
    )


@router.post("/", response_model=AgentTemplateResponse, status_code=201)
async def create_template(
    request: AgentTemplateCreate,
    template_service: TemplateService = Depends(get_template_service_dep),
    user: ToolContext = Depends(get_current_user),
):
    """创建自定义任务模板。"""
    template = template_service.create_template(request)
    return _to_response(template_service, template)


@router.get("/{template_id}", response_model=AgentTemplateResponse)
async def get_template(
    template_id: str,
    template_service: TemplateService = Depends(get_template_service_dep),
    user: ToolContext = Depends(get_current_user),
):
    """获取单个任务模板详情（含变量列表）。"""
    template = template_service.get_template(template_id)
    if template is None:
        raise TemplateNotFoundException(template_id)
    return _to_response(template_service, template)


@router.put("/{template_id}", response_model=AgentTemplateResponse)
async def update_template(
    template_id: str,
    request: AgentTemplateUpdate,
    template_service: TemplateService = Depends(get_template_service_dep),
    user: ToolContext = Depends(get_current_user),
):
    """更新自定义任务模板（内置模板禁止更新）。"""
    template = template_service.update_template(template_id, request)
    if template is None:
        raise TemplateNotFoundException(template_id)
    return _to_response(template_service, template)


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: str,
    template_service: TemplateService = Depends(get_template_service_dep),
    user: ToolContext = Depends(get_current_user),
):
    """删除自定义任务模板（内置模板禁止删除）。"""
    if not template_service.delete_template(template_id):
        raise TemplateNotFoundException(template_id)
    return None


@router.post(
    "/{template_id}/run",
    response_model=TaskResponse,
    status_code=201,
    dependencies=[Depends(require_ready)],
)
async def run_template(
    template_id: str,
    request: TemplateRunRequest | None = None,
    template_service: TemplateService = Depends(get_template_service_dep),
    task_service: TaskService = Depends(get_task_service),
    task_queue: TaskQueue = Depends(get_task_queue),
    user: ToolContext = Depends(get_current_user),
):
    """
    基于模板创建任务（渲染 {var} 变量）。

    - **inputs**: 模板变量值（如 {"topic": "AI 行业", "language": "中文"}）
    - **auto_execute**: 创建后是否立即入队执行
    """
    request = request or TemplateRunRequest()
    template = template_service.get_template(template_id)
    if template is None:
        raise TemplateNotFoundException(template_id)
    if not request.inputs and template.variables():
        logger.info(
            "模板创建任务缺少变量输入",
            template_id=template_id,
            variables=template.variables(),
        )
    try:
        task_id = await template_service.create_task_from_template(
            template_id,
            inputs=request.inputs or {},
            task_service=task_service,
            owner_id=user.user_id,
            tenant_id=user.tenant_id,
        )
    except ValueError as e:
        from app.api.errors import AppException

        raise AppException(message=str(e)) from e

    task = await task_service.get_task(task_id)

    if request.auto_execute:
        try:
            await task_queue.enqueue(
                TaskMessage(
                    task_id=task_id,
                    goal=task.goal,
                    context=task.context,
                    tool_context=user.to_dict(),
                )
            )
        except QueueFullError as e:
            logger.warning("模板创建任务入队失败：队列已满", task_id=task_id)
            raise QueueUnavailableException(str(e)) from e

    return await task_service.to_task_response(task)
