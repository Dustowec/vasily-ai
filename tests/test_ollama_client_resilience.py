"""OllamaClient resilience tests (Coverage Hardening, file 1 of 3).

Covers: health_check, chat/generate success, retry logic, crash report
generation on total failure, num_ctx injection into options.
"""

import pytest
from aiohttp import web

from integrations.ollama_client import LLMUnavailableError, OllamaClient

DEAD_PORT_URL = "http://127.0.0.1:59999"


@pytest.fixture
async def stub_ollama():
    """Local Ollama stub server.

    state["fail_first"]   - how many first requests return 503
    state["requests"]     - total request counter
    state["last_payload"] - last received JSON payload
    """
    state = {"fail_first": 0, "requests": 0, "last_payload": None, "url": ""}

    async def handler(request):
        state["requests"] += 1
        if request.path == "/api/tags":
            return web.json_response({"models": [{"name": "vasily-qwen"}]})
        try:
            state["last_payload"] = await request.json()
        except Exception:
            state["last_payload"] = None
        if state["requests"] <= state["fail_first"]:
            return web.Response(status=503, text="Service Unavailable")
        return web.json_response({"message": {"content": "stub response"}})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    host, port = runner.addresses[0][:2]
    state["url"] = f"http://{host}:{port}"
    yield state
    await runner.cleanup()


@pytest.fixture
async def client_factory(tmp_path):
    """Create OllamaClient instances and close them after the test."""
    clients = []

    def _make(base_url, **kwargs):
        kwargs.setdefault("log_dir", str(tmp_path))
        kwargs.setdefault("retry_delay_base", 0.01)
        client = OllamaClient(base_url=base_url, **kwargs)
        clients.append(client)
        return client

    yield _make
    for client in clients:
        await client.close()


async def test_health_check_success(stub_ollama, client_factory):
    client = client_factory(stub_ollama["url"])
    assert await client.health_check() is True


async def test_health_check_unreachable(client_factory):
    client = client_factory(DEAD_PORT_URL)
    assert await client.health_check() is False


def test_build_options_defaults_and_override(tmp_path):
    client = OllamaClient(log_dir=str(tmp_path))
    options = client._build_options()
    assert options["temperature"] == 0.1
    assert options["num_ctx"] == 32768

    overridden = client._build_options(num_ctx=1024, temperature=0.5)
    assert overridden["num_ctx"] == 1024
    assert overridden["temperature"] == 0.5


async def test_chat_success_sends_num_ctx(stub_ollama, client_factory):
    client = client_factory(stub_ollama["url"], num_ctx=4096)
    response = await client.chat(messages=[{"role": "user", "content": "hi"}])

    assert response["message"]["content"] == "stub response"
    payload = stub_ollama["last_payload"]
    assert payload["model"] == "vasily-qwen"
    assert payload["options"]["num_ctx"] == 4096
    assert payload["options"]["temperature"] == 0.1


async def test_generate_success(stub_ollama, client_factory):
    client = client_factory(stub_ollama["url"])
    response = await client.generate("say hello")

    assert response["message"]["content"] == "stub response"
    assert stub_ollama["last_payload"]["prompt"] == "say hello"


async def test_retry_then_success(stub_ollama, client_factory):
    """First attempt fails with 503, second succeeds."""
    stub_ollama["fail_first"] = 1
    client = client_factory(stub_ollama["url"], max_retries=2)

    response = await client.chat(messages=[{"role": "user", "content": "hi"}])

    assert response["message"]["content"] == "stub response"
    assert stub_ollama["requests"] == 2


async def test_all_retries_exhausted_raises_and_creates_crash_report(
    stub_ollama, client_factory, tmp_path
):
    """Total failure: LLMUnavailableError raised and crash report written."""
    stub_ollama["fail_first"] = 999
    client = client_factory(stub_ollama["url"], max_retries=1)

    with pytest.raises(LLMUnavailableError):
        await client.chat(messages=[{"role": "user", "content": "hi"}])

    # initial attempt + 1 retry
    assert stub_ollama["requests"] == 2

    reports = list((tmp_path / "crash_reports").glob("crash_*"))
    assert len(reports) >= 1
