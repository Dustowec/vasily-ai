"""Intelligence testing on real scenarios (T3-021).

Production policy: dev_mode=False. Plugins return typed errors when backends
are unavailable; the model must handle them gracefully (no infinite retries).
"""

import asyncio
import json
from collections import Counter
from pathlib import Path

import structlog

from core.config import Config
from core.logging_config import setup_logging
from core.plugin_registry import PluginRegistry
from core.react_loop import ReActLoop
from integrations.ollama_client import OllamaClient

UNAVAILABLE_TOKENS = [
    "недоступн",
    "ошибк",
    "попробу",
    "проблем",
    "не могу",
    "не работа",
    "unavailable",
    "cannot",
    "try again",
    "later",
    "fail",
]

BLOCKED_MARKERS = ("[DUPLICATE CALL]", "[LIMIT REACHED]")

SCENARIOS = [
    {
        "name": "Direct answer (no tools)",
        "request": "What is 2+2? Answer with just the number.",
        "expected_tools": [],
        "prompt_type": "default",
    },
    {
        "name": "Web search (backend down)",
        "request": "Find information about stable diffusion.",
        "expected_tools": ["web_search"],
        "prompt_type": "search",
        "max_iterations": 4,
        "max_identical_calls": 2,
        "expect_unavailable": True,
    },
    {
        "name": "Art generation",
        "request": "Create an art prompt for a samurai standing in the rain at night.",
        "expected_tools": ["art_generator"],
        "prompt_type": "art",
    },
    {
        "name": "Danbooru tags (backend down)",
        "request": "Search danbooru for tags: 1girl, cyberpunk.",
        "expected_tools": ["danbooru_search"],
        "prompt_type": "default",
        "expect_unavailable": True,
    },
    {
        "name": "Echo tool",
        "request": "Use the echo tool with message 'intelligence test' and tell me the result.",
        "expected_tools": ["echo"],
        "prompt_type": "default",
    },
]


def count_real_identical_calls(steps) -> int:
    """Count real plugin executions per identical signature.

    Blocked duplicates ([DUPLICATE CALL] / [LIMIT REACHED]) are not counted
    as executions.
    """
    signatures = Counter()
    for step in steps:
        preview = step.get("result_preview", "")
        if preview.startswith(BLOCKED_MARKERS):
            continue
        signature = (
            f"{step['tool']}:" f"{json.dumps(step['args'], sort_keys=True, ensure_ascii=False)}"
        )
        signatures[signature] += 1
    return max(signatures.values()) if signatures else 0


async def run_scenario(react_loop, scenario, index):
    """Run one scenario and evaluate structural + soft criteria."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=f"scenario-{index:02d}")

    result = {
        "name": scenario["name"],
        "expected_tools": scenario["expected_tools"],
        "passed": False,
        "tools_used": [],
        "tool_match": None,
        "answer_preview": "",
        "iterations": 0,
        "notes": [],
        "error": None,
    }

    try:
        response = await react_loop.run(scenario["request"], prompt_type=scenario["prompt_type"])

        steps = response.get("steps", [])
        result["tools_used"] = [step["tool"] for step in steps]
        result["answer_preview"] = response.get("answer", "")[:200]
        result["iterations"] = response.get("iterations", 0)

        status_ok = response.get("status") == "success"
        answer_ok = bool(response.get("answer", "").strip())
        result["passed"] = status_ok and answer_ok

        expected = scenario["expected_tools"]
        if expected:
            used = set(result["tools_used"])
            result["tool_match"] = any(tool in used for tool in expected)
        else:
            result["tool_match"] = len(result["tools_used"]) == 0

        # Structural: iteration budget (no wasted retry loops)
        if "max_iterations" in scenario:
            limit = scenario["max_iterations"]
            ok = result["iterations"] <= limit
            result["notes"].append(
                f"iterations<={limit}: {result['iterations']} " f"{'OK' if ok else 'FAIL'}"
            )
            result["passed"] = result["passed"] and ok

        # Structural: identical calls executed at most N times
        if "max_identical_calls" in scenario:
            limit = scenario["max_identical_calls"]
            worst = count_real_identical_calls(steps)
            ok = worst <= limit
            result["notes"].append(
                f"real identical calls<={limit}: {worst} {'OK' if ok else 'FAIL'}"
            )
            result["passed"] = result["passed"] and ok

        # Soft: unavailable phrases in the answer (informational)
        if scenario.get("expect_unavailable"):
            lowered = response.get("answer", "").lower()
            matched = [token for token in UNAVAILABLE_TOKENS if token in lowered]
            result["notes"].append(f"unavailable phrases (soft): {matched[:4] or 'none'}")

    except Exception as e:
        result["error"] = str(e)

    return result


async def main():
    setup_logging(Path("logs"), level="INFO")

    config = Config.load()
    config.validate()

    # Production policy: mock data blocked outside dev_mode
    assert config.dev_mode is False

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
    print("   VASILY AI - INTELLIGENCE TEST (production policy)")
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
            print(f"    Iterations: {result['iterations']}")
            if result["expected_tools"]:
                match_icon = "OK" if result["tool_match"] else "MISMATCH"
                print(f"    Expected {result['expected_tools']}: {match_icon}")
            for note in result["notes"]:
                print(f"    {note}")
            if result["answer_preview"]:
                print(f"    Answer: {result['answer_preview']}...")
            if result["error"]:
                print(f"    Error: {result['error']}")
    finally:
        await client.close()

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
