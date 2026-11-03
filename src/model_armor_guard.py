#!/usr/bin/env python3
"""Regional Model Armor Guard (europe-west2 London) + Deterministic URL/Markdown Stripper & Local Fallback.

Enforces Rule 01 (Zero Raw UK PII) and Rule 03 / SAIF (Regional Model Armor across all boundaries):
  - Regional endpoint: https://modelarmor.europe-west2.rep.googleapis.com (<256 KB cap, 800ms timeout)
  - Template: projects/{ARGOLIS_PROJECT_ID}/locations/{MODEL_ARMOR_LOCATION}/templates/{MODEL_ARMOR_TEMPLATE_ID}
  - Wired across ADK callbacks:
      * before_model_callback (user prompt inspection)
      * after_tool_callback   (scans BigQuery rows from App 1 portal_change_requests)
      * after_model_callback  (screens model response + strips untrusted URLs/Markdown images)
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

MAX_ARMOR_PAYLOAD_BYTES = 250_000  # < 256 KB cap for Model Armor API
ARMOR_TIMEOUT_SECONDS = 0.8        # 800ms strict latency cap

UK_PHONE_RE = re.compile(r"(?:\+44\s?7\d{3}|\b07\d{3})\s?\d{3}\s?\d{3}\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
ALLOWED_TOKENIZED_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9._-]+(?:\+(?:stu-\d{4}|sm-\d{3}))?@yenchau\.altostrat\.com$",
    re.I,
)
UK_NI_RE = re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b", re.I)
DOB_RE = re.compile(r"\b(?:0[1-9]|[12]\d|3[01])/(?:0[1-9]|1[0-2])/(?:19|20)\d{2}\b")
PROMPT_INJECTION_RE = re.compile(
    r"(ignore\s+(?:all\s+)?(?:previous|prior)\s+(?:rules|instructions)|"
    r"system\s+override|bypass\s+eligibility|disregard\s+(?:all\s+)?instructions|"
    r"ignore\s+the\s+3-star\s+minimum|you\s+are\s+now\s+in\s+developer\s+mode|"
    r"<script\b|javascript:)",
    re.I,
)

# Deterministic Markdown image exfiltration & untrusted URL stripper
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
EXTERNAL_URL_RE = re.compile(r"https?://[^\s)>\]\"']+", re.I)
ALLOWED_URL_HOSTS = (
    "gov.uk",
    "www.gov.uk",
    "cloud.google.com",
    "docs.cloud.google.com",
    "console.cloud.google.com",
    "saif.google",
    "vertexaisearch.cloud.google.com",
)


def strip_untrusted_urls_and_markdown_images(text: str) -> str:
    """Strips Markdown image tags and non-allowlisted external URLs to prevent data exfiltration."""
    if not text:
        return text
    cleaned = MARKDOWN_IMAGE_RE.sub("[IMAGE_STRIPPED_BY_MODEL_ARMOR]", text)

    def _replace_url(match: re.Match[str]) -> str:
        url = match.group(0)
        lowered = url.lower()
        for host in ALLOWED_URL_HOSTS:
            if f"://{host}" in lowered or f".{host}" in lowered:
                return url
        return "[UNTRUSTED_URL_STRIPPED]"

    return EXTERNAL_URL_RE.sub(_replace_url, cleaned)


@dataclass
class ArmorVerdict:
    allowed: bool
    direction: str  # "USER_PROMPT" | "TOOL_OUTPUT" | "MODEL_RESPONSE"
    source: str
    template_name: str
    categories_triggered: list[str] = field(default_factory=list)
    block_reason: str | None = None
    raw_api_match_state: str | None = None
    sanitized_text: str | None = None
    inspected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelArmorGuard:
    """Day-1 Base State Guardrail wired to regional Model Armor (europe-west2) + deterministic PII/PI/URL rules."""

    def __init__(
        self,
        project_id: str | None = None,
        location: str | None = None,
        template_id: str | None = None,
        api_endpoint: str | None = None,
        mode: str | None = None,
    ) -> None:
        self.project_id = (
            project_id
            or os.getenv("ARGOLIS_PROJECT_ID")
            or os.getenv("GOOGLE_CLOUD_PROJECT", "talent-management-513520")
        )
        self.location = location or os.getenv("MODEL_ARMOR_LOCATION", "europe-west2")
        self.template_id = template_id or os.getenv("MODEL_ARMOR_TEMPLATE_ID", "next-student-transfer-armor")
        self.api_endpoint = (
            api_endpoint
            or os.getenv("MODEL_ARMOR_API_ENDPOINT", f"https://modelarmor.{self.location}.rep.googleapis.com")
        ).rstrip("/")
        self.mode = (mode or os.getenv("ENABLE_MODEL_ARMOR", "1")).strip().lower()
        self.template_resource = (
            f"projects/{self.project_id}/locations/{self.location}/templates/{self.template_id}"
        )
        self._cached_token: str | None = None
        self._token_failed: bool = False

    def _local_deterministic_scan(self, text: str) -> list[str]:
        triggers: list[str] = []
        if not text:
            return triggers
        if PROMPT_INJECTION_RE.search(text):
            triggers.append("PI_AND_JAILBREAK_FILTER")
        if UK_PHONE_RE.search(text):
            triggers.append("SDP_UK_PHONE_NUMBER")
        for match in EMAIL_RE.findall(text):
            if not ALLOWED_TOKENIZED_EMAIL_RE.match(match):
                triggers.append("SDP_EMAIL_ADDRESS")
                break
        if UK_NI_RE.search(text):
            triggers.append("SDP_UK_NATIONAL_INSURANCE_NUMBER")
        if DOB_RE.search(text):
            triggers.append("SDP_DATE_OF_BIRTH")
        if MARKDOWN_IMAGE_RE.search(text):
            triggers.append("MALICIOUS_MARKDOWN_IMAGE_EXFILTRATION")
        return triggers

    def _get_gcp_access_token(self) -> str | None:
        if self._cached_token:
            return self._cached_token
        if self._token_failed:
            return None
        try:
            import google.auth
            import google.auth.transport.requests

            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            req = google.auth.transport.requests.Request()
            creds.refresh(req)
            self._cached_token = creds.token
            return self._cached_token
        except Exception:
            self._token_failed = True
            return None

    def _call_regional_model_armor(self, text: str, method: str) -> tuple[bool, list[str], str | None]:
        """Calls https://modelarmor.europe-west2.rep.googleapis.com/v1/{template}:{method} (<256KB, 800ms)."""
        if self.mode in {"0", "false", "local", "offline"}:
            return False, [], None

        token = self._get_gcp_access_token()
        if not token:
            return False, [], None

        # Enforce < 256 KB payload cap for Model Armor API
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_ARMOR_PAYLOAD_BYTES:
            text = encoded[:MAX_ARMOR_PAYLOAD_BYTES].decode("utf-8", errors="ignore")

        url = f"{self.api_endpoint}/v1/{self.template_resource}:{method}"
        payload_key = "userPromptData" if method == "sanitizeUserPrompt" else "modelResponseData"
        body = json.dumps({payload_key: {"text": text}}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Goog-User-Project": self.project_id,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=ARMOR_TIMEOUT_SECONDS) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError):
            return False, [], None

        result = data.get("sanitizationResult", {})
        match_state = result.get("filterMatchState", "NO_MATCH_FOUND")
        matched_filters: list[str] = []
        if match_state == "MATCH_FOUND":
            filter_results = result.get("filterResults", {})
            for fname, fval in filter_results.items():
                if "MATCH_FOUND" in json.dumps(fval):
                    matched_filters.append(f"MODEL_ARMOR_{fname.upper()}")
            if not matched_filters:
                matched_filters.append("MODEL_ARMOR_POLICY_MATCH")
        return True, matched_filters, match_state

    def inspect_text(self, text: str, direction: str = "USER_PROMPT") -> ArmorVerdict:
        local_triggers = self._local_deterministic_scan(text)
        # Redact allowlisted tokenized demo emails before calling cloud SDP so +aliases don't false-positive
        cloud_text = EMAIL_RE.sub(
            lambda m: "[TOKENIZED_DEMO_ALIAS]" if ALLOWED_TOKENIZED_EMAIL_RE.match(m.group(0)) else m.group(0),
            text or "",
        )
        method = "sanitizeUserPrompt" if direction in {"USER_PROMPT", "TOOL_OUTPUT"} else "sanitizeModelResponse"
        called_api, api_triggers, api_state = self._call_regional_model_armor(cloud_text, method)

        combined = list(dict.fromkeys(api_triggers + local_triggers))
        source = (
            "REGIONAL_MODEL_ARMOR_EUROPE_WEST2+LOCAL_GUARD"
            if called_api
            else "LOCAL_DETERMINISTIC_GUARD"
        )
        sanitized = strip_untrusted_urls_and_markdown_images(text or "")
        if combined:
            reason = (
                f"Blocked by Regional Model Armor ({self.location} / {self.template_id}) & Rule 01/03 Guard: "
                f"detected {', '.join(combined)}. Raw UK PII (07... phones, personal emails, NI numbers) and "
                f"prompt-injection / eligibility-override attempts are strictly prohibited."
            )
            return ArmorVerdict(
                allowed=False,
                direction=direction,
                source=source,
                template_name=self.template_resource,
                categories_triggered=combined,
                block_reason=reason,
                raw_api_match_state=api_state or "LOCAL_MATCH_FOUND",
                sanitized_text=sanitized,
            )
        return ArmorVerdict(
            allowed=True,
            direction=direction,
            source=source,
            template_name=self.template_resource,
            categories_triggered=[],
            block_reason=None,
            raw_api_match_state=api_state or "NO_MATCH_FOUND",
            sanitized_text=sanitized,
        )

    def sanitize_user_prompt(self, prompt: str) -> ArmorVerdict:
        return self.inspect_text(prompt, direction="USER_PROMPT")

    def sanitize_tool_output(self, tool_output: str) -> ArmorVerdict:
        return self.inspect_text(tool_output, direction="TOOL_OUTPUT")

    def sanitize_model_response(self, response_text: str) -> ArmorVerdict:
        return self.inspect_text(response_text, direction="MODEL_RESPONSE")

    # -------------------------------------------------------------------------
    # Google ADK Callback Hooks (before_model_callback, after_tool_callback, after_model_callback)
    # -------------------------------------------------------------------------
    def before_model_callback(self, callback_context: Any, llm_request: Any) -> Any:
        """ADK before_model_callback hook: screens the incoming user prompt before calling Gemini."""
        text_parts: list[str] = []
        contents = getattr(llm_request, "contents", None) or []
        for content in contents:
            for part in getattr(content, "parts", None) or []:
                part_text = getattr(part, "text", None)
                if part_text:
                    text_parts.append(str(part_text))
        verdict = self.sanitize_user_prompt("\n".join(text_parts))
        if not verdict.allowed:
            try:
                from google.adk.models.llm_response import LlmResponse
                from google.genai import types

                return LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=f"[SECURITY INTERCEPT — {verdict.source}] {verdict.block_reason}")],
                    )
                )
            except Exception:
                raise PermissionError(verdict.block_reason)
        return None

    def after_tool_callback(
        self,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
        tool_response: Any,
    ) -> Any:
        """ADK after_tool_callback hook: screens untrusted BigQuery rows from App 1 before returning to Gemini."""
        serialized = json.dumps(tool_response, default=str)
        if len(serialized.encode("utf-8")) > MAX_ARMOR_PAYLOAD_BYTES:
            serialized = serialized.encode("utf-8")[:MAX_ARMOR_PAYLOAD_BYTES].decode("utf-8", errors="ignore")
        verdict = self.sanitize_tool_output(serialized)
        if not verdict.allowed:
            return {
                "status": "BLOCKED_BY_MODEL_ARMOR",
                "reason": verdict.block_reason,
                "categories": verdict.categories_triggered,
            }
        return None

    def after_model_callback(self, callback_context: Any, llm_response: Any) -> Any:
        """ADK after_model_callback hook: screens Gemini output and strips untrusted URLs/Markdown images."""
        text_parts: list[str] = []
        content = getattr(llm_response, "content", None)
        if content:
            for part in getattr(content, "parts", None) or []:
                part_text = getattr(part, "text", None)
                if part_text:
                    cleaned = strip_untrusted_urls_and_markdown_images(str(part_text))
                    try:
                        part.text = cleaned
                    except Exception:
                        pass
                    text_parts.append(cleaned)
        verdict = self.sanitize_model_response("\n".join(text_parts))
        if not verdict.allowed:
            try:
                from google.adk.models.llm_response import LlmResponse
                from google.genai import types

                return LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=f"[SECURITY INTERCEPT — {verdict.source}] {verdict.block_reason}")],
                    )
                )
            except Exception:
                raise PermissionError(verdict.block_reason)
        return None
