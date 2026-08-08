"""Health Check - validates all system components at startup."""

import asyncio
from pathlib import Path
from typing import Any

import aiohttp

from core.logging_config import get_logger

logger = get_logger("core", "HealthCheck")


class HealthChecker:
    """Runs quick health checks using already-initialized objects."""

    def __init__(
        self,
        config,
        plugin_registry=None,
        memory_manager=None,
    ):
        self.config = config
        self.plugin_registry = plugin_registry
        self.memory_manager = memory_manager

    async def run_all(self) -> dict[str, Any]:
        """Run all health checks in parallel (< 1 sec)."""
        results = await asyncio.gather(
            self._check_plugins(),
            self._check_llm(),
            self._check_directories(),
            self._check_memory(),
            return_exceptions=True,
        )

        report = {
            "plugins": results[0] if not isinstance(results[0], Exception) else {"status": "FAIL"},
            "llm": results[1] if not isinstance(results[1], Exception) else {"status": "FAIL"},
            "directories": (
                results[2] if not isinstance(results[2], Exception) else {"status": "FAIL"}
            ),
            "memory": results[3] if not isinstance(results[3], Exception) else {"status": "FAIL"},
        }

        all_ok = all(r.get("status") == "OK" for r in report.values())
        report["overall"] = "OK" if all_ok else "DEGRADED"

        return report

    async def _check_plugins(self) -> dict[str, Any]:
        """Check plugins using already-initialized registry."""
        try:
            if self.plugin_registry is None:
                return {"status": "FAIL", "error": "PluginRegistry not provided"}

            count = len(self.plugin_registry)
            if count == 0:
                return {"status": "FAIL", "error": "No plugins loaded"}

            return {
                "status": "OK",
                "count": count,
                "plugins": self.plugin_registry.list_tools(),
            }
        except Exception as e:
            return {"status": "FAIL", "error": str(e)}

    async def _check_llm(self) -> dict[str, Any]:
        """Check LLM with retries and timeout."""
        max_retries = self.config.llm_max_retries
        timeout = aiohttp.ClientTimeout(total=2.0)  # 2 sec per attempt

        for attempt in range(max_retries + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{self.config.llm_url}/api/tags",
                        timeout=timeout,
                    ) as response:
                        if response.status == 200:
                            return {"status": "OK", "url": self.config.llm_url}
                        return {"status": "FAIL", "http_status": response.status}
            except Exception as e:
                logger.warning(
                    "LLM check failed",
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    error=str(e),
                )
                if attempt < max_retries:
                    await asyncio.sleep(0.5)

        # All attempts failed - degraded mode
        logger.error(
            "LLM unavailable after retries - entering DEGRADED MODE",
            url=self.config.llm_url,
            attempts=max_retries + 1,
        )
        return {
            "status": "FAIL",
            "error": "LLM unavailable",
            "mode": "DEGRADED",
        }

    async def _check_directories(self) -> dict[str, Any]:
        """Check that required directories exist."""
        dirs = {
            "logs": self.config.log_dir,
            "data": self.config.data_dir,
        }
        results = {}
        for name, path in dirs.items():
            path = Path(path)
            if not path.exists():
                path.mkdir(parents=True, exist_ok=True)
            results[name] = "OK" if path.is_dir() else "FAIL"

        all_ok = all(v == "OK" for v in results.values())
        return {"status": "OK" if all_ok else "FAIL", "details": results}

    async def _check_memory(self) -> dict[str, Any]:
        """Check memory using already-initialized manager."""
        try:
            if self.memory_manager is None:
                return {"status": "FAIL", "error": "MemoryManager not provided"}

            return {"status": "OK", "entries": len(self.memory_manager)}
        except Exception as e:
            return {"status": "FAIL", "error": str(e)}

    def print_report(self, report: dict[str, Any]) -> None:
        """Print colored health report to console."""
        print("\n" + "=" * 50)
        print("        VASILY AI - HEALTH CHECK")
        print("=" * 50)

        icons = {"OK": "✅", "FAIL": "❌", "DEGRADED": "⚠️"}

        for component, result in report.items():
            if component == "overall":
                continue
            status = result.get("status", "UNKNOWN")
            icon = icons.get(status, "❓")
            extra = ""
            if component == "plugins" and status == "OK":
                extra = f" ({result.get('count', 0)} loaded)"
            elif component == "llm" and status == "FAIL":
                extra = " (DEGRADED MODE)"
            print(f"  {icon} {component.upper():<15} {status}{extra}")

        print("-" * 50)
        overall = report.get("overall", "UNKNOWN")
        overall_icon = icons.get(overall, "❓")
        print(f"  {overall_icon} OVERALL: {overall}")
        print("=" * 50 + "\n")
