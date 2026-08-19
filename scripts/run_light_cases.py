"""轻量 Agent 测试用例驱动脚本（5 个用例）。

覆盖：基础执行 / 上下文 / 工具调用 / 异常处理 / 任务规划。
通过任务 API 创建任务、执行、轮询状态并回写结构化报告。
"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"
TERMINAL = {"completed", "failed", "cancelled", "awaiting_approval"}
POLL_PER_TASK = 120  # 秒


def http(method: str, url: str, payload: dict | None = None, timeout: int = 120) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8")
        return json.loads(body) if body else {}


def create_task(goal: str, context: str | None = None) -> dict:
    return http("POST", f"{BASE}/tasks/", {"goal": goal, "context": context})


def execute_task(task_id: str) -> dict:
    return http("POST", f"{BASE}/tasks/{task_id}/execute", {})


def poll(task_id: str, label: str) -> dict:
    deadline = time.time() + POLL_PER_TASK
    while time.time() < deadline:
        state = http("GET", f"{BASE}/tasks/{task_id}")
        if state.get("status") in TERMINAL:
            return state
        time.sleep(2)
    return http("GET", f"{BASE}/tasks/{task_id}")


CASES = [
    {
        "name": "Case1_简单问答",
        "goal": "什么是 Agent？用 3 句话简单解释一下。",
    },
    {
        "name": "Case2_信息提取",
        "goal": (
            "请从下面内容中提取订单号、客户姓名和金额："
            "“客户李明于8月19日提交订单，订单号为A20260819001，订单金额为299元。”"
        ),
    },
    {
        "name": "Case3_多轮对话",
        "goal": (
            "去台北，周六早上出发，周日晚上回来，预算1000元。"
            "请围绕我之前说的【周末旅行计划】输出一个符合条件的行程，"
            "不要重复询问地点、时间、预算等已经提供的信息。"
        ),
        "context": (
            "用户第一轮说：我想制定一个周末旅行计划。\n"
            "现在是第二轮的补充信息：去台北，周六早上出发，周日晚上回来，预算1000元。"
        ),
    },
    {
        "name": "Case4_缺少信息主动询问",
        "goal": "帮我找一家明天晚上吃饭的餐厅，两个人，预算300元。",
    },
    {
        "name": "Case5_复杂任务拆解",
        "goal": (
            "我下周要去上海出差两天，帮我安排一下行程，包括交通、住宿和每天的工作安排。"
            "预算3000元。请把缺失的关键条件先列出来询问，不要凭空编造交通班次或酒店价格。"
        ),
    },
]


def main() -> None:
    report: dict = {"cases": []}
    for case in CASES:
        print(f"\n===== {case['name']} =====", flush=True)
        created = create_task(case["goal"], case.get("context"))
        task_id = created.get("task_id")
        print(f"  created task_id={task_id}", flush=True)

        execute_task(task_id)
        state = poll(task_id, case["name"])
        st = state.get("status")
        subs = [
            {
                "id": s.get("id"),
                "desc": s.get("description"),
                "status": s.get("status"),
                "tool_used": s.get("tool_used"),
                "result": s.get("result"),
            }
            for s in (state.get("subtasks") or [])
        ]
        record = {
            "name": case["name"],
            "task_id": task_id,
            "status": st,
            "error": state.get("error"),
            "execution_mode": state.get("execution_mode"),
            "final_result": state.get("final_result"),
            "pending_approval": state.get("pending_approval"),
            "subtasks": subs,
        }
        report["cases"].append(record)
        print(f"  status={st}", flush=True)
        print(f"  final_result:\n{record['final_result']}", flush=True)

    with open("light_cases_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\nreport written: light_cases_report.json")


if __name__ == "__main__":
    main()
