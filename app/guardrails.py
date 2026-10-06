# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Runtime guardrails: PII redaction, prompt-injection signals, lab linting.

Pure functions with no ADK dependency, so they are cheap to unit test and are
reused by both the GuardrailPlugin and the JSON log formatter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas import LabGuide

# ---------------------------------------------------------------------------
# PII / secret redaction
# ---------------------------------------------------------------------------

_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S
)
_SA_KEY_FIELD = re.compile(r'"private_key(?:_id)?"\s*:\s*"[^"]*"')
_GOOGLE_API_KEY = re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_CARD = re.compile(r"\b(?:\d[ \-]?){13,19}\b")
_PHONE = re.compile(
    r"(?<![\w.])(?:"
    r"(?:\+\d{1,3}[ .\-]?)?\(?\d{3}\)?[ .\-]\d{3}[ .\-]\d{4}"  # NANP: (650) 253-0000
    r"|\+\d{1,3}(?:[ .\-]?\d{2,4}){2,4}"  # international, must start with +
    r")(?![ .\-]?\d)"
)
_IPV4 = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
)
# Addresses that are not personal and appear in normal lab material.
_SAFE_IPS = {"0.0.0.0", "127.0.0.1", "255.255.255.255"}
_SAFE_EMAIL_DOMAINS = (
    "example.com",
    "gserviceaccount.com",
    "developer.gserviceaccount.com",
)


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Returns (redacted_text, kinds_found). Idempotent."""
    found: list[str] = []

    def sub(pattern: re.Pattern[str], kind: str, keep=lambda m: False) -> None:
        nonlocal text

        def repl(m: re.Match[str]) -> str:
            if keep(m):
                return m.group(0)
            found.append(kind)
            return f"[REDACTED:{kind}]"

        text = pattern.sub(repl, text)

    sub(_PRIVATE_KEY, "private_key")
    sub(_SA_KEY_FIELD, "service_account_key")
    sub(_GOOGLE_API_KEY, "api_key")
    sub(
        _EMAIL,
        "email",
        keep=lambda m: m.group(0).lower().endswith(_SAFE_EMAIL_DOMAINS),
    )
    sub(
        _CARD,
        "credit_card",
        keep=lambda m: not _luhn_ok(re.sub(r"\D", "", m.group(0))),
    )
    sub(_PHONE, "phone")
    sub(_IPV4, "ip_address", keep=lambda m: m.group(0) in _SAFE_IPS)
    return text, found


# ---------------------------------------------------------------------------
# Prompt-injection signals
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS = {
    "override_instructions": r"\b(ignore|disregard|forget)\b.{0,30}\b(previous|prior|above|all|your)\b.{0,20}\b(instructions|rules|prompt)",
    "reveal_prompt": r"\b(reveal|show|print|repeat|leak)\b.{0,30}\b(system prompt|instructions|hidden prompt)",
    "role_override": r"\byou are now\b|\bact as (?:an? )?(?:unrestricted|jailbroken|dan)\b|\bdeveloper mode\b",
    "tool_spoofing": r"\b(function_call|function_response|transfer_to_agent)\b",
}
_INJECTION_RES = {k: re.compile(v, re.I | re.S) for k, v in _INJECTION_PATTERNS.items()}


def injection_signals(text: str) -> list[str]:
    """Names of prompt-injection patterns present in the text."""
    return [name for name, rx in _INJECTION_RES.items() if rx.search(text)]


# ---------------------------------------------------------------------------
# Lab linter
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    rule: str
    detail: str


_DENY_RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "broad_role",
        re.compile(r"roles/(owner|editor)\b"),
        "Grants roles/owner or roles/editor; use a narrowly scoped predefined role.",
    ),
    (
        "public_principal",
        re.compile(r"\ball(Authenticated)?Users\b"),
        "Grants access to allUsers / allAuthenticatedUsers.",
    ),
    (
        "delete_container",
        re.compile(
            r"gcloud\s+(alpha\s+|beta\s+)?(projects|organizations|resource-manager\s+folders)\s+delete"
        ),
        "Deletes a project, folder or organization.",
    ),
    (
        "rm_rf",
        re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f|\brm\s+-[a-zA-Z]*f[a-zA-Z]*r"),
        "Uses rm -rf.",
    ),
    (
        "pipe_to_shell",
        re.compile(r"(curl|wget)[^|\n]*\|\s*(sudo\s+)?(ba|z)?sh\b"),
        "Pipes a download straight into a shell.",
    ),
    (
        "inline_secret",
        re.compile(
            r"AIza[0-9A-Za-z_\-]{35}|-----BEGIN [A-Z ]*PRIVATE KEY|(--password|password=)\s*\S{4,}",
            re.I,
        ),
        "Puts a key or password inline; use ADC or Secret Manager.",
    ),
    (
        "open_admin_port",
        re.compile(
            r"(0\.0\.0\.0/0[^\n]*(tcp:)?(22|3389)\b)|((tcp:)?\b(22|3389)\b[^\n]*0\.0\.0\.0/0)"
        ),
        "Opens SSH/RDP to the whole internet.",
    ),
    (
        "hidden_output",
        re.compile(
            r"delete[^\n]*--no-user-output-enabled|--no-user-output-enabled[^\n]*delete"
        ),
        "Hides output of a delete command.",
    ),
]
_LITERAL_PROJECT = re.compile(r"--project[= ](?!\"?\$)[a-z][a-z0-9\-]{5,29}\b")


def _all_commands(lab: LabGuide) -> list[str]:
    cmds = list(lab.setup_commands) + list(lab.cleanup_commands)
    for step in lab.steps:
        cmds += step.commands
    return cmds


def lint_lab(lab: LabGuide) -> list[Violation]:
    """Checks a lab for unsafe commands and missing required parts."""
    commands = _all_commands(lab)
    text = "\n".join(commands)
    violations = [
        Violation(rule, detail) for rule, rx, detail in _DENY_RULES if rx.search(text)
    ]
    if not lab.cleanup_commands:
        violations.append(Violation("missing_cleanup", "No clean-up commands."))
    if "PROJECT_ID" not in text:
        violations.append(
            Violation("missing_project_var", "Commands must use $PROJECT_ID.")
        )
    if _LITERAL_PROJECT.search(text):
        violations.append(
            Violation(
                "literal_project", "Uses a literal project ID instead of $PROJECT_ID."
            )
        )
    return violations
