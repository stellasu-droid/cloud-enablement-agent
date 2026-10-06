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

"""Shared sample payloads for unit tests."""

OUTLINE = {
    "topic": "Cloud Run basics",
    "subtopics": [
        {
            "number": 1,
            "title": "Containers",
            "objective": "Explain containers.",
            "complexity": 1,
        },
        {
            "number": 2,
            "title": "Deploying",
            "objective": "Deploy a service.",
            "complexity": 2,
        },
        {
            "number": 3,
            "title": "Scaling",
            "objective": "Tune autoscaling.",
            "complexity": 4,
        },
    ],
}

EXPLAINER = {
    "what_it_is": "Cloud Run runs containers.",
    "how_it_works": ["You push an image.", "Cloud Run scales it."],
    "mermaid": "flowchart LR\n  A --> B",
    "analogy": "Like a taxi.",
    "key_takeaways": ["Serverless", "Scales to zero", "Pay per use"],
    "learn_more": [
        {"title": "Cloud Run docs", "url": "https://cloud.google.com/run/docs"}
    ],
}

ARGS = {
    "topic": "Cloud Run basics",
    "subtopic_number": 2,
    "subtopic_title": "Deploying",
    "learning_objective": "Deploy a service.",
    "learner_level": "beginner",
    "complexity": 2,
}


def lab(cost=0.1, commands=None, cleanup=None):
    return {
        "goal": "Deploy hello world.",
        "estimated_cost_usd": cost,
        "cost_drivers": ["Cloud Run requests"],
        "prerequisites": ["A project with billing"],
        "apis_to_enable": ["run.googleapis.com"],
        "setup_commands": [
            'export PROJECT_ID="your-project-id"',
            'export REGION="us-central1"',
        ],
        "steps": [
            {
                "purpose": "Deploy",
                "commands": commands
                or [
                    "gcloud run deploy hello --image=us-docker.pkg.dev/cloudrun/container/hello "
                    "--region=$REGION --project=$PROJECT_ID"
                ],
                "expected_result": "A URL is printed.",
            }
        ],
        "validation": ["curl the URL"],
        "cleanup_commands": cleanup
        if cleanup is not None
        else [
            "gcloud run services delete hello --region=$REGION --project=$PROJECT_ID --quiet"
        ],
    }


QUIZ = {
    "questions": [
        {
            "question": f"Q{i}?",
            "options": ["a", "b", "c", "d"],
            "answer": "B",
            "explanation": "Because.",
        }
        for i in range(4)
    ]
}
