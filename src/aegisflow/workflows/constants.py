"""Shared Temporal orchestration constants."""

from datetime import timedelta

TASK_QUEUE = "aegisflow-task-queue"
ACTIVITY_START_TO_CLOSE_TIMEOUT = timedelta(seconds=60)
HEALTH_CHECK_TIMEOUT = timedelta(seconds=30)
HUMAN_APPROVAL_TIMEOUT = timedelta(minutes=15)
