"""Research artifacts must stay inside the configured sandbox, not a prefix sibling."""
import pytest

from app.errors import ToolExecutionError
from app.tools.builtin.analyze import _resolve_path
from app.tools.builtin.data import _resolve_in_sandbox, file_write_handler
from app.tools.builtin.documents import _resolve_sandbox_path


@pytest.mark.parametrize("resolve", [_resolve_in_sandbox, _resolve_path, _resolve_sandbox_path])
def test_research_paths_reject_sibling_with_same_prefix(tmp_path, monkeypatch, resolve):
    sandbox = tmp_path / "sandbox"
    sibling = tmp_path / "sandbox-private"
    sandbox.mkdir()
    sibling.mkdir()
    (sibling / "results.csv").write_text("seed,score\n1,90\n", encoding="utf-8")
    monkeypatch.setenv("SANDBOX_DIR", str(sandbox))

    with pytest.raises(ToolExecutionError, match="路径越界"):
        resolve("../sandbox-private/results.csv")


def test_file_write_cannot_overwrite_prefix_sibling(tmp_path, monkeypatch):
    sibling = tmp_path / "sandbox-private"
    sibling.mkdir()
    artifact = sibling / "paper.md"
    artifact.write_text("original", encoding="utf-8")
    monkeypatch.setenv("SANDBOX_DIR", str(tmp_path / "sandbox"))

    with pytest.raises(ToolExecutionError, match="路径越界"):
        file_write_handler("../sandbox-private/paper.md", "replacement")
    assert artifact.read_text(encoding="utf-8") == "original"


@pytest.mark.parametrize("resolve", [_resolve_in_sandbox, _resolve_path, _resolve_sandbox_path])
def test_research_paths_allow_nested_artifacts(tmp_path, monkeypatch, resolve):
    sandbox = tmp_path / "sandbox"
    artifact = sandbox / "experiment" / "results.csv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("seed,score\n1,90\n", encoding="utf-8")
    monkeypatch.setenv("SANDBOX_DIR", str(sandbox))
    assert resolve("experiment/results.csv") == artifact.resolve()


@pytest.mark.parametrize("resolve", [_resolve_in_sandbox, _resolve_path, _resolve_sandbox_path])
def test_research_paths_reject_symlink_escape(tmp_path, monkeypatch, resolve):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "private.csv"
    outside.write_text("private", encoding="utf-8")
    try:
        (sandbox / "linked.csv").symlink_to(outside)
    except OSError:
        pytest.skip("Creating symbolic links requires platform permission")
    monkeypatch.setenv("SANDBOX_DIR", str(sandbox))
    with pytest.raises(ToolExecutionError, match="路径越界"):
        resolve("linked.csv")
