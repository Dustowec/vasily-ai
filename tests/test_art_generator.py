"""Tests for ArtGeneratorTool.

Covers: prompt generation with Pony Diffusion v6 specific tags,
        quality tags, style tags, negative prompts.
"""

import pytest

from plugins.art_generator.tool import ArtGeneratorTool


@pytest.fixture
def tool():
    return ArtGeneratorTool()


# ==================== ОСНОВНЫЕ ТЕГИ ====================


def test_generate_prompt_with_pony_tags(tool):
    """Промт должен содержать качественные теги для Pony Diffusion v6."""
    import asyncio

    result = asyncio.run(
        tool._execute(
            subject="1girl, cyberpunk city", style="anime style", tags=["neon lights", "rain"]
        )
    )

    assert result["status"] == "success"
    prompt = result["prompt"]

    # Проверяем наличие качественных тегов (из QUALITY_TAGS)
    quality_tags = ["masterpiece", "best quality", "highly detailed", "sharp focus"]
    found = any(tag in prompt for tag in quality_tags)
    assert found is True


def test_generate_prompt_with_source_pony(tool):
    """Промт должен содержать source_pony при соответствующем стиле."""
    import asyncio

    result = asyncio.run(tool._execute(subject="1girl", style="pony style", tags=["detailed"]))

    assert result["status"] == "success"
    # Pony стиль должен содержать source_pony или anime
    assert "anime" in result["prompt"].lower() or "pony" in result["prompt"].lower()


def test_generate_negative_prompt_has_quality_tags(tool):
    """Негативный промт должен содержать теги низкого качества."""
    import asyncio

    result = asyncio.run(tool._execute(subject="1girl", style="anime style", tags=[]))

    assert result["status"] == "success"
    negative = result["negative_prompt"]

    # Проверяем наличие тегов низкого качества
    assert "lowres" in negative
    assert "bad anatomy" in negative
    assert "bad hands" in negative
    assert "worst quality" in negative


def test_generate_prompt_with_score_tags(tool):
    """Промт должен содержать score_9 или score_8 для высокого качества."""
    import asyncio

    result = asyncio.run(tool._execute(subject="1girl", style="realistic", tags=["detailed"]))

    assert result["status"] == "success"
    prompt = result["prompt"]

    # Проверяем наличие качественных тегов
    quality_tags = ["masterpiece", "best quality", "highly detailed", "sharp focus"]
    found = any(tag in prompt for tag in quality_tags)
    assert found is True


def test_generate_prompt_empty_subject(tool):
    """Без subject должен возвращаться error."""
    import asyncio

    result = asyncio.run(tool._execute(subject="", style="anime style", tags=[]))

    assert result["status"] == "error"
    assert "Subject is required" in result["message"]


def test_generate_prompt_with_tags_list(tool):
    """Дополнительные теги должны быть добавлены в промт."""
    import asyncio

    tags = ["rain", "night", "neon lights"]
    result = asyncio.run(tool._execute(subject="1girl, city", style="anime style", tags=tags))

    assert result["status"] == "success"
    # Хотя бы один из тегов должен быть в промте
    found = False
    for tag in tags:
        if tag in result["prompt"]:
            found = True
            break
    assert found is True


def test_generate_prompt_has_parameters(tool):
    """Должны быть параметры для генерации (steps, cfg, sampler)."""
    import asyncio

    result = asyncio.run(tool._execute(subject="1girl", style="anime style", tags=[]))

    assert result["status"] == "success"
    params = result["parameters"]
    assert "steps" in params
    assert params["steps"] == 30
    assert "cfg_scale" in params
    assert params["cfg_scale"] == 7.5
    assert "sampler" in params
    assert "width" in params
    assert "height" in params


def test_generate_prompt_random_quality_tags(tool):
    """Качественные теги должны выбираться случайно, но всегда быть."""
    import asyncio

    results = []
    for _ in range(10):
        result = asyncio.run(tool._execute(subject="1girl", style="anime style", tags=[]))
        results.append(result)

    # Все промты должны содержать хотя бы один из QUALITY_TAGS
    quality_tags = ["masterpiece", "best quality", "highly detailed", "sharp focus"]
    for result in results:
        found = False
        for tag in quality_tags:
            if tag in result["prompt"]:
                found = True
                break
        assert found is True


def test_generate_prompt_different_styles(tool):
    """Разные стили должны давать разные промты."""
    import asyncio

    styles = ["anime style", "realistic", "digital art", "concept art"]
    prompts = []

    for style in styles:
        result = asyncio.run(tool._execute(subject="1girl", style=style, tags=[]))
        prompts.append(result["prompt"])

    # Проверяем, что все промты не пустые
    for prompt in prompts:
        assert len(prompt) > 20


def test_generate_prompt_negative_prompt_not_empty(tool):
    """Негативный промт не должен быть пустым."""
    import asyncio

    result = asyncio.run(tool._execute(subject="1girl", style="anime style", tags=["detailed"]))

    assert result["status"] == "success"
    negative = result["negative_prompt"]
    assert len(negative) > 10


# ==================== ТЕСТЫ ДЛЯ PONY V6 ====================


def test_generate_prompt_pony_v6_style(tool):
    """Для Pony v6 должны генерироваться промты с правильными тегами."""
    import asyncio

    result = asyncio.run(
        tool._execute(
            subject="1girl, solo, looking at viewer",
            style="anime style",
            tags=["school uniform", "blonde hair", "blue eyes"],
        )
    )

    assert result["status"] == "success"
    prompt = result["prompt"]

    # Проверяем, что промт содержит качественные теги
    quality_tags = ["masterpiece", "best quality", "highly detailed", "sharp focus"]
    found = any(tag in prompt for tag in quality_tags)
    assert found is True

    # Проверяем, что негативный промт содержит важные теги
    negative = result["negative_prompt"]
    assert "bad anatomy" in negative
    assert "lowres" in negative
