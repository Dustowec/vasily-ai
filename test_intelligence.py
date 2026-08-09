"""Intelligence testing on real scenarios (T3-021)."""

import asyncio
from pathlib import Path

import structlog

from core.config import Config
from core.logging_config import setup_logging
from core.plugin_registry import PluginRegistry
from core.react_loop import ReActLoop
from integrations.ollama_client import OllamaClient

SCENARIOS = [
    {
        "name": "Direct answer (no tools)",
        "request": "What is 2+2? Answer with just the number.",
        "expected_tools": [],
        "prompt_type": "default",
    },
    {
        "name": "Web search",
        "request": "Find information about stable diffusion.",
        "expected_tools": ["web_search"],
        "prompt_type": "search",
    },
    {
        "name": "Art generation",
        "request": "Create an art prompt for a samurai standing in the rain at night.",
        "expected_tools": ["art_generator"],
        "prompt_type": "art",
    },
    {
        "name": "Danbooru tags",
        "request": "Search danbooru for tags: 1girl, cyberpunk.",
        "expected_tools": ["danbooru_search"],
        "prompt_type": "default",
    },
    {
        "name": "Echo tool",
        "request": "Use the echo tool with message 'intelligence test' and tell me the result.",
        "expected_tools": ["echo"],
        "prompt_type": "default",
    },
]


async def run_scenario(react_loop, scenario, index):
    """Run one scenario and return a result dict."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=f"scenario-{index:02d}")

    result = {
        "name": scenario["name"],
        "expected_tools": scenario["expected_tools"],
        "passed": False,
        "tools_used": [],
        "tool_match": None,
        "answer_preview": "",
        "error": None,
    }

    try:
        response = await react_loop.run(scenario["request"], prompt_type=scenario["prompt_type"])

        result["tools_used"] = [step["tool"] for step in response.get("steps", [])]
        result["answer_preview"] = response.get("answer", "")[:100]

        # Hard check: success status and non-empty answer
        status_ok = response.get("status") == "success"
        answer_ok = bool(response.get("answer", "").strip())
        result["passed"] = status_ok and answer_ok

        # Soft check: expected tool selection
        expected = scenario["expected_tools"]
        if expected:
            used = set(result["tools_used"])
            result["tool_match"] = any(tool in used for tool in expected)
        else:
            result["tool_match"] = len(result["tools_used"]) == 0

    except Exception as e:
        result["error"] = str(e)

    return result


async def main():
    setup_logging(Path("logs"), level="INFO")

    config = Config.load()
    config.validate()

    registry = PluginRegistry()
    registry.discover_plugins(config.plugins_dir)

    client = OllamaClient(
        base_url=config.llm_url,
        model=config.llm_model,
        timeout=config.llm_timeout,
        num_ctx=config.llm_num_ctx,
    )

    react_loop = ReActLoop(config=config, llm_client=client, plugin_registry=registry)

    print("=" * 60)
    print("      VASILY AI - INTELLIGENCE TEST (T3-021)")
    print("=" * 60)

    results = []
    try:
        for index, scenario in enumerate(SCENARIOS, start=1):
            print(f"\n[{index}/{len(SCENARIOS)}] {scenario['name']}")
            print(f"    Request: {scenario['request'][:70]}")

            result = await run_scenario(react_loop, scenario, index)
            results.append(result)

            print(f"    Result: {'PASS' if result['passed'] else 'FAIL'}")
            print(f"    Tools used: {result['tools_used'] or 'none'}")
            if result["expected_tools"]:
                match_icon = "OK" if result["tool_match"] else "MISMATCH"
                print(f"    Expected {result['expected_tools']}: {match_icon}")
            if result["answer_preview"]:
                print(f"    Answer: {result['answer_preview']}...")
            if result["error"]:
                print(f"    Error: {result['error']}")
    finally:
        await client.close()

    # Summary
    passed = sum(1 for r in results if r["passed"])
    tool_ok = sum(1 for r in results if r["tool_match"])
    print("\n" + "=" * 60)
    print(f"  Scenarios passed: {passed}/{len(results)}")
    print(f"  Correct tool selection: {tool_ok}/{len(results)}")
    print("=" * 60)

    if passed == len(results):
        print("\nIntelligence test complete - ALL PASSED!")
    else:
        print("\nIntelligence test complete - some scenarios failed.")


if __name__ == "__main__":
    asyncio.run(main())
