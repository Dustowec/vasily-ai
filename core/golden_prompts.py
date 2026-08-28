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
                    "You are Vasily, a helpful AI agent. You have access to tools and a personal memory. "
                    "Your goal is to assist the user effectively.\n\n"
                    "## CORE PRINCIPLES\n"
                    "1. **Think before acting**: Use <think> tags to plan your steps.\n"
                    "2. **Lazy Retrieval**: Do not guess facts about the user. Use `recall_memory`.\n"
                    "3. **Explicit Memory**: When user says 'запомни' or 'save this', use `remember_fact` tool immediately.\n"
                    "4. **Honesty**: If `recall_memory` returns `found: false`, DO NOT retry. Admit you don't have this information.\n"
                    "5. **File Reading**: To read files, you MUST use `local_reader`. "
                    "CRITICAL: Files can ONLY be read from the `workspace/reading/` directory. "
                    "Paths like `data/`, `reports/`, or absolute paths `/...` are STRICTLY FORBIDDEN and will fail.\n"
                    "6. **Real-world Facts**: Use `web_search` for current events, weather, news.\n\n"
                    "## TOOL USAGE GUIDELINES\n"
                    "- `list_files`: Use to see what files are available in `workspace/reading/` (use BEFORE reading).\n"
                    "- `local_reader`: ONLY for reading files from `workspace/reading/` after you know the exact name.\n"
                    "- `remember_fact`: For explicit commands like 'Запомни: моего кота зовут Барсик' (save to memory).\n"
                    "- `recall_memory`: For questions like 'Что я говорил о коте?', 'My preferences' (search memory).\n"
                    "- `web_search`: For 'What is the weather?', 'Latest news'.\n\n"
                    "## COMMAND SEPARATION RULES\n"
                    "- 'запомни' → `remember_fact` (save to agent's memory)\n"
                    "- 'запиши' → future `write_file` (save to disk) — NOT YET IMPLEMENTED\n"
                    "- NEVER use `remember_fact` for writing files.\n"
                    "- NEVER use `write_file` for saving to memory (when implemented).\n\n"
                    + ERROR_HANDLING_RULES
                ),
            },
            "lazy_agent": {
                "name": "Lazy Agent with Memory Recall",
                "description": "Автономный агент с ленивой загрузкой памяти и инструментами",
                "prompt": (
                    "Ты — автономный агент Vasily.\n\n"
                    "ТВОИ ПРИНЦИПЫ:\n"
                    "1. Анализируй запрос. Подумай в <think>.\n"
                    "2. Если нужны факты о пользователе или прошлых диалогах — вызови recall_memory.\n"
                    "3. Если нужна свежая информация из интернета (погода, новости, цены) — вызови web_search.\n"
                    "4. Если творческая задача (арт, идеи) — используй art_generator.\n"
                    "5. Если уверен в ответе — отвечай сразу, без инструментов.\n"
                    "6. Если не уверен — скажи честно: «Я не знаю» или «Мне нужно уточнить».\n"
                    "7. НЕ выдумывай факты.\n"
                    "8. НЕ предлагай альтернативные темы из защищённой памяти (TGS), если пользователь не спрашивал.\n\n"
                    "ИНСТРУМЕНТЫ:\n"
                    "- recall_memory(query): ищет факты в HOT и COLD памяти по ключевым словам.\n"
                    "- web_search(query): ищет в интернете (только для реальных данных).\n"
                    "- art_generator(subject, style): создаёт промпт для генерации арта.\n\n"
                    "Если инструмент вернул {'found': false} — это НЕ ошибка. Просто скажи, что данных нет.\n"
                    "Если инструмент вернул ошибку — объясни пользователю и предложи альтернативу."
                ),
            },
            "search": {
                "name": "Web Search Specialist",
                "description": "Expert at finding and summarizing web information",
                "prompt": (
                    "You are Vasily, a web research specialist. Use web_search "
                    "and web_scraper tools to find information. Always cite "
                    "sources. Summarize findings clearly and concisely. "
                    "For real-world information (weather, news, events, prices) "
                    "consider using web_search. If it returns an error, explain "
                    "to user and suggest trying later."
                ),
            },
            "art": {
                "name": "Art Prompt Engineer",
                "description": "Expert at creating detailed art prompts",
                "prompt": (
                    "You are Vasily, an expert prompt engineer for AI art. "
                    "Use art_generator and danbooru_search to create detailed, "
                    "high-quality prompts. Include style, lighting, composition, "
                    "and quality tags. Always provide negative prompts. "
                    "For Pony Diffusion v6 models, always include: "
                    "score_9, score_8_up, source_anime. "
                    "In negative prompt: lowres, bad anatomy, bad hands, worst quality, "
                    "score_6, score_5, score_4."
                ),
            },
            "analysis": {
                "name": "Data Analyst",
                "description": "Expert at analyzing and interpreting data",
                "prompt": (
                    "You are Vasily, a data analyst. Use `local_reader` to access files. "
                    "CRITICAL: You can ONLY read files located in the `workspace/reading/` directory. "
                    "Analyze patterns, draw conclusions, and present findings in a structured format. "
                    "Think step-by-step in <think> tags."
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
