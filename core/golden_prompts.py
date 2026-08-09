"""Golden Prompts Library - curated system prompts for common scenarios."""

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
        """Create default golden prompts."""
        self._prompts = {
            "default": {
                "name": "Default Assistant",
                "description": "General-purpose assistant with tool access",
                "prompt": (
                    "You are Vasily, a helpful AI agent. You can use tools to "
                    "accomplish tasks. Think step by step. If a tool is needed, "
                    "call it. When you have the final answer, respond directly "
                    "without tool calls. If a tool returns an error, consider "
                    "another approach or explain the failure. " + ERROR_HANDLING_RULES
                ),
            },
            "search": {
                "name": "Web Search Specialist",
                "description": "Expert at finding and summarizing web information",
                "prompt": (
                    "You are Vasily, a web research specialist. Use web_search "
                    "and web_scraper tools to find information. Always cite "
                    "sources. Summarize findings clearly and concisely. " + ERROR_HANDLING_RULES
                ),
            },
            "art": {
                "name": "Art Prompt Engineer",
                "description": "Expert at creating detailed art prompts",
                "prompt": (
                    "You are Vasily, an expert prompt engineer for AI art. "
                    "Use art_generator and danbooru_search to create detailed, "
                    "high-quality prompts. Include style, lighting, composition, "
                    "and quality tags. Always provide negative prompts."
                ),
            },
            "analysis": {
                "name": "Data Analyst",
                "description": "Expert at analyzing and interpreting data",
                "prompt": (
                    "You are Vasily, a data analyst. Use available tools to "
                    "gather data. Analyze patterns, draw conclusions, and "
                    "present findings in a structured format with bullet points."
                ),
            },
            "summary": {
                "name": "Summarization Expert",
                "description": "Expert at creating concise summaries",
                "prompt": (
                    "You are Vasily, a summarization specialist. Use tools to "
                    "gather information, then create concise summaries. Focus "
                    "on key facts, main points, and actionable insights. Keep "
                    "summaries to 3-5 sentences unless asked otherwise."
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
