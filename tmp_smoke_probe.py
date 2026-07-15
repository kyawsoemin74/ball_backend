import urllib.request
from app.services.scheduler import live_scheduler

print('scheduler_running_before', live_scheduler.is_running)
try:
    with urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=10) as resp:
        body = resp.read().decode()
        print('health_status', resp.status)
        print(body)
except Exception as exc:
    print('health_error', repr(exc))
