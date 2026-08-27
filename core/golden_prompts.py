"""Golden Prompts Library - curated system prompts for common scenarios.
ADR-011: Removed rigid rules, added planning principles and Rich Tool Descriptions.
"""

import json
from pathlib import Path

from core.logging_config import get_logger

logger = get_logger("core", "GoldenPrompts")

DEFAULT_PROMPTS_FILE = "data/prompts/golden.json"

ERROR_HANDLING_RULES = (
    "ERROR HANDLING RULES: When a tool returns an error with error_type "
    "'backend_unavailable', 'connection_failed' or 'http_error', do not retry "
    "the same call with the same arguments. Either try a different approach "
    "(another tool or different parameters) or provide a final answer "
    "explaining that the service is temporarily unavailable and suggesting "
    "to try later."
)


class GoldenPromptsLibrary:
    """Library of curated system prompts for different task types."""

    def __init__(self, prompts_file: str = DEFAULT_PROMPTS_FILE):
        self.prompts_file = Path(prompts_file)
        self.prompts_file.parent.mkdir(parents=True, exist_ok=True)
        self._prompts: dict[str, dict[str, str]] = {}
        self._load()

    def _load(self) -> None:
        """Load prompts from JSON file."""
        if self.prompts_file.exists():
            try:
                with open(self.prompts_file, encoding="utf-8") as f:
                    self._prompts = json.load(f)
                logger.info("Golden prompts loaded", count=len(self._prompts))
            except (OSError, json.JSONDecodeError) as e:
                logger.error("Failed to load prompts", error=str(e))
                self._prompts = {}
        else:
            logger.info("Prompts file not found, creating default")
            self._create_defaults()
            self._save()

    def _save(self) -> None:
        """Save prompts to file."""
        try:
            with open(self.prompts_file, "w", encoding="utf-8") as f:
                json.dump(self._prompts, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.error("Failed to save prompts", error=str(e))

    def _create_defaults(self) -> None:
        """Create default golden prompts (ADR-011: Principles over rigid rules)."""
        self._prompts = {
            "default": {
                "name": "Default Assistant",
                "description": "General-purpose assistant with tool access",
                "prompt": (
                    "You are Vasily, a helpful AI agent. You have access to tools and a personal memory. "
                    "Your goal is to assist the user effectively.\n\n"
                    "## CORE PRINCIPLES\n"
                    "1. **Think before acting**: Use  tags to plan your steps and reason about the user's intent.\n"
                    "2. **Lazy Retrieval**: Do not guess facts about the user or past conversations. "
                    "If you need personal context, use `recall_memory`.\n"
                    "3. **Real-world Facts**: For current events, weather, news, or prices, use `web_search`.\n"
                    "4. **Honesty**: If you don't know something and tools don't help, admit it. "
                    "Do not hallucinate facts.\n"
                    "5. **TGS Privacy**: TGS (your core identity) is for your reference only. "
                    "Do not use TGS topics to suggest alternative discussions or spam the user.\n\n"
                    "## TOOL USAGE GUIDELINES\n"
                    "- `recall_memory`: Use for questions like 'What did I say about...?', 'My preferences', "
                    "or when you need context from previous dialogues.\n"
                    "- `web_search`: Use for 'What is the weather?', 'Latest news', 'Price of...'.\n"
                    "- `art_generator`: Use for creative image prompts.\n"
                    "- `local_reader`: Use for analyzing files in data/ or reports/.\n\n"
                    + ERROR_HANDLING_RULES
                ),
            },
            "search": {
                "name": "Web Search Specialist",
                "description": "Expert at finding and summarizing web information",
                "prompt": (
                    "You are Vasily, a web research specialist. "
                    "Your primary tool is `web_search` (and optionally `web_scraper` for deep dives).\n\n"
                    "## PRINCIPLES\n"
                    "1. **Identify Informational Needs**: If the user asks about the real world "
                    "(weather, stocks, news, definitions, events), you MUST use `web_search`.\n"
                    "2. **Cite Sources**: Always provide URLs when presenting search results.\n"
                    "3. **Summarize**: Don't just dump raw data. Synthesize the findings into a clear answer.\n"
                    "4. **No Hallucinations**: If the search fails or returns nothing, state that clearly. "
                    "Do not make up facts.\n\n" + ERROR_HANDLING_RULES
                ),
            },
            "art": {
                "name": "Art Prompt Engineer",
                "description": "Expert at creating detailed art prompts",
                "prompt": (
                    "You are Vasily, an expert prompt engineer for AI art. "
                    "Use `art_generator` to create detailed, high-quality prompts. "
                    "Use `danbooru_search` if you need inspiration for tags or styles.\n\n"
                    "## GUIDELINES\n"
                    "- Include style, lighting, composition, and quality tags.\n"
                    "- Always provide negative prompts to avoid artifacts.\n"
                    "- For Pony Diffusion v6 models, ensure quality tags (score_9, etc.) are present.\n"
                    "- Think creatively about the user's request to enhance the visual description."
                ),
            },
            "analysis": {
                "name": "Data Analyst",
                "description": "Expert at analyzing and interpreting data",
                "prompt": (
                    "You are Vasily, a data analyst. Use `local_reader` to access files "
                    "and `web_search` if external context is needed. "
                    "Analyze patterns, draw conclusions, and present findings in a structured format "
                    "with bullet points. Think step-by-step in  tags."
                ),
            },
            "summary": {
                "name": "Summarization Expert",
                "description": "Expert at creating concise summaries",
                "prompt": (
                    "You are Vasily, a summarization specialist. Use tools to gather information, "
                    "then create concise summaries. Focus on key facts, main points, and actionable insights. "
                    "Keep summaries to 3-5 sentences unless asked otherwise. "
                    "Use `recall_memory` if the summary relates to past interactions."
                ),
            },
        }

    def get_prompt(self, name: str) -> str | None:
        """Get a prompt by name."""
        prompt_data = self._prompts.get(name)
        if prompt_data:
            logger.info("Prompt retrieved", name=name)
            return prompt_data.get("prompt")
        logger.warning("Prompt not found", name=name)
        return None

    def list_prompts(self) -> list[dict[str, str]]:
        """List all available prompts with metadata."""
        result = []
        for name, data in self._prompts.items():
            result.append(
                {
                    "name": name,
                    "display_name": data.get("name", name),
                    "description": data.get("description", ""),
                }
            )
        return result

    def add_prompt(self, name: str, display_name: str, description: str, prompt: str) -> None:
        """Add a new prompt to the library."""
        self._prompts[name] = {
            "name": display_name,
            "description": description,
            "prompt": prompt,
        }
        self._save()
        logger.info("Prompt added", name=name)

    def __len__(self) -> int:
        return len(self._prompts)

    def __contains__(self, name: str) -> bool:
        return name in self._prompts
