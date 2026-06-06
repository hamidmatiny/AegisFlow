"""Shared Temporal orchestration constants."""

from datetime import timedelta

TASK_QUEUE = "aegisflow-task-queue"
ACTIVITY_START_TO_CLOSE_TIMEOUT = timedelta(seconds=60)
HUMAN_APPROVAL_TIMEOUT = timedelta(minutes=15)
