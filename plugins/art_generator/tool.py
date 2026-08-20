"""Art Generator plugin - creates prompts for image generation."""

import random
from typing import Any

from core.base_tool import BaseTool

# Quality tags for prompt enhancement
QUALITY_TAGS = ["masterpiece", "best quality", "highly detailed", "sharp focus"]
STYLE_TAGS = ["anime style", "digital art", "illustration", "concept art"]
LIGHTING_TAGS = ["soft lighting", "dramatic lighting", "natural light", "golden hour"]


class ArtGeneratorTool(BaseTool):
    """Generate art prompts for Stable Diffusion / Midjourney."""

    name = "art_generator"
    description = "Generate detailed prompts for AI art generation"
    version = "1.0.0"

    async def _execute(
        self,
        subject: str = "",
        style: str = "",
        tags: list = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Generate an art prompt from subject and optional parameters."""
        if not subject:
            return {"status": "error", "message": "Subject is required"}

        # Build prompt components
        quality = random.choice(QUALITY_TAGS)
        art_style = style if style else random.choice(STYLE_TAGS)
        lighting = random.choice(LIGHTING_TAGS)

        # Combine tags
        extra_tags = ", ".join(tags) if tags else ""

        # Build final prompt
        prompt_parts = [quality, subject, art_style, lighting]
        if extra_tags:
            prompt_parts.append(extra_tags)

        prompt = ", ".join(prompt_parts)

        # Build negative prompt
        negative_prompt = "lowres, bad anatomy, bad hands, text, error, missing fingers, cropped, worst quality, low quality, blurry"

        return {
            "status": "success",
            "source": "template",
            "subject": subject,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "parameters": {
                "steps": 30,
                "cfg_scale": 7.5,
                "sampler": "DPM++ 2M Karras",
                "width": 512,
                "height": 768,
            },
        }

    def _get_parameters(self) -> dict[str, Any]:
        """Get parameter schema."""
        return {
            "subject": {
                "type": "string",
                "description": "Main subject of the art",
                "required": True,
            },
            "style": {"type": "string", "description": "Art style (optional)", "required": False},
            "tags": {
                "type": "array",
                "description": "Additional tags (optional)",
                "required": False,
            },
        }
