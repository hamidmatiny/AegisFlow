"""OpenTelemetry configuration and instrumentation helpers for AegisFlow."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SpanExporter
from opentelemetry.trace import Status, StatusCode, Tracer
from temporalio.contrib.opentelemetry import OpenTelemetryInterceptor, create_tracer_provider
from temporalio.contrib.opentelemetry._tracer_provider import ReplaySafeTracerProvider

if TYPE_CHECKING:
    from pydantic_ai.usage import RunUsage

logger = logging.getLogger(__name__)

DEFAULT_SERVICE_NAME = "aegisflow"
OTLP_ENDPOINT_ENV = "AEGISFLOW_OTLP_ENDPOINT"
SERVICE_NAME_ENV = "AEGISFLOW_SERVICE_NAME"

_tracer_provider: ReplaySafeTracerProvider | None = None


def reset_telemetry_for_tests() -> None:
    """Reset global telemetry state for isolated unit tests."""
    global _tracer_provider
    if _tracer_provider is not None:
        _tracer_provider.shutdown()
    _tracer_provider = None


def _build_span_exporter(*, otlp_endpoint: str | None) -> SpanExporter:
    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
        except ImportError:
            logger.warning(
                "OTLP endpoint configured but opentelemetry-exporter-otlp-proto-http "
                "is not installed; falling back to console exporter.",
            )
            return ConsoleSpanExporter()
        return OTLPSpanExporter(endpoint=otlp_endpoint)
    return ConsoleSpanExporter()


def configure_telemetry(
    *,
    service_name: str | None = None,
    otlp_endpoint: str | None = None,
) -> ReplaySafeTracerProvider:
    """Initialize replay-safe OpenTelemetry tracing for Temporal and activities."""
    global _tracer_provider

    resolved_service_name = service_name or os.getenv(SERVICE_NAME_ENV) or DEFAULT_SERVICE_NAME
    resolved_otlp_endpoint = otlp_endpoint or os.getenv(OTLP_ENDPOINT_ENV)

    if _tracer_provider is not None:
        return _tracer_provider

    resource = Resource.create({"service.name": resolved_service_name})
    replay_safe_provider = create_tracer_provider(resource=resource)
    exporter = _build_span_exporter(otlp_endpoint=resolved_otlp_endpoint)
    replay_safe_provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(replay_safe_provider)
    _tracer_provider = replay_safe_provider

    logger.info(
        "OpenTelemetry configured for service %r using %s exporter.",
        resolved_service_name,
        "OTLP" if resolved_otlp_endpoint else "console",
    )
    return replay_safe_provider


def get_tracer_provider() -> ReplaySafeTracerProvider:
    """Return the configured tracer provider, initializing defaults if needed."""
    return configure_telemetry()


def get_tracer(instrumentation_name: str = "aegisflow") -> Tracer:
    """Return a tracer bound to the configured provider."""
    return get_tracer_provider().get_tracer(instrumentation_name)


def build_temporal_otel_interceptor() -> OpenTelemetryInterceptor:
    """Create a Temporal interceptor wired to the configured tracer provider."""
    configure_telemetry()
    return OpenTelemetryInterceptor(add_temporal_spans=True)


@contextmanager
def activity_span(
    activity_name: str,
    *,
    attributes: Mapping[str, str | int | float | bool] | None = None,
) -> Iterator[trace.Span]:
    """Create a span for Temporal activity execution."""
    tracer = get_tracer("aegisflow.activities")
    span_attributes: dict[str, str | int | float | bool] = {
        "aegisflow.activity.name": activity_name,
    }
    if attributes:
        span_attributes.update(dict(attributes))

    with tracer.start_as_current_span(
        f"activity.{activity_name}",
        attributes=span_attributes,
    ) as span:
        started_at = time.perf_counter()
        try:
            yield span
        except Exception as error:
            span.record_exception(error)
            span.set_status(Status(StatusCode.ERROR, str(error)))
            raise
        finally:
            latency_ms = (time.perf_counter() - started_at) * 1000
            span.set_attribute("aegisflow.latency_ms", latency_ms)


@asynccontextmanager
async def traced_pydantic_ai_run(
    operation_name: str,
    *,
    agent_name: str,
    model_name: str | None = None,
    attributes: Mapping[str, str | int | float | bool] | None = None,
) -> AsyncIterator[trace.Span]:
    """Context manager that records PydanticAI run latency and token usage."""
    tracer = get_tracer("aegisflow.pydantic_ai")
    span_attributes: dict[str, str | int | float | bool] = {
        "gen_ai.operation.name": operation_name,
        "gen_ai.agent.name": agent_name,
    }
    if model_name is not None:
        span_attributes["gen_ai.request.model"] = model_name
    if attributes:
        span_attributes.update(attributes)

    with tracer.start_as_current_span(
        f"pydantic_ai.{operation_name}",
        attributes=span_attributes,
    ) as span:
        started_at = time.perf_counter()
        try:
            yield span
        except Exception as error:
            span.record_exception(error)
            span.set_status(Status(StatusCode.ERROR, str(error)))
            raise
        finally:
            span.set_attribute(
                "gen_ai.response.latency_ms",
                (time.perf_counter() - started_at) * 1000,
            )


def record_run_usage(span: trace.Span, usage: RunUsage) -> None:
    """Attach PydanticAI token usage metadata to the active span."""
    span.set_attributes(usage.opentelemetry_attributes())


async def run_traced_agent[T](
    operation_name: str,
    *,
    agent_name: str,
    model_name: str | None,
    runner: Callable[[], Awaitable[T]],
    usage_provider: Callable[[T], RunUsage],
) -> T:
    """Execute a PydanticAI coroutine and capture usage/latency on the span."""
    async with traced_pydantic_ai_run(
        operation_name,
        agent_name=agent_name,
        model_name=model_name,
    ) as span:
        result = await runner()
        record_run_usage(span, usage_provider(result))
        return result
