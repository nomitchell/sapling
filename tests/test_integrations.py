import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from sapling.integrations.execution import (
    LocalDockerBackend,
    LocalProcessBackend,
    WorkspaceViolation,
    contained_path,
    sanitized_environment,
)
from sapling.integrations.model import (
    MissingCredential,
    ModelResponseError,
    OpenAIModelRuntime,
    partial_json_string_field,
)
from sapling.integrations.permissions import PermissionPolicy
from sapling.integrations.search import SearchClient, SearchUnavailable, SourceRejected, validate_public_url


@pytest.mark.asyncio
async def test_tavily_is_preferred_and_credentials_stay_on_provider():
    requests = []
    def handler(request):
        requests.append(request)
        assert request.url == "https://api.tavily.com/search"
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer test-tavily-key"
        return httpx.Response(200, json={"results": [{"title": "Primary study", "url": "https://example.org/paper", "content": "Experiments"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with SearchClient("http://unused.local", tavily_api_key="test-tavily-key", client=client) as search:
            results = await search.search_web("robustness")
    assert len(requests) == 1
    assert results[0].provider == "tavily" and results[0].summary == "Experiments"


def test_permission_modes_and_scoped_grants():
    assert PermissionPolicy("balanced").evaluate("execute_container").allowed
    assert PermissionPolicy("balanced").evaluate("execute_host").requires_approval
    assert PermissionPolicy("ask").evaluate("read_workspace").requires_approval
    assert PermissionPolicy("yolo").evaluate("execute_host").allowed
    assert not PermissionPolicy("yolo").evaluate("invented_capability").allowed
    policy = PermissionPolicy("ask", {"execute_host:experiment-1", "search_web"})
    assert policy.evaluate("execute_host", "experiment-1").allowed
    assert not policy.evaluate("execute_host", "experiment-2").allowed
    assert policy.evaluate("search_web").allowed


def test_paths_and_environment(tmp_path, monkeypatch):
    for relative in ("../escape", "file.txt:secret", "CON", "folder/file. "):
        with pytest.raises(WorkspaceViolation):
            contained_path(tmp_path, relative)
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("PYTHONPATH", "untrusted")
    environment = sanitized_environment(tmp_path)
    assert not {"OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "PYTHONPATH"} & environment.keys()
    assert environment["HOME"] == str(tmp_path / ".home")


async def public_resolver(host, port):
    return ["93.184.216.34"]


@pytest.mark.asyncio
async def test_public_fetch_blocks_private_addresses_and_redirects(tmp_path):
    async def private_resolver(host, port):
        return ["127.0.0.1"]
    for url in ("file:///secret", "http://localhost/", "http://10.0.0.1/", "https://user:secret@example.com", "http://example.com:8080"):
        with pytest.raises(SourceRejected):
            await validate_public_url(url, private_resolver)
    requests = []
    async def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://localhost/secret"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        search = SearchClient(client=client, resolver=public_resolver)
        with pytest.raises(SourceRejected):
            await search.fetch_source("https://example.com", tmp_path)
    assert requests == ["https://example.com/"]


@pytest.mark.asyncio
async def test_source_capture_retains_provenance_and_limits(tmp_path):
    body = b"<html><head><title>Study</title></head><body><script>ignore()</script><p>Observed effect.</p></body></html>"
    async def handler(request):
        assert request.headers.get("authorization") == ""
        return httpx.Response(200, headers={"content-type": "text/html"}, content=body)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        search = SearchClient(client=client, resolver=public_resolver)
        record = await search.fetch_source("https://example.com/study#summary", tmp_path)
        assert record.title == "Study"
        assert "Observed effect." in record.text and "ignore()" not in record.text
        assert record.sha256 == hashlib.sha256(body).hexdigest()
        assert Path(record.raw_path).read_bytes() == body
        assert json.loads(Path(record.metadata_path).read_text())["requested_url"].endswith("#summary")
        search.max_source_bytes = 10
        with pytest.raises(SourceRejected):
            await search.fetch_source("https://example.com/large", tmp_path)


@pytest.mark.asyncio
async def test_search_protocols_and_missing_service():
    async def handler(request):
        if request.url.host == "api.openalex.org":
            assert request.url.params["search"] == "causality"
            return httpx.Response(200, json={"results": [{"id": "https://openalex.org/W1", "display_name": "Study", "publication_year": 2025, "abstract_inverted_index": {"A": [0], "result": [1]}, "authorships": [{"author": {"display_name": "Author"}}]}]})
        assert request.url.params["format"] == "json"
        return httpx.Response(200, json={"results": [{"title": "Result", "url": "https://example.com", "content": "Excerpt"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        search = SearchClient(searxng_url="http://localhost:8888", client=client)
        paper = (await search.search_literature("causality"))[0]
        assert paper.summary == "A result" and paper.authors == ["Author"]
        assert (await search.search_web("query"))[0].provider == "searxng"
        with pytest.raises(SearchUnavailable, match="Configure"):
            await SearchClient(client=client, native_fallback=False).search_web("query")


@pytest.mark.asyncio
async def test_paper_reader_prefers_openalex_full_text_without_persisting_key(tmp_path):
    async def handler(request):
        if request.url.host == "api.openalex.org":
            assert request.url.params["search.exact"] == '"A useful paper"'
            assert request.url.params["api_key"] == "test-openalex-key"
            return httpx.Response(200, json={"results": [
                {
                    "id": "https://openalex.org/W999",
                    "display_name": "A related but different paper",
                },
                {
                    "id": "https://openalex.org/W123",
                    "display_name": "A useful paper",
                    "content_urls": {
                        "grobid_xml": "https://content.openalex.org/works/W123.grobid-xml",
                    },
                    "best_oa_location": {"pdf_url": "https://arxiv.org/pdf/1234.5678"},
                },
            ]})
        assert request.url.host == "content.openalex.org"
        assert request.url.params["api_key"] == "test-openalex-key"
        return httpx.Response(
            200,
            headers={"content-type": "application/xml"},
            content=b"<article><title>Useful</title><p>Full experimental results.</p></article>",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        search = SearchClient(
            openalex_api_key="test-openalex-key", client=client, resolver=public_resolver,
        )
        paper = await search.fetch_paper("A useful paper", tmp_path)

    assert "Full experimental results." in paper.text
    assert "test-openalex-key" not in paper.requested_url
    assert "test-openalex-key" not in paper.final_url
    assert "test-openalex-key" not in Path(paper.metadata_path).read_text()


class StrictDecision(BaseModel):
    summary: str


class FlexibleDecision(BaseModel):
    arguments: dict


def test_partial_json_response_stream_exposes_only_public_field():
    partial = '{"updated_summary":"private planning","response":"Hello\\n**research'
    assert partial_json_string_field(partial, "response") == "Hello\n**research"
    assert partial_json_string_field('{"updated_summary":"contains \\\"response\\\": \\\"secret\\\""', "response") is None
    assert partial_json_string_field('{"response":null,"updated_summary":"private"}', "response") is None
    assert partial_json_string_field('{"response":"hi \\u26', "response") == "hi "


@pytest.mark.asyncio
async def test_model_credentials_structured_decisions_and_cost():
    calls = []
    async def parse(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_parsed=StrictDecision(summary="done"), id="response-1", status="completed", usage=SimpleNamespace(input_tokens=1000, output_tokens=200, input_tokens_details=SimpleNamespace(cached_tokens=100)))
    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    model = OpenAIModelRuntime("placeholder", "configured-model", client=client, input_cost_per_million=2, output_cost_per_million=8)
    with pytest.raises(MissingCredential):
        await model.turn({}, StrictDecision, "Research")
    assert not calls
    model.api_key = "sk-test-real-shaped"
    result = await model.turn({}, StrictDecision, "Research")
    assert result.cost_usd == pytest.approx(0.0036)
    assert result.decision.summary == "done"
    assert calls[0]["text_format"] is StrictDecision and calls[0]["store"] is False


@pytest.mark.asyncio
async def test_openai_stream_reports_progress_without_exposing_reasoning():
    progress = []
    response = SimpleNamespace(
        output=[], output_text='{"summary":"done"}', id="response-streamed", status="completed",
        usage=SimpleNamespace(
            input_tokens=120, output_tokens=48,
            input_tokens_details=SimpleNamespace(cached_tokens=10),
        ),
    )

    class Stream:
        def __aiter__(self):
            return self._events().__aiter__()

        async def _events(self):
            yield SimpleNamespace(type="response.reasoning_summary_text.delta", delta="r" * 300)
            yield SimpleNamespace(type="response.output_text.delta", delta='{"summary":"done"}')
            yield SimpleNamespace(type="response.completed", response=response)

    async def create(**kwargs):
        assert kwargs["stream"] is True
        assert kwargs["text"]["format"]["type"] == "json_schema"
        return Stream()

    model = OpenAIModelRuntime(
        "sk-test-real-shaped", "configured-model",
        client=SimpleNamespace(responses=SimpleNamespace(create=create), close=lambda: None),
        input_cost_per_million=1, output_cost_per_million=2,
    )
    result = await model.turn(
        {}, StrictDecision, "Research", progress=lambda update: progress.append(update)
    )
    assert result.decision.summary == "done"
    assert progress[0]["estimated"] is True
    assert any(item["estimated"] and item["output_tokens"] > 0 for item in progress)
    assert progress[-1]["input_tokens"] == 120 and progress[-1]["estimated"] is False


@pytest.mark.asyncio
async def test_flexible_decision_validated_and_invalid_usage_retained():
    async def create(**kwargs):
        assert kwargs["text"]["format"]["type"] == "json_schema"
        assert kwargs["text"]["format"]["strict"]
        assert "json" in kwargs["input"][0]["content"].lower()
        assert kwargs["input"][0]["content"].endswith("{}")
        return SimpleNamespace(output_text='{"arguments": "not an object"}', id="bad-response", status="completed", usage=SimpleNamespace(input_tokens=10, output_tokens=10, input_tokens_details=None))
    model = OpenAIModelRuntime("sk-test-real-shaped", "configured-model", client=SimpleNamespace(responses=SimpleNamespace(create=create)), input_cost_per_million=1, output_cost_per_million=2)
    with pytest.raises(ModelResponseError) as error:
        await model.turn({}, FlexibleDecision, "Research")
    assert error.value.cost_usd == pytest.approx(0.00003)
    assert error.value.usage["output_tokens"] == 10


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("git"), reason="Git required")
async def test_process_exec_snapshot_capture_and_timeout(tmp_path):
    backend = LocalProcessBackend(tmp_path)
    workspace = await backend.create_workspace("project", "experiment")
    workspace.write_file("experiment.py", "import os\nfrom pathlib import Path\nassert 'OPENAI_API_KEY' not in os.environ\nPath('result.txt').write_text('42')\nprint('completed')\n")
    with pytest.raises(WorkspaceViolation):
        workspace.write_file("../escape.py", "bad")
    result = await backend.run(workspace, [sys.executable, "experiment.py"], timeout_seconds=10)
    assert result.exit_code == 0 and "completed" in result.stdout
    assert result.git_commit and result.git_commit != workspace.parent_commit
    artifact = next(item for item in result.artifacts if item.relative_path == "result.txt")
    assert artifact.sha256 == hashlib.sha256(b"42").hexdigest()
    assert Path(result.manifest_path).exists()
    timeout = await backend.run(workspace, [sys.executable, "-c", "import time;time.sleep(30)"], timeout_seconds=0.2)
    assert timeout.timed_out and timeout.duration_seconds < 10


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("git"), reason="Git required")
async def test_explicit_cancel_and_docker_network_config(tmp_path):
    backend = LocalProcessBackend(tmp_path)
    workspace = await backend.create_workspace("project", "cancel")
    task = asyncio.create_task(backend.run(workspace, [sys.executable, "-c", "import time;time.sleep(30)"], run_id="run"))
    for _ in range(100):
        if "run" in backend._running:
            break
        await asyncio.sleep(0.03)
    assert await backend.cancel("run")
    result = await task
    assert result.cancelled and not result.timed_out
    assert not await backend.cancel("missing")
    docker = LocalDockerBackend(tmp_path)
    assert not docker.allow_network


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("git"), reason="Git required")
async def test_git_metadata_outside_mount_and_file_count_limit(tmp_path, monkeypatch):
    backend = LocalProcessBackend(tmp_path, max_artifact_files=3)
    workspace = await backend.create_workspace("project", "boundaries")
    assert (workspace.metadata_path / "repository.git").is_dir()
    assert not (workspace.path / ".git").exists()
    workspace.write_file(".gitattributes", "*.txt filter=unconfigured")
    # A model-created .git pointer must not redirect the host's Git commands.
    (workspace.path / ".git").write_text("gitdir: /nonexistent/attacker")
    assert len(await backend.snapshot(workspace)) == 40
    workspace.write_file("a.txt", "a")
    workspace.write_file("b.txt", "b")
    with pytest.raises(WorkspaceViolation, match="file-count"):
        await backend.collect_artifacts(workspace)
    docker = LocalDockerBackend(tmp_path, gpus="all")
    monkeypatch.setattr(shutil, "which", lambda _: "docker")
    command = docker._command(workspace, ["python", "main.py"], "test")
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--gpus") + 1] == "all"
    mount = command[command.index("--mount") + 1]
    assert str(workspace.path) in mount and str(workspace.metadata_path) not in mount


@pytest.mark.asyncio
async def test_model_close_cancels_active_request():
    started = asyncio.Event()
    closed = []
    async def parse(**kwargs):
        started.set()
        await asyncio.sleep(60)
    async def close():
        closed.append(True)
    client = SimpleNamespace(responses=SimpleNamespace(parse=parse), close=close)
    model = OpenAIModelRuntime("sk-test-real-shaped", "model", client=client, input_cost_per_million=1, output_cost_per_million=1)
    turn = asyncio.create_task(model.turn({}, StrictDecision, "Research", turn_id="active"))
    await started.wait()
    await model.close()
    with pytest.raises(asyncio.CancelledError):
        await turn
    assert closed == [True] and not model._active


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("git"), reason="Git required")
async def test_process_reclaims_descendants_after_success(tmp_path):
    backend = LocalProcessBackend(tmp_path)
    workspace = await backend.create_workspace("project", "descendants")
    workspace.write_file("child.py", "from pathlib import Path\nimport time\nPath('child-ready').write_text('ready')\ntime.sleep(1)\nPath('leaked-child').write_text('bad')\n")
    workspace.write_file("parent.py", "from pathlib import Path\nimport subprocess,sys,time\nsubprocess.Popen([sys.executable,'child.py'])\nwhile not Path('child-ready').exists(): time.sleep(0.01)\nprint('parent finished')\n")
    result = await backend.run(workspace, [sys.executable, "parent.py"], timeout_seconds=10)
    assert result.exit_code == 0
    assert (workspace.path / "child-ready").exists()
    await asyncio.sleep(1.2)
    assert not (workspace.path / "leaked-child").exists()


@pytest.mark.asyncio
async def test_web_search_native_fallback_is_explicit_and_handles_failure():
    class NativeSearch:
        def __init__(self, **kwargs):
            assert kwargs["verify"] is True and kwargs["timeout"] <= 8
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def text(self, query, **kwargs):
            assert query == "test query"
            return [{"title": "Native result", "href": "https://example.com/result", "body": "Result excerpt"}]
    async def unavailable(request):
        return httpx.Response(503)
    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
        search = SearchClient(searxng_url="http://localhost:8888", client=client, ddgs_factory=NativeSearch)
        result = (await search.search_web("test query"))[0]
        assert result.provider == "ddgs" and "unavailable" in result.fallback_reason
        assert result.url == "https://example.com/result"
        class FailedSearch(NativeSearch):
            def text(self, *args, **kwargs):
                raise RuntimeError("upstream error")
        search.ddgs_factory = FailedSearch
        with pytest.raises(SearchUnavailable, match="native DDGS"):
            await search.search_web("test query")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_name", "status"),
    [("AuthenticationError", 401), ("PermissionDeniedError", 403), ("BadRequestError", 400), ("NotFoundError", 404), ("RateLimitError", 429)],
)
async def test_rejected_model_requests_are_zero_cost_and_honor_none_effort(error_name, status):
    import openai
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    error = getattr(openai, error_name)("Rejected request", response=httpx.Response(status, request=request), body=None)
    async def parse(**kwargs):
        assert kwargs["reasoning"] == {"effort": "none"}
        raise error
    model = OpenAIModelRuntime("sk-test-real-shaped", "model", reasoning_effort="none", client=SimpleNamespace(responses=SimpleNamespace(parse=parse)), input_cost_per_million=1, output_cost_per_million=1)
    with pytest.raises(getattr(openai, error_name)) as caught:
        await model.turn({}, StrictDecision, "Research", turn_id="rejected")
    assert caught.value.cost_usd == 0 and not model._active


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "server", "missing_usage"])
async def test_ambiguous_model_failures_do_not_claim_zero_cost(failure):
    import openai
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    async def parse(**kwargs):
        assert "reasoning" not in kwargs
        if failure == "timeout":
            raise openai.APITimeoutError(request=request)
        if failure == "server":
            raise openai.InternalServerError("Server error", response=httpx.Response(500, request=request), body=None)
        return SimpleNamespace(usage=None)
    model = OpenAIModelRuntime("sk-test-real-shaped", "custom-model", reasoning_effort=None, client=SimpleNamespace(responses=SimpleNamespace(parse=parse)), input_cost_per_million=1, output_cost_per_million=1)
    with pytest.raises((openai.APITimeoutError, openai.InternalServerError, ModelResponseError)) as caught:
        await model.turn({}, StrictDecision, "Research")
    assert not hasattr(caught.value, "cost_usd")
