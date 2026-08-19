"""
Act — 代码仓库工具（github.create_pr 等独立 Operation Tool）。

设计（需求 §7.4）：每个仓库操作是独立 Tool（create_pr），而不是巨型
`github(action, ...)`。通过 GitHubProvider 抽象避免绑定具体 Git 服务。

抽象覆盖的原子操作：
- RepositoryRead / RepositoryWrite / CreateBranch / CreateCommit / CreatePullRequest

由于在本仓库后端未内置 GitHub 集成凭据，默认标记 available=False；
接入时实现 GitHubProvider 并注入。保留 InMemoryGitHubProvider 供流程验证。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config.logging import get_logger
from app.tools.base import BaseTool, ToolInput, ToolOutput
from app.tools.schema import ExecutionMode, ToolCategory
from app.tools.security import CATEGORY_NETWORK, ToolContext

logger = get_logger(__name__)


@dataclass
class PullRequest:
    repo: str
    title: str
    head: str
    base: str
    description: str
    url: str = ""
    created_at: float = field(default_factory=time.time)


class GitHubProvider(Protocol):
    """Git 服务提供商抽象（原子操作）。"""

    async def create_pull_request(
        self,
        repo: str,
        *,
        title: str,
        head: str,
        base: str,
        description: str = "",
    ) -> dict[str, Any]:
        ...


class InMemoryGitHubProvider:
    """内存实现：不真实创建 PR，仅记录。"""

    prs: list[PullRequest] = []

    async def create_pull_request(self, repo, *, title, head, base, description=""):
        self.prs.append(
            PullRequest(repo=repo, title=title, head=head, base=base, description=description)
        )
        return {
            "number": len(self.prs),
            "url": f"memory://{repo}/pull/{len(self.prs)}",
            "created": True,
        }


class GitHubCreatePRTool(BaseTool):
    """
    创建 Pull Request 工具（Act 副作用）。

    入参（parameters）：
    - repo: 必填，仓库标识（owner/name）
    - title: 必填，PR 标题
    - head / base: 分支，默认 main
    - description: 可选，PR 描述

    TODO: 接入真实 GitHub API（需 token/凭据）后注入真实 GitHubProvider。
    """

    category: str = CATEGORY_NETWORK
    runtime_category: ToolCategory = ToolCategory.ACT
    execution_mode: ExecutionMode = ExecutionMode.SYNC
    timeout: float = 15.0
    permissions: frozenset[str] = frozenset({"act:github"})
    metadata: dict[str, Any] = {"side_effect": True, "risk": "high", "idempotent": False}
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "repo": {"type": "string"},
            "title": {"type": "string"},
            "head": {"type": "string", "default": "feature"},
            "base": {"type": "string", "default": "main"},
            "description": {"type": "string"},
        },
        "required": ["repo", "title"],
    }

    def __init__(self, provider: GitHubProvider | None = None):
        self._provider: GitHubProvider | None = provider
        # 未接入真实 GitHub 凭据 -> 默认不可用
        self.available = provider is not None

    @property
    def name(self) -> str:
        return "github.create_pr"

    @property
    def description(self) -> str:
        return (
            "创建 Pull Request（高风险副作用，需审批）。"
            "未配置 GitHub 集成凭据，默认不可用。"
        )

    async def execute(self, input: ToolInput, context: ToolContext | None = None) -> ToolOutput:
        auth_error = self._authorize(context)
        if auth_error:
            return ToolOutput(success=False, error=auth_error)
        if self._provider is None:
            return ToolOutput(success=False, error="github.create_pr 暂不可用（未配置集成）")

        params = input.parameters or {}
        repo = str(params.get("repo") or "").strip()
        title = str(params.get("title") or "").strip()
        if not repo or not title:
            return ToolOutput(success=False, error="缺少必填参数: repo/title")
        try:
            result = await self._provider.create_pull_request(
                repo,
                title=title,
                head=str(params.get("head") or "feature"),
                base=str(params.get("base") or "main"),
                description=str(params.get("description") or ""),
            )
        except Exception as e:
            logger.warning("github.create_pr 失败", error=str(e))
            return ToolOutput(success=False, error="创建 PR 失败")
        return ToolOutput(success=True, data=result)
