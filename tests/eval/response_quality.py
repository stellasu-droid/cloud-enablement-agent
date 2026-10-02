"""Local LLM-as-judge for `custom_response_quality` (see eval_config.yaml)."""

import threading

from google import genai
from google.genai import types
from pydantic import BaseModel

_local = threading.local()


class _Verdict(BaseModel):
    score: int  # 1-5
    explanation: str


def _client() -> genai.Client:
    """One client per grading thread.

    The eval SDK grades cases on its own thread pool; this initialization runs once
    per thread. Avoids creating a new client for each eval case, which would re-do
    ADC and the TLS handshake every time. Each thread gets its own client, because
    google-auth freezes the SSL context after the first connection when a client
    certificate is present.
    """
    client = getattr(_local, "client", None)
    if client is None:
        # AI Studio (GEMINI_API_KEY) or Agent Platform (ADC).
        client = _local.client = genai.Client()
    return client


def _visible_reply(instance) -> str:
    """All text the learner saw in the last turn.

    Specialist sub-agents post their own messages, and the coach adds only a
    short wrap-up line, so the final event alone is not the whole reply.
    """
    turns = (instance.get("agent_data") or {}).get("turns") or []
    if not turns:
        return str(instance.get("response", ""))
    chunks = []
    for event in turns[-1].get("events", []):
        if event.get("author") == "user":
            continue
        for part in (event.get("content") or {}).get("parts") or []:
            if part.get("text") and not part.get("thought"):
                chunks.append(part["text"])
    return "\n\n".join(chunks) or str(instance.get("response", ""))


def evaluate(instance):
    reference = instance.get("reference")
    rubric = (
        "The agent is an enablement coach for Google Cloud / Google AI topics. "
        "Grade the agent's final response on a 1-5 scale (1 poor, 5 excellent).\n"
        "- If the user named a new topic, the response must be a numbered outline "
        "of 3-7 distinct, well-ordered subtopics, each with a learning objective, "
        "ending with an invitation to pick a subtopic.\n"
        "- If the user picked a subtopic, the response must contain Explainer, "
        "Lab and Quiz sections for that subtopic. The explainer must be accurate. "
        "The lab must use $PROJECT_ID placeholders, have runnable current gcloud "
        "commands, a validation checklist and cleanup steps, and must not grant "
        "roles/owner or public access. The quiz must have correct answers with "
        "explanations.\n"
        "Penalize factual errors about Google Cloud, unsafe lab steps, missing "
        "sections, or content about the wrong subtopic."
    )
    if reference:
        rubric += (
            " The response should agree with the expected answer below; penalize "
            "factual disagreement with it."
        )
    prompt = (
        f"You are an expert Google Cloud instructor reviewing training material. {rubric}\n"
        f"User Prompt: {instance.get('prompt', '')}\n"
        f"Agent reply (everything the learner saw this turn): {_visible_reply(instance)}\n"
    )
    if reference:
        prompt += f"Expected Answer (ground truth): {reference}\n"
    prompt += f"Full Agent Trace: {instance.get('agent_data', '')}\n"

    response = _client().models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,  # deterministic grading
            response_mime_type="application/json",
            response_schema=_Verdict,  # guaranteed schema-valid JSON
        ),
    )
    verdict = response.parsed
    if verdict is None:  # model returned nothing usable
        return {"score": 0, "explanation": response.text or ""}
    return {"score": max(1, min(5, verdict.score)), "explanation": verdict.explanation}
