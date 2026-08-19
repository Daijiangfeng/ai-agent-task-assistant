"""
任务相关 API 路由。
提供任务的创建、查询、列表、生命周期控制（暂停/恢复/取消/重试）与
Human-in-the-loop 审批决策功能。
"""

from fastapi import APIRouter, Depends, Query

from app.api.auth import can_access_task, get_current_user
from app.api.deps import get_task_queue, get_task_service, require_ready
from app.api.errors import (
    ApprovalAlreadyDecidedException,
    ApprovalNotFoundException,
    QueueUnavailableException,
    TaskForbiddenException,
    TaskNotFoundException,
    TaskStateException,
)
from app.config.logging import get_logger
from app.models.api_schemas import (
    ApprovalDecideRequest,
    CreateTaskRequest,
    RetryTaskRequest,
    TaskListResponse,
    TaskResponse,
    TaskStatusResponse,
)
from app.models.task import RESTARTABLE_STATUSES, ApprovalRequest, ApprovalStatus, TaskStatus
from app.queue.base import QueueFullError, TaskMessage, TaskQueue
from app.services.task_control import get_task_control
from app.services.task_service import TaskService
from app.tools.security import ROLE_ADMIN, ToolContext

logger = get_logger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post(
    "/",
    response_model=TaskResponse,
    status_code=201,
    dependencies=[Depends(require_ready)],
)
async def create_task(
    request: CreateTaskRequest,
    task_service: TaskService = Depends(get_task_service),
    user: ToolContext = Depends(get_current_user),
):
    """
    创建新的 Agent 任务。

    - **goal**: 用户目标描述
    - **context**: 可选的上下文信息
    """
    task_id = await task_service.create_task(
        goal=request.goal,
        context=request.context,
        owner_id=user.user_id,
        tenant_id=user.tenant_id,
    )
    task = await task_service.get_task(task_id)
    return await task_service.to_task_response(task)


@router.get("/", response_model=TaskListResponse)
async def list_tasks(
    limit: int = Query(default=20, ge=1, le=100, description="返回数量限制"),
    offset: int = Query(default=0, ge=0, description="偏移量"),
    status: TaskStatus | None = Query(
        default=None, description="按状态过滤（如 awaiting_approval）"
    ),
    task_service: TaskService = Depends(get_task_service),
    user: ToolContext = Depends(get_current_user),
):
    """列表查询任务（默认仅返回本人任务，admin 返回全部）。"""
    owner_id = None if user.role == ROLE_ADMIN else user.user_id
    tenant_id = None if user.role == ROLE_ADMIN else user.tenant_id
    tasks = await task_service.list_tasks(
        limit=limit, offset=offset, owner_id=owner_id, tenant_id=tenant_id, status=status
    )
    total = await task_service.get_task_count(
        owner_id=owner_id, tenant_id=tenant_id
    )

    task_responses = [
        await task_service.to_task_response(t) for t in tasks
    ]

    return TaskListResponse(total=total, tasks=task_responses)


@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(
    task_id: str,
    task_service: TaskService = Depends(get_task_service),
    user: ToolContext = Depends(get_current_user),
):
    """
    查询任务执行状态和进度。

    返回任务当前状态、进度百分比、当前步骤等信息。
    """
    task = await task_service.get_task(task_id)
    if task is None:
        raise TaskNotFoundException(task_id)
    if not can_access_task(task, user):
        raise TaskForbiddenException(task_id)

    response = await task_service.get_task_status_response(task_id)
    if response is None:
        raise TaskNotFoundException(task_id)
    return response


# ---------------------------------------------------------------------------
# 任务生命周期控制：暂停 / 恢复 / 取消 / 重试
# ---------------------------------------------------------------------------


def _allowed_statuses(values: set[TaskStatus]) -> list[str]:
    return sorted(v.value for v in values)


@router.post("/{task_id}/pause", response_model=TaskStatusResponse)
async def pause_task(
    task_id: str,
    task_service: TaskService = Depends(get_task_service),
    user: ToolContext = Depends(get_current_user),
):
    """
    暂停任务（节点边界生效）。

    Worker 完成当前节点后停止执行；LangGraph Checkpoint 保留进度，
    可通过 resume 断点续跑。仅运行中（planning/executing/reflecting/replanning/
    awaiting_approval）的任务可暂停。
    """
    task = await task_service.get_task(task_id)
    if task is None:
        raise TaskNotFoundException(task_id)
    if not can_access_task(task, user):
        raise TaskForbiddenException(task_id)

    running = {
        TaskStatus.PLANNING,
        TaskStatus.EXECUTING,
        TaskStatus.REFLECTING,
        TaskStatus.REPLANNING,
        TaskStatus.AWAITING_APPROVAL,
    }
    if task.status not in running:
        raise TaskStateException(
            task_id=task_id,
            current_status=task.status.value,
            allowed_statuses=_allowed_statuses(running),
        )

    get_task_control().request_pause(task_id)
    logger.info("任务暂停请求已登记", task_id=task_id, operator=user.user_id)
    task = await task_service.get_task(task_id)
    response = await task_service.get_task_status_response(task_id)
    if response is None:
        raise TaskNotFoundException(task_id)
    return response


@router.post(
    "/{task_id}/resume",
    response_model=TaskResponse,
    dependencies=[Depends(require_ready)],
)
async def resume_task(
    task_id: str,
    task_service: TaskService = Depends(get_task_service),
    task_queue: TaskQueue = Depends(get_task_queue),
    user: ToolContext = Depends(get_current_user),
):
    """
    恢复暂停/取消/失败的任务（断点续跑）。

    清除暂停控制请求后重新入队；Worker 从 LangGraph Checkpoint
    （thread_id=task_id）继续执行未完成节点。
    """
    task = await task_service.get_task(task_id)
    if task is None:
        raise TaskNotFoundException(task_id)
    if not can_access_task(task, user):
        raise TaskForbiddenException(task_id)

    resumable = {
        TaskStatus.PAUSED,
        TaskStatus.CANCELLED,
        TaskStatus.FAILED,
        TaskStatus.COMPLETED,
        TaskStatus.PENDING,
    }
    if task.status not in resumable:
        raise TaskStateException(
            task_id=task_id,
            current_status=task.status.value,
            allowed_statuses=_allowed_statuses(resumable),
        )

    get_task_control().clear(task_id)
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
        raise QueueUnavailableException(str(e)) from e

    await task_service.update_task_status(task_id, TaskStatus.PLANNING)
    task = await task_service.get_task(task_id)
    return await task_service.to_task_response(task)


@router.post("/{task_id}/cancel", response_model=TaskStatusResponse)
async def cancel_task(
    task_id: str,
    task_service: TaskService = Depends(get_task_service),
    user: ToolContext = Depends(get_current_user),
):
    """
    取消任务（节点边界生效）。

    仅非终态（pending/planning/executing/reflecting/replanning/awaiting_approval/
    paused）任务可取消；取消后可通过 resume 重新执行。
    """
    task = await task_service.get_task(task_id)
    if task is None:
        raise TaskNotFoundException(task_id)
    if not can_access_task(task, user):
        raise TaskForbiddenException(task_id)

    terminal = {TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED}
    if task.status in terminal:
        raise TaskStateException(
            task_id=task_id,
            current_status=task.status.value,
            allowed_statuses=_allowed_statuses(
                {s for s in TaskStatus if s not in terminal}
            ),
        )

    get_task_control().request_cancel(task_id)
    # 待审批任务：取消后需恢复执行环境，清除审批请求
    if task.status == TaskStatus.AWAITING_APPROVAL and task.pending_approval:
        await task_service.resolve_approval(
            task_id,
            task.pending_approval.id,
            ApprovalStatus.REJECTED,
            note="任务被取消",
        )
    logger.info("任务取消请求已登记", task_id=task_id, operator=user.user_id)
    response = await task_service.get_task_status_response(task_id)
    if response is None:
        raise TaskNotFoundException(task_id)
    return response


@router.post(
    "/{task_id}/retry",
    response_model=TaskResponse,
    dependencies=[Depends(require_ready)],
)
async def retry_task(
    task_id: str,
    request: RetryTaskRequest | None = None,
    task_service: TaskService = Depends(get_task_service),
    task_queue: TaskQueue = Depends(get_task_queue),
    user: ToolContext = Depends(get_current_user),
):
    """
    重试任务。

    - 不传 from_index：清空结果从头重新规划执行（重置检查点）；
    - 传 from_index：基于已有计划从该子任务索引继续执行（失败节点恢复）。
    """
    task = await task_service.get_task(task_id)
    if task is None:
        raise TaskNotFoundException(task_id)
    if not can_access_task(task, user):
        raise TaskForbiddenException(task_id)

    if task.status not in RESTARTABLE_STATUSES:
        raise TaskStateException(
            task_id=task_id,
            current_status=task.status.value,
            allowed_statuses=_allowed_statuses(set(RESTARTABLE_STATUSES)),
        )

    from_index = (request or RetryTaskRequest()).from_index
    if from_index is not None:
        plan_subtasks = task.subtasks
        if from_index >= len(plan_subtasks):
            raise TaskStateException(
                task_id=task_id,
                current_status=task.status.value,
                allowed_statuses=[],
            )

    await task_service.reset_task_for_retry(task_id, from_index=from_index)
    get_task_control().clear(task_id)
    try:
        await task_queue.enqueue(
            TaskMessage(
                task_id=task_id,
                goal=task.goal,
                context=task.context,
                tool_context=user.to_dict(),
                action="execute",
                payload={"retry_from_index": from_index},
            )
        )
    except QueueFullError as e:
        raise QueueUnavailableException(str(e)) from e

    await task_service.update_task_status(task_id, TaskStatus.PLANNING)
    task = await task_service.get_task(task_id)
    return await task_service.to_task_response(task)


# ---------------------------------------------------------------------------
# Human-in-the-loop：审批请求查看与决策
# ---------------------------------------------------------------------------


@router.get("/{task_id}/approvals", response_model=list[ApprovalRequest])
async def list_approvals(
    task_id: str,
    task_service: TaskService = Depends(get_task_service),
    user: ToolContext = Depends(get_current_user),
):
    """查看任务的全部审批请求（含历史已决策的）。"""
    task = await task_service.get_task(task_id)
    if task is None:
        raise TaskNotFoundException(task_id)
    if not can_access_task(task, user):
        raise TaskForbiddenException(task_id)
    return task.approval_history or []


@router.post(
    "/{task_id}/approvals/{approval_id}/approve",
    response_model=TaskStatusResponse,
    dependencies=[Depends(require_ready)],
)
async def approve_approval(
    task_id: str,
    approval_id: str,
    request: ApprovalDecideRequest | None = None,
    task_service: TaskService = Depends(get_task_service),
    task_queue: TaskQueue = Depends(get_task_queue),
    user: ToolContext = Depends(get_current_user),
):
    """
    批准审批请求（可选修改工具参数）。

    批准后入队 approval_resume 消息，Worker 以 Command(resume=决策) 恢复
    被暂停的 Workflow，工具按（可能修改后的）参数执行。
    """
    return await _decide_approval(
        task_id,
        approval_id,
        approve=True,
        note=(request or ApprovalDecideRequest()).note,
        modified_args=(request or ApprovalDecideRequest()).modified_args,
        task_service=task_service,
        task_queue=task_queue,
        user=user,
    )


@router.post(
    "/{task_id}/approvals/{approval_id}/reject",
    response_model=TaskStatusResponse,
    dependencies=[Depends(require_ready)],
)
async def reject_approval(
    task_id: str,
    approval_id: str,
    request: ApprovalDecideRequest | None = None,
    task_service: TaskService = Depends(get_task_service),
    task_queue: TaskQueue = Depends(get_task_queue),
    user: ToolContext = Depends(get_current_user),
):
    """
    拒绝审批请求。

    拒绝后入队 approval_resume 消息，Worker 恢复执行；被拒工具不会执行，
    Agent 将调整方案或如实向用户说明。
    """
    return await _decide_approval(
        task_id,
        approval_id,
        approve=False,
        note=(request or ApprovalDecideRequest()).note,
        modified_args=None,
        task_service=task_service,
        task_queue=task_queue,
        user=user,
    )


async def _decide_approval(
    task_id: str,
    approval_id: str,
    *,
    approve: bool,
    note: str | None,
    modified_args: dict | None,
    task_service: TaskService,
    task_queue: TaskQueue,
    user: ToolContext,
) -> TaskStatusResponse:
    """审批决策公共逻辑：校验 -> 落库 -> 入队恢复消息。"""
    task = await task_service.get_task(task_id)
    if task is None:
        raise TaskNotFoundException(task_id)
    if not can_access_task(task, user):
        raise TaskForbiddenException(task_id)

    pending = await task_service.get_pending_approvals(task_id)
    request = next((a for a in pending if a.id == approval_id), None)
    if request is None:
        raise ApprovalNotFoundException(task_id, approval_id)

    status = (
        ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    )
    if approve and task.status != TaskStatus.AWAITING_APPROVAL:
        raise TaskStateException(
            task_id=task_id,
            current_status=task.status.value,
            allowed_statuses=[TaskStatus.AWAITING_APPROVAL.value],
        )

    decision = {
        "decision": "approved" if approve else "rejected",
        "reason": note,
    }
    if approve and modified_args:
        decision["args"] = modified_args

    resolved = await task_service.resolve_approval(
        task_id,
        approval_id,
        status,
        note=note,
        modified_args=modified_args if approve else None,
    )
    if resolved is None:
        raise ApprovalAlreadyDecidedException(approval_id)

    # 入队恢复消息，Worker 续跑被暂停的 Workflow
    try:
        await task_queue.enqueue(
            TaskMessage(
                task_id=task_id,
                goal=task.goal,
                context=task.context,
                tool_context=user.to_dict(),
                action="approval_resume",
                payload=decision,
            )
        )
    except QueueFullError as e:
        raise QueueUnavailableException(str(e)) from e

    # 任务回到执行中（Worker 消费后继续推进）
    await task_service.update_task_status(task_id, TaskStatus.EXECUTING)
    response = await task_service.get_task_status_response(task_id)
    if response is None:
        raise TaskNotFoundException(task_id)
    return response
