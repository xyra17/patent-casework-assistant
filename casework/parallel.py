"""Bounded parallel work on already materialized evidence; no shared DB handles."""
import asyncio
import os


async def independent_pair(left,right, *, enabled=True):
    """Factories avoid leaked coroutines when cancelled before the second job."""
    if not enabled or os.getenv('CASEWORK_PARALLEL_REVIEW','2')=='1':
        return [await left(),await right()]
    tasks=[asyncio.create_task(left()),asyncio.create_task(right())]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        raise
