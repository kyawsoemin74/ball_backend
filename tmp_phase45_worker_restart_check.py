import json
import subprocess
import sys
import time
import uuid


def run_container_python(code: str):
    result = subprocess.run(
        ["docker", "exec", "fover_worker", "python", "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"container command failed: {result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def stage_retry_row():
    unique_suffix = uuid.uuid4().hex[:8]
    name = f"TMP_RESTART_PERSIST_{unique_suffix}"
    code = f"""
import json, sys, asyncio
from sqlalchemy import text
sys.path.insert(0, '/usr/src/app')
from app.db import async_session
async def main():
    async with async_session() as db:
        result = await db.execute(text('''
            INSERT INTO leagues (provider, provider_id, name, country, country_code, is_featured, display_order, created_at, updated_at)
            VALUES (:provider, NULL, :name, :country, :country_code, false, 999, NOW(), NOW())
            RETURNING league_id
        '''), {{'provider': 'api-football', 'name': '{name}', 'country': 'Runtime Country', 'country_code': 'RT'}})
        league_id = int(result.scalar_one())
        await db.execute(text('''
            INSERT INTO league_identity_recovery (
                league_id, state, attempt_count, next_retry_at, last_attempted_at,
                last_error_code, last_error_message, provider, resolved_provider_id,
                resolved_at, created_at, updated_at
            ) VALUES (
                :league_id, 'IDENTITY_RESOLUTION_RETRY', 1, NOW() - INTERVAL '1 minute', NOW(),
                'NO_CANDIDATE', 'NO_CANDIDATE', 'api-football', NULL, NULL, NOW(), NOW()
            )
            ON CONFLICT (league_id) DO UPDATE
            SET state = EXCLUDED.state,
                attempt_count = 1,
                next_retry_at = NOW() - INTERVAL '1 minute',
                updated_at = NOW()
            RETURNING recovery_id
        '''), {{'league_id': league_id}})
        await db.commit()
        print(json.dumps({{'league_id': league_id, 'name': '{name}'}}))
asyncio.run(main())
"""
    output = run_container_python(code)
    return json.loads(output)


def fetch_retry_state(league_id: int):
    code = f"""
import json, sys
sys.path.insert(0, '/usr/src/app')
from sqlalchemy import text
from app.db import async_session
import asyncio
async def main():
    async with async_session() as db:
        result = await db.execute(text('''
            SELECT recovery_id, league_id, state, attempt_count, next_retry_at, last_error_code, last_error_message,
                   provider, resolved_provider_id, created_at, updated_at
            FROM league_identity_recovery
            WHERE league_id = :league_id
        '''), {{'league_id': {league_id}}})
        row = result.mappings().first()
        print(json.dumps(dict(row) if row else None, default=str))
asyncio.run(main())
"""
    out = run_container_python(code)
    return json.loads(out) if out else None


def resume_recovery(league_id: int, provider_id: str):
    code = f"""
import json, sys
from unittest.mock import patch
sys.path.insert(0, '/usr/src/app')
from app.services.football import football_service
from app.services.scheduler import live_scheduler
import asyncio
async def main():
    svc = football_service.league_service.league_identity_recovery_service
    async def fake(name):
        return {{
            'response': [
                {{
                    'league': {{'id': {provider_id}, 'name': name}},
                    'country': {{'name': 'Runtime Country', 'code': 'RT'}},
                }}
            ]
        }}
    with patch.object(svc.league_provider, 'get_leagues_by_name', side_effect=fake):
        metrics = await live_scheduler._recover_league_identities_job()
    print(json.dumps({{'league_id': {league_id}, 'metrics': metrics}}, default=str))
asyncio.run(main())
"""
    out = run_container_python(code)
    return json.loads(out)


def fetch_league_state(league_id: int):
    code = f"""
import json, sys
sys.path.insert(0, '/usr/src/app')
from sqlalchemy import text
from app.db import async_session
import asyncio
async def main():
    async with async_session() as db:
        result = await db.execute(text('''
            SELECT league_id, provider, provider_id, name, country, country_code
            FROM leagues
            WHERE league_id = :league_id
        '''), {{'league_id': {league_id}}})
        row = result.mappings().first()
        print(json.dumps(dict(row) if row else None, default=str))
asyncio.run(main())
"""
    out = run_container_python(code)
    return json.loads(out) if out else None


def wait_for_worker_ready():
    for _ in range(30):
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", "fover_worker"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip() == "running":
            return
        time.sleep(2)
    raise TimeoutError("fover_worker did not return to running state after restart")


def main():
    staged = stage_retry_row()
    league_id = staged["league_id"]
    before_restart_record = fetch_retry_state(league_id)

    subprocess.run(["docker", "restart", "fover_worker"], check=False)
    wait_for_worker_ready()

    after_restart_record = fetch_retry_state(league_id)
    resumed = resume_recovery(league_id, str(9000000 + (league_id % 100000)))
    after_resume_record = fetch_retry_state(league_id)
    league_state = fetch_league_state(league_id)

    report = {
        "created_league": staged,
        "before_restart_record": before_restart_record,
        "after_restart_record": after_restart_record,
        "resume_metrics": resumed,
        "after_resume_record": after_resume_record,
        "league_state": league_state,
    }
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
