"""按 4 个用例驱动后端测试（UTF-8 安全），生成 test_report.json。

Case1 信息提取 / Case2 意图识别 / Case3 多轮上下文(任务型合并) / Case4 缺少关键信息。
"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"
TERMINAL = {"completed", "failed", "cancelled", "awaiting_approval"}
POLL_PER_TASK = 280  # 秒


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
        print(f"  [{label}] status={state.get('status')} ...")
        time.sleep(2)
    return http("GET", f"{BASE}/tasks/{task_id}")


CASES = [
    {
        "name": "Case1_信息提取",
        "goal": (
            "请从下面这段信息中提取姓名、手机号和订单号，并以 JSON 格式返回："
            "张三，手机号 13812345678，订单号 OD20260819001。这个订单需要尽快处理。"
        ),
    },
    {
        "name": "Case2_意图识别",
        "goal": "我想知道明天北京的天气，帮我看看适不适合出去玩。",
    },
    {
        "name": "Case3_多轮上下文",
        "goal": (
            "我想找一家适合聚餐的餐厅。补充多人需求：一共4个人，晚上7点左右，"
            "想吃火锅，离市中心近一点。"
        ),
    },
    {
        "name": "Case4_缺少关键信息",
        "goal": "帮我订明天晚上7点的餐厅，两个人。",
    },
]


def main() -> None:
    report = {"checked_encoding": "goal 与 result 均以 UTF-8 传输；乱码表现为 '?' 占位符"}
    for case in CASES:
        print(f"== {case['name']} ==")
        created = create_task(case["goal"])
        task_id = created.get("task_id")
        print(f"  created task_id={task_id}")

        execute_task(task_id)
        state = poll(task_id, case["name"])
        st = state.get("status")
        plan_goal = state.get("plan", {}) or {}
        plan_goal_txt = plan_goal.get("goal")
        sub = [
            {"id": s.get("id"), "desc": s.get("description"), "status": s.get("status"),
             "tool_used": s.get("tool_used"), "result": s.get("result")}
            for s in (state.get("subtasks") or [])
        ]
        record = {
            "name": case["name"],
            "task_id": task_id,
            "status": st,
            "error": state.get("error"),
            "stored_goal_echo": plan_goal_txt,
            "plan_goal": plan_goal_txt,
            "plan_reasoning": plan_goal.get("reasoning"),
            "subtasks": sub,
            "execution_mode": state.get("execution_mode"),
            "final_result": state.get("final_result"),
            "pending_approval": state.get("pending_approval"),
        }
        report.setdefault("results", []).append(record)
        print(f"  status={st}")

    with open("test_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("report written: test_report.json")


if __name__ == "__main__":
    main()
