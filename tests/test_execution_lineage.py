import json
import os
import sys

import pytest

from sapling.integrations.execution import ExecutionUnavailable, LocalProcessBackend, WorkspaceViolation


@pytest.mark.asyncio
async def test_fork_uses_recorded_snapshot_instead_of_mutable_outputs(tmp_path):
    backend = LocalProcessBackend(tmp_path)
    parent = await backend.create_workspace("project", "parent")
    parent.write_file("train.py", "print('original')")
    (parent.path / "data.bin").write_bytes(bytes(range(256)))
    commit = await backend.snapshot(parent)
    parent.write_file("train.py", "print('modified after the snapshot')")
    child = await backend.fork_workspace("project", "child", "parent", commit)
    assert child.source_commit == commit and child.source_experiment_id == "parent"
    assert (child.path / "train.py").read_text() == "print('original')"
    assert (child.path / "data.bin").read_bytes() == bytes(range(256))
    result = await backend.run(child, [sys.executable, "train.py"])
    manifest = json.loads(open(result.manifest_path, encoding="utf-8").read())
    assert manifest["source_commit"] == commit
    assert result.stdout.strip() == "original"
    assert any(item["relative_path"] == "data.bin" for item in manifest["inputs"])
    with pytest.raises(WorkspaceViolation):
        await backend.fork_workspace("other-project", "child", "parent", commit)


@pytest.mark.asyncio
async def test_evaluator_version_and_candidate_integrity(tmp_path):
    backend = LocalProcessBackend(tmp_path)
    candidate = await backend.create_workspace("project", "candidate")
    candidate.write_file("results.json", '{"predictions": [1, 2, 3]}')
    evaluator = {"evaluate.py": "import json\nfrom pathlib import Path\nx=json.loads(Path('candidate/results.json').read_text())\nprint(json.dumps({'sum':sum(x['predictions'])}))"}
    command = [sys.executable, "evaluate.py"]
    first = await backend.evaluate(candidate, evaluator, command)
    second = await backend.evaluate(candidate, evaluator, command, expected_version=first["evaluator_version"])
    assert first["evaluator_version"] == second["evaluator_version"]
    assert first["metrics"] == {"sum": 6}
    assert first["inputs_unchanged"] and first["metrics_are_untrusted"]
    with pytest.raises(WorkspaceViolation, match="version"):
        await backend.evaluate(candidate, {"evaluate.py": "print('{}')"}, command,
                               expected_version=first["evaluator_version"])
    tampering = {"evaluate.py": "from pathlib import Path\nPath('candidate/results.json').write_text('{}')\nprint('{\"sum\":99}')"}
    invalid = await backend.evaluate(candidate, tampering, command)
    assert not invalid["inputs_unchanged"] and invalid["metrics"] is None
    assert json.loads((candidate.path / "results.json").read_text()) == {"predictions": [1, 2, 3]}


@pytest.mark.asyncio
async def test_downloaded_data_is_not_collected_as_an_experiment_output(tmp_path):
    backend = LocalProcessBackend(tmp_path)
    workspace = await backend.create_workspace("project", "data-is-input")
    workspace.write_file("data/download.txt", "downloaded input")
    workspace.write_file("runs/metrics.json", '{"score": 1}')
    artifacts = await backend.collect_artifacts(workspace)
    paths = {item.relative_path for item in artifacts}
    assert "runs/metrics.json" in paths
    assert "data/download.txt" not in paths


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows-only command policy")
async def test_windows_process_backend_rejects_wsl_shells(tmp_path):
    backend = LocalProcessBackend(tmp_path)
    workspace = await backend.create_workspace("project", "windows-command")
    with pytest.raises(ExecutionUnavailable, match="Linux shell commands are disabled"):
        await backend.run(workspace, ["bash", "-c", "echo blocked"])
