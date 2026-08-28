"""Tests for LocalReaderTool.

Covers: reading csv, json, txt, md, xlsx, pdf,
        path traversal protection, file size limits,
        error handling.
"""

import json
import sys
from unittest.mock import patch

import pytest

from plugins.local_reader.tool import MAX_FILE_SIZE_BYTES, LocalReaderTool


@pytest.fixture
def tool():
    return LocalReaderTool()


@pytest.fixture
def workspace_dir(tmp_path):
    """Create workspace/reading/ directory with test files."""
    workspace = tmp_path / "workspace" / "reading"
    workspace.mkdir(parents=True)
    return workspace


# ==================== TEST _EXECUTE ====================


async def test_execute_missing_path(tool):
    """When path is missing, _execute returns error."""
    result = await tool._execute(path="")
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_url"


async def test_execute_file_not_found(tool, data_dir, monkeypatch):
    """When file doesn't exist, _execute returns error."""
    monkeypatch.chdir(data_dir.parent)
    result = await tool._execute(path="data/nonexistent.txt")
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_url"


async def test_execute_path_not_allowed(tool, tmp_path, monkeypatch):
    """When path is outside allowed dirs, _execute returns error."""
    monkeypatch.chdir(tmp_path)
    result = await tool._execute(path="../../etc/passwd")
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_url"
    assert "not allowed" in result["message"]


async def test_execute_file_too_large(tool, data_dir, monkeypatch):
    """When file exceeds MAX_FILE_SIZE_BYTES, _execute returns error."""
    monkeypatch.chdir(data_dir.parent)
    large_file = data_dir / "large.txt"
    large_file.write_text("x" * (MAX_FILE_SIZE_BYTES + 1))

    result = await tool._execute(path="data/large.txt")
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_url"
    assert "too large" in result["message"]


async def test_execute_unsupported_format(tool, data_dir, monkeypatch):
    """When file format is unsupported, _execute returns error."""
    monkeypatch.chdir(data_dir.parent)
    unsupported = data_dir / "test.xyz"
    unsupported.write_text("test")

    result = await tool._execute(path="data/test.xyz")
    assert result["status"] == "error"
    assert result["error_type"] == "invalid_url"
    assert "Unsupported" in result["message"]


# ==================== TEST TEXT PARSER ====================


async def test_read_txt(tool, workspace_dir, monkeypatch):
    """Read a .txt file."""
    monkeypatch.chdir(workspace_dir.parent.parent)  # workspace/ -> project root
    txt_file = workspace_dir / "test.txt"
    txt_file.write_text("Hello, Vasily!")

    result = await tool._execute(path="workspace/reading/test.txt")
    assert result["status"] == "success"
    assert result["parser"] == "text"
    assert result["content"] == "Hello, Vasily!"


async def test_read_md(tool, data_dir, monkeypatch):
    """Read a .md file."""
    monkeypatch.chdir(data_dir.parent)
    md_file = data_dir / "test.md"
    md_file.write_text("# Heading\n\nContent")

    result = await tool._execute(path="data/test.md")
    assert result["status"] == "success"
    assert result["parser"] == "text"
    assert "# Heading" in result["content"]


# ==================== TEST JSON PARSER ====================


async def test_read_json(tool, data_dir, monkeypatch):
    """Read a .json file."""
    monkeypatch.chdir(data_dir.parent)
    json_file = data_dir / "test.json"
    json_file.write_text(json.dumps({"key": "value", "number": 42}))

    result = await tool._execute(path="data/test.json")
    assert result["status"] == "success"
    assert result["parser"] == "json"
    assert result["content"]["key"] == "value"
    assert result["content"]["number"] == 42


# ==================== TEST CSV PARSER ====================


async def test_read_csv(tool, data_dir, monkeypatch):
    """Read a .csv file."""
    monkeypatch.chdir(data_dir.parent)
    csv_file = data_dir / "test.csv"
    csv_file.write_text("name,age\nAlex,30\nBob,25")

    result = await tool._execute(path="data/test.csv")
    assert result["status"] == "success"
    assert result["parser"] == "csv"
    assert len(result["content"]) == 2
    assert result["content"][0]["name"] == "Alex"


# ==================== TEST XLSX PARSER ====================


async def test_read_xlsx_missing_dependency(tool, data_dir, monkeypatch):
    """When openpyxl is not installed, return error."""
    monkeypatch.chdir(data_dir.parent)
    xlsx_file = data_dir / "test.xlsx"
    xlsx_file.write_text("dummy")

    with patch.dict(sys.modules, {"openpyxl": None}):
        result = await tool._execute(path="data/test.xlsx")
        assert result["status"] == "success"
        assert "openpyxl is not installed" in result["content"]["error"]


# ==================== TEST PDF PARSER ====================


async def test_read_pdf_missing_dependency(tool, data_dir, monkeypatch):
    """When PyPDF2 is not installed, return error."""
    monkeypatch.chdir(data_dir.parent)
    pdf_file = data_dir / "test.pdf"
    pdf_file.write_text("dummy")

    with patch.dict(sys.modules, {"PyPDF2": None}):
        result = await tool._execute(path="data/test.pdf")
        assert result["status"] == "success"
        assert "PyPDF2 is not installed" in result["content"]["error"]


# ==================== TEST PATH TRAVERSAL ====================


def test_is_path_allowed_valid(tool, workspace_dir, monkeypatch):
    """Valid paths in workspace/reading/ should be allowed."""
    monkeypatch.chdir(workspace_dir.parent.parent)
    assert tool._is_path_allowed("workspace/reading/test.txt") is True


def test_is_path_allowed_invalid(tool, tmp_path, monkeypatch):
    """Paths outside allowed dirs should be blocked."""
    monkeypatch.chdir(tmp_path)
    assert tool._is_path_allowed("data/../etc/passwd") is False
    assert tool._is_path_allowed("reports/../secret") is False
    assert tool._is_path_allowed("something.txt") is False
