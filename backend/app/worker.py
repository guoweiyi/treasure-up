from celery import Celery

from app.config import settings
from app.jobs import run_job_id

celery = Celery("treasure", broker=settings.redis_url)
celery.conf.update(task_acks_late=True, task_reject_on_worker_lost=True, worker_prefetch_multiplier=1,
                   broker_transport_options={"visibility_timeout": 25200}, task_time_limit=21600,
                   task_serializer="json", accept_content=["json"], result_serializer="json",
                   task_ignore_result=True, timezone="UTC")


@celery.task(name="treasure.run_job")
def execute(job_id):
    return run_job_id(job_id)
