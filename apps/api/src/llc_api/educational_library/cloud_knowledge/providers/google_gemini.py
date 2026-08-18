from __future__ import annotations

import os
import re
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llc_api.core.config import Settings

from ..models import (
    CloudProviderDescriptor,
    ProviderAvailability,
    ProviderCapabilities,
    ProviderExtractionRequest,
    ProviderRawResponse,
    ProviderUsage,
)
from ..provider import CloudKnowledgeProvider, CloudProviderError

SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+"),
)
QUOTA_MARKERS = (
    "quota",
    "resource_exhausted",
    "resource exhausted",
    "billing",
    "free tier",
    "limit: 0",
)


def _safe_text(value: object, api_key: str | None = None, *, limit: int = 2_000) -> str:
    text = str(value)
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(
            lambda match: (match.group(1) if match.lastindex else "") + "[REDACTED]", text
        )
    return text[:limit]


def normalize_gemini_error(exc: Exception, *, api_key: str | None = None) -> CloudProviderError:
    raw_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    try:
        code = int(raw_code) if raw_code is not None else None
    except (TypeError, ValueError):
        code = None
    message = _safe_text(getattr(exc, "message", None) or exc, api_key)
    lowered = message.lower()
    if code == 429:
        state = (
            ProviderAvailability.QUOTA_EXHAUSTED
            if any(marker in lowered for marker in QUOTA_MARKERS)
            else ProviderAvailability.TEMPORARILY_LIMITED
        )
    elif code in {401, 403}:
        state = ProviderAvailability.AUTHENTICATION_ERROR
    elif code in {400, 404, 405, 409, 422}:
        state = ProviderAvailability.INVALID_REQUEST
    elif code in {408, 502, 503, 504} or isinstance(exc, TimeoutError):
        state = ProviderAvailability.UNAVAILABLE
    elif code is not None and code >= 500:
        state = ProviderAvailability.PROVIDER_ERROR
    else:
        state = ProviderAvailability.UNKNOWN
    details: dict[str, object] = {
        "exception_type": type(exc).__name__,
        "http_status": code,
    }
    provider_status = getattr(exc, "status", None)
    if provider_status:
        details["provider_status"] = _safe_text(provider_status, api_key, limit=500)
    provider_details = _safe_error_value(getattr(exc, "details", None), api_key=api_key)
    if provider_details is not None:
        details["provider_details"] = provider_details
    return CloudProviderError(
        state,
        message or "Gemini request failed.",
        code=str(raw_code) if raw_code is not None else None,
        details=details,
    )


class GoogleGeminiProvider(CloudKnowledgeProvider):
    provider_id = "google_gemini"

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def descriptor(self) -> CloudProviderDescriptor:
        configured = bool(
            self.settings.gemini_api_key and self.settings.gemini_api_key.get_secret_value().strip()
        )
        return CloudProviderDescriptor(
            provider_id=self.provider_id,
            display_name="Google Gemini",
            model=self.settings.cloud_knowledge_gemini_model,
            availability=(
                ProviderAvailability.AVAILABLE
                if configured
                else ProviderAvailability.AUTHENTICATION_ERROR
            ),
            configured=configured,
            capabilities=ProviderCapabilities(
                supports_pdf=True,
                supports_images=True,
                supports_structured_output=True,
                supports_file_upload=True,
                supports_thinking_control=True,
            ),
        )

    def extract(self, request: ProviderExtractionRequest) -> ProviderRawResponse:
        api_key = (
            self.settings.gemini_api_key.get_secret_value().strip()
            if self.settings.gemini_api_key
            else ""
        )
        if not api_key:
            raise CloudProviderError(
                ProviderAvailability.AUTHENTICATION_ERROR,
                "GEMINI_API_KEY is not configured.",
                code="provider_not_configured",
            )
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise CloudProviderError(
                ProviderAvailability.UNAVAILABLE,
                "The optional google-genai package is not installed.",
                code="sdk_not_installed",
            ) from exc

        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(self.settings.cloud_knowledge_timeout_seconds * 1_000)
            ),
        )
        uploaded: Any | None = None
        response: Any | None = None
        cleanup_error: str | None = None
        request_error: Exception | None = None
        with _pdf_for_upload(request.document_path, request.pages) as prepared:
            try:
                uploaded = client.files.upload(
                    file=prepared.path,
                    config=types.UploadFileConfig(mime_type=request.mime_type),
                )
                uploaded = _wait_until_active(
                    client,
                    uploaded,
                    timeout_seconds=min(self.settings.cloud_knowledge_timeout_seconds, 120),
                )
                generation_config: dict[str, object] = {
                    "thinking_level": "minimal",
                    "max_output_tokens": self.settings.cloud_knowledge_max_output_tokens,
                    "thinking_summaries": "none",
                }
                response = client.interactions.create(
                    model=request.model,
                    input=[
                        {
                            "type": "document",
                            "uri": uploaded.uri,
                            "mime_type": getattr(uploaded, "mime_type", None) or request.mime_type,
                        },
                        {"type": "text", "text": request.prompt},
                    ],
                    generation_config=generation_config,
                    response_format={
                        "type": "text",
                        "mime_type": "application/json",
                        "schema": request.response_schema,
                    },
                    store=False,
                )
            except Exception as exc:  # SDK errors are optional dependency types.
                request_error = exc
            finally:
                if uploaded is not None and getattr(uploaded, "name", None):
                    try:
                        client.files.delete(name=uploaded.name)
                    except Exception as exc:  # Valid output survives cleanup diagnostics.
                        cleanup_error = _safe_text(exc, api_key, limit=500)

        if request_error is not None:
            if isinstance(request_error, CloudProviderError):
                raise request_error
            raise normalize_gemini_error(request_error, api_key=api_key) from request_error
        if response is None:
            raise CloudProviderError(
                ProviderAvailability.UNKNOWN,
                "Gemini returned no response object.",
                code="empty_response",
            )
        try:
            text = _interaction_text(response)
        except Exception as exc:
            raise normalize_gemini_error(exc, api_key=api_key) from exc
        if not text:
            raise CloudProviderError(
                ProviderAvailability.PROVIDER_ERROR,
                "Gemini returned no structured response text.",
                code="empty_response_text",
            )
        raw = _model_dump(response)
        usage = _usage(getattr(response, "usage", None))
        metadata: dict[str, object] = {
            "response_id": getattr(response, "id", None),
            "model_version": getattr(response, "model", None),
            "response_status": getattr(response, "status", None),
            "remote_file_deleted": cleanup_error is None,
            "storage_mode": "interactions_store_false",
            "uploaded_pdf_was_subset": prepared.is_subset,
            "uploaded_pdf_page_count": prepared.page_count,
            "local_temporary_pdf_deleted": not prepared.is_subset or prepared.cleanup_error is None,
        }
        if cleanup_error:
            metadata["remote_cleanup_error"] = cleanup_error
        if prepared.cleanup_error:
            metadata["local_cleanup_error"] = prepared.cleanup_error
        return ProviderRawResponse(
            text=text,
            raw=_json_safe(raw),
            usage=usage,
            provider_metadata=_json_safe(metadata),
        )


@dataclass(slots=True)
class PreparedPDF:
    path: Path
    page_count: int
    is_subset: bool
    cleanup_error: str | None = None


@contextmanager
def _pdf_for_upload(document_path: Path, pages: list[int]) -> Iterator[PreparedPDF]:
    """Yield the original PDF or a private, selected-page temporary copy."""
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:
        raise CloudProviderError(
            ProviderAvailability.UNAVAILABLE,
            "The optional pypdf package is required for PDF page selection.",
            code="pdf_dependency_not_installed",
        ) from exc

    try:
        reader = PdfReader(document_path)
        total_pages = len(reader.pages)
    except Exception as exc:
        raise CloudProviderError(
            ProviderAvailability.INVALID_REQUEST,
            "The registered PDF cannot be opened for page selection.",
            code="invalid_pdf",
            details={"exception_type": type(exc).__name__},
        ) from exc

    selected = sorted(set(pages)) if pages else list(range(1, total_pages + 1))
    if any(page < 1 or page > total_pages for page in selected):
        raise CloudProviderError(
            ProviderAvailability.INVALID_REQUEST,
            "A requested PDF page is outside the document range.",
            code="pdf_page_outside_range",
            details={"page_count": total_pages},
        )
    if selected == list(range(1, total_pages + 1)):
        yield PreparedPDF(path=document_path, page_count=total_pages, is_subset=False)
        return

    descriptor, temporary_name = tempfile.mkstemp(prefix="llc-cloud-pages-", suffix=".pdf")
    temporary = Path(temporary_name)
    prepared: PreparedPDF | None = None
    try:
        os.fchmod(descriptor, 0o600)
        writer = PdfWriter()
        for page in selected:
            writer.add_page(reader.pages[page - 1])
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            writer.write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        prepared = PreparedPDF(path=temporary, page_count=len(selected), is_subset=True)
        yield prepared
    except CloudProviderError:
        raise
    except Exception as exc:
        raise CloudProviderError(
            ProviderAvailability.INVALID_REQUEST,
            "The requested PDF page subset could not be prepared.",
            code="pdf_page_selection_failed",
            details={"exception_type": type(exc).__name__},
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError as exc:
            if prepared is not None:
                prepared.cleanup_error = _safe_text(exc, limit=500)


def _model_dump(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return {"text": getattr(value, "text", None)}


def _interaction_text(response: object) -> str | None:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str):
        return output_text
    outputs = getattr(response, "outputs", None) or []
    texts = [getattr(output, "text", None) for output in outputs]
    joined = "".join(text for text in texts if isinstance(text, str))
    return joined or None


def _wait_until_active(client: object, uploaded: object, *, timeout_seconds: float) -> object:
    """Wait only when the Files API reports asynchronous processing."""
    deadline = time.monotonic() + timeout_seconds
    current = uploaded
    while _state_name(getattr(current, "state", None)) == "PROCESSING":
        if time.monotonic() >= deadline:
            raise TimeoutError("Gemini file processing timed out.")
        time.sleep(1)
        current = client.files.get(name=current.name)
    if _state_name(getattr(current, "state", None)) == "FAILED":
        raise RuntimeError("Gemini file processing failed.")
    return current


def _state_name(value: object | None) -> str:
    if value is None:
        return ""
    name = getattr(value, "name", None) or str(value)
    return str(name).rsplit(".", 1)[-1].upper()


def _usage(value: object | None) -> ProviderUsage:
    if value is None:
        return ProviderUsage()
    metadata = _json_safe(_model_dump(value))
    return ProviderUsage(
        input_tokens=_integer(value, "total_input_tokens"),
        output_tokens=_integer(value, "total_output_tokens"),
        thinking_tokens=_integer(value, "total_thought_tokens"),
        total_tokens=_integer(value, "total_tokens"),
        metadata=metadata if isinstance(metadata, dict) else {},
    )


def _integer(value: object, name: str) -> int | None:
    candidate = getattr(value, name, None)
    return int(candidate) if isinstance(candidate, int | float) else None


def _json_safe(value: object) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return _safe_text(value)


def _safe_error_value(
    value: object,
    *,
    api_key: str | None,
    depth: int = 0,
) -> Any:
    """Keep SDK error bodies useful without persisting requests or credentials."""
    if value is None or depth > 5:
        return None
    if isinstance(value, str):
        return _safe_text(value, api_key)
    if isinstance(value, int | float | bool):
        return value
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in list(value.items())[:50]:
            normalized_key = str(key)
            if normalized_key.lower() in {
                "api_key",
                "apikey",
                "authorization",
                "headers",
                "request",
            }:
                continue
            safe[normalized_key] = _safe_error_value(item, api_key=api_key, depth=depth + 1)
        return safe
    if isinstance(value, list | tuple):
        return [_safe_error_value(item, api_key=api_key, depth=depth + 1) for item in value[:50]]
    return _safe_text(value, api_key)
