from synal.runtime_v3 import app
from synal.thread_activity_http import router as thread_activity_router

app.include_router(thread_activity_router)
