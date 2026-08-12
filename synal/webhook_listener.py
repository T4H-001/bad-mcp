from synal import runtime_v4 as runtime
from synal.coding_agent_dispatch import execute_kimi, is_kimi_work
from synal.thread_activity_http import router as thread_activity_router

_default_execute = runtime.execute


def execute(work_key: str, url: str, body: str, worker: str) -> None:
    if is_kimi_work(work_key):
        execute_kimi(work_key, url, body, worker, runtime.v3)
        return
    _default_execute(work_key, url, body, worker)


# The FastAPI webhook endpoint is defined in runtime_v3 and resolves its execute
# function dynamically. Patch that single execution boundary so the known Kimi
# qualification work key cannot fall through to generic prose/Qwen execution.
runtime.v3.execute = execute
app = runtime.app
app.include_router(thread_activity_router)
