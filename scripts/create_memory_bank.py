"""Create (or delete) the Agent Platform instance that backs Memory Bank and Sessions.

The instance holds no agent code: nothing is deployed and nothing serves traffic.
It only provides Memory Bank (long-term learner memory) and Sessions
(persistent conversation history) for the locally running agent.

Usage:
    uv run python scripts/create_memory_bank.py           # create, then write IDs to .env
    uv run python scripts/create_memory_bank.py --delete  # delete, then remove IDs from .env
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re

import vertexai
from dotenv import load_dotenv
from vertexai import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
ID_VAR = "GOOGLE_CLOUD_AGENT_ENGINE_ID"
LOCATION_VAR = "GOOGLE_CLOUD_AGENT_ENGINE_LOCATION"
DEFAULT_LOCATION = "us-central1"
DISPLAY_NAME = "enablement-agent-memory"

# What Memory Bank should extract from enablement conversations.
CUSTOM_TOPICS = {
    "learner_level": (
        "The learner's self-described or demonstrated skill level "
        "(beginner, intermediate or advanced), per technology area where known, "
        "e.g. 'intermediate with Cloud Run, beginner with GKE'."
    ),
    "learning_goals": (
        "What the learner is working towards: certifications, projects, roles "
        "or deadlines, e.g. 'preparing for the Associate Cloud Engineer exam'."
    ),
    "course_progress": (
        "Courses the learner started and which numbered subtopics they completed, "
        "with the course topic, e.g. 'Cloud Run basics: completed subtopics 1, 2 "
        "and 3 of 6'. A subtopic counts as completed once its explainer, lab and "
        "quiz were delivered."
    ),
}


def memory_bank_config() -> types.ReasoningEngineContextSpecMemoryBankConfig:
    topics = [
        types.MemoryBankCustomizationConfigMemoryTopic(
            custom_memory_topic=types.MemoryBankCustomizationConfigMemoryTopicCustomMemoryTopic(
                label=label, description=description
            )
        )
        for label, description in CUSTOM_TOPICS.items()
    ]
    topics.append(
        types.MemoryBankCustomizationConfigMemoryTopic(
            managed_memory_topic=types.MemoryBankCustomizationConfigMemoryTopicManagedMemoryTopic(
                managed_topic_enum=types.ManagedTopicEnum.EXPLICIT_INSTRUCTIONS
            )
        )
    )
    # Generation and embedding models are left unset so Memory Bank uses its
    # supported defaults for the region.
    return types.ReasoningEngineContextSpecMemoryBankConfig(
        customization_configs=[
            types.MemoryBankCustomizationConfig(memory_topics=topics)
        ]
    )


def _set_env_vars(values: dict[str, str | None]) -> None:
    text = ENV_FILE.read_text() if ENV_FILE.exists() else ""
    for key, value in values.items():
        text = re.sub(rf"(?m)^{key}=.*\n?", "", text)
        if value is not None:
            if text and not text.endswith("\n"):
                text += "\n"
            text += f"{key}={value}\n"
    ENV_FILE.write_text(text)


def create(client: vertexai.Client, location: str) -> None:
    engine = client.agent_engines.create(
        config=types.AgentEngineConfig(
            display_name=DISPLAY_NAME,
            description="Memory Bank and Sessions for the local Enablement Agent.",
            context_spec=types.ReasoningEngineContextSpec(
                memory_bank_config=memory_bank_config()
            ),
            labels={"app": "enablement-agent"},
        )
    )
    resource_name = engine.api_resource.name
    engine_id = resource_name.rsplit("/", 1)[-1]
    _set_env_vars({ID_VAR: engine_id, LOCATION_VAR: location})
    print(f"Created {resource_name}")
    print(f"Wrote {ID_VAR}={engine_id} and {LOCATION_VAR}={location} to {ENV_FILE}")


def delete(client: vertexai.Client, project: str, location: str) -> None:
    engine_id = os.environ.get(ID_VAR)
    if not engine_id:
        raise SystemExit(f"{ID_VAR} is not set; nothing to delete.")
    name = f"projects/{project}/locations/{location}/reasoningEngines/{engine_id}"
    # force=True also deletes the sessions and memories stored in the instance.
    client.agent_engines.delete(name=name, force=True)
    _set_env_vars({ID_VAR: None, LOCATION_VAR: None})
    print(f"Deleted {name} and removed its IDs from {ENV_FILE}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--delete", action="store_true", help="Delete the instance.")
    args = parser.parse_args()

    load_dotenv(ENV_FILE)
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    location = os.environ.get(LOCATION_VAR) or DEFAULT_LOCATION
    client = vertexai.Client(project=project, location=location)

    if args.delete:
        delete(client, project, location)
    else:
        if os.environ.get(ID_VAR):
            raise SystemExit(f"{ID_VAR} is already set; run with --delete first.")
        create(client, location)


if __name__ == "__main__":
    main()
