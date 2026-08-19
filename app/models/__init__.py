"""数据模型模块。"""

from app.models import task as _task_module
from app.models.api_schemas import (
    CreateTaskRequest,
    HealthResponse,
    TaskListResponse,
    TaskResponse,
    TaskStatusResponse,
)
from app.models.plan import Plan, ReflectionResult
from app.models.task import (
    ApprovalRequest,
    ApprovalStatus,
    SubTask,
    Task,
    TaskStatus,
)

# 注入 Plan / ReflectionResult 到 task 模块命名空间并重建 Task 模型，
# 解析其对 Plan / ReflectionResult 的前向引用（打破 task.py <-> plan.py 循环导入）。
_task_module.Plan = Plan
_task_module.ReflectionResult = ReflectionResult
Task.model_rebuild()

__all__ = [
    "TaskStatus",
    "SubTask",
    "Task",
    "ApprovalRequest",
    "ApprovalStatus",
    "Plan",
    "ReflectionResult",
    "CreateTaskRequest",
    "TaskResponse",
    "TaskStatusResponse",
    "TaskListResponse",
    "HealthResponse",
]
