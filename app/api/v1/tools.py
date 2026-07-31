"""
工具相关 API 路由。
暴露已注册到 ToolRegistry 的内置工具清单，供前端能力面板展示。
"""

from fastapi import APIRouter

from app.models.api_schemas import ToolInfo, ToolListResponse
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("", response_model=ToolListResponse)
async def list_tools():
    """列出所有已注册的可用工具（名称 + 描述）。"""
    tools = [
        ToolInfo(name=tool.name, description=tool.description)
        for tool in ToolRegistry.get_all().values()
    ]
    return ToolListResponse(total=len(tools), tools=tools)
