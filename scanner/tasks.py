import logging

from django.conf import settings

from config.celery import app

from .actions import publish_confs_to_github, run_full_scan

task_logger = logging.getLogger("task")


@app.task(bind=True, ignore_result=False, queue=settings.CELERY_TASK_DEFAULT_QUEUE)
def run_full_scan_task(self):
    """Celery task to trigger run_full_scan."""
    run_full_scan()


@app.task(bind=True, ignore_result=False, queue=settings.CELERY_TASK_DEFAULT_QUEUE)
def publish_confs_to_github_task(self):
    """Celery task to trigger publish_confs_to_github."""
    publish_confs_to_github()
