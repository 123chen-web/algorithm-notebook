"""欧叶OY 容量压测场景（locust）。

典型用户行为，读写比约 8:2：
  读(8): 总览 x3 / 复习队列 x2 / 错题列表 x1 / 论坛列表 x1 / 榜单 x1
  写(2): 复习评分 x1 / 新增错题 x1

认证：用 seed.py 预置的 session cookie（绕开登录接口的 IP 限流；
生产环境用户来自不同 IP，该限流在压测里不具代表性；登录接口单独低并发实测）。

运行：
    LOADTEST_USERS=100 locust -f tools/loadtest/locustfile.py \
        --headless -u 30 -r 10 -t 2m --csv /tmp/loadtest/results/tier_30 \
        --host http://127.0.0.1:8001
"""
import itertools
import os
import random
import threading

from locust import HttpUser, task, between

N_USERS = int(os.environ.get("LOADTEST_USERS", "100"))

_counter = itertools.count()
_counter_lock = threading.Lock()


class OuyeUser(HttpUser):
    wait_time = between(0.5, 2.0)

    def on_start(self):
        with _counter_lock:
            idx = next(_counter) % N_USERS
        self.token = f"loadtest-session-token-{idx:03d}-static"
        # 直接种 session cookie，等价于已登录态
        self.client.cookies.set("session", self.token, domain="127.0.0.1", path="/")
        # 真实前端所有 /api/ 写请求都带此头（防跨站表单），压测同样带上
        self.client.headers.update({"X-CSRF-Protection": "1"})
        self.queue = []

    @task(3)
    def overview(self):
        self.client.get("/api/overview", name="GET /api/overview")

    @task(2)
    def review_queue(self):
        with self.client.get("/api/review/queue", name="GET /api/review/queue",
                             catch_response=True) as r:
            if r.status_code == 200:
                try:
                    self.queue = r.json().get("items", [])
                except Exception:
                    self.queue = []
                    r.failure("bad json")
            else:
                r.failure(f"status {r.status_code}")

    @task(1)
    def review_one(self):
        if not self.queue:
            return
        item = random.choice(self.queue)
        # 409（版本冲突/已到期）是符合预期的业务响应，不计为失败
        with self.client.post(
            f"/api/mistakes/{item['id']}/review",
            json={"quality": random.choice([3, 4, 5]), "version": item.get("version")},
            name="POST /api/mistakes/{id}/review",
            catch_response=True,
        ) as r:
            if r.status_code in (200, 409):
                r.success()
            else:
                r.failure(f"status {r.status_code}: {r.text[:120]}")

    @task(1)
    def list_mistakes(self):
        self.client.get("/api/mistakes", name="GET /api/mistakes")

    @task(1)
    def create_problem(self):
        with self.client.post(
            "/api/problems",
            json={"title": f"压测新增题 {random.randint(1, 999999)}",
                  "zone": "算法", "language": "python", "quick": True},
            name="POST /api/problems",
            catch_response=True,
        ) as r:
            if r.status_code in (201, 429):
                r.success()
            else:
                r.failure(f"status {r.status_code}: {r.text[:120]}")

    @task(1)
    def forum_list(self):
        self.client.get("/api/posts", name="GET /api/posts")

    @task(1)
    def leaderboard(self):
        self.client.get("/api/leaderboard", name="GET /api/leaderboard")
