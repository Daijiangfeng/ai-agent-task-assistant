"""列出当前后端中的所有任务（UTF-8 安全）。"""
import json
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> None:
    data = get(f"{BASE}/tasks/?limit=100&offset=0")
    total = data.get("total", 0)
    tasks = data.get("tasks", [])
    print(f"TOTAL={total}")
    print(f"FETCHED={len(tasks)}")
    for t in tasks:
        print(json.dumps(
            {
                "id": t.get("id"),
                "status": t.get("status"),
                "goal": t.get("goal"),
                "owner_id": t.get("owner_id"),
                "created_at": t.get("created_at"),
            },
            ensure_ascii=False,
        ))


if __name__ == "__main__":
    main()
