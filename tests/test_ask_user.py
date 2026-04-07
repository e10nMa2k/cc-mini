"""Tests for AskUserQuestionTool."""

import pytest
from unittest.mock import patch, MagicMock
from tools.ask_user import AskUserQuestionTool, _select_one, _select_multi


@pytest.fixture
def tool():
    return AskUserQuestionTool()


# ---------------------------------------------------------------------------
# Schema / metadata
# ---------------------------------------------------------------------------

def test_name_and_schema(tool):
    assert tool.name == "AskUserQuestion"
    assert tool.is_read_only()
    schema = tool.input_schema
    assert "questions" in schema["properties"]
    assert schema["required"] == ["questions"]


def test_no_questions(tool):
    result = tool.execute(questions=[])
    assert result.is_error


def test_api_schema(tool):
    schema = tool.to_api_schema()
    assert schema["name"] == "AskUserQuestion"
    assert "description" in schema
    assert "input_schema" in schema


# ---------------------------------------------------------------------------
# Single-select (mock _select_one to avoid terminal interaction)
# ---------------------------------------------------------------------------

def test_single_select(tool):
    with patch("tools.ask_user._select_one", return_value="Python"):
        result = tool.execute(questions=[{
            "question": "Pick a language?",
            "options": [
                {"label": "Python", "description": "Simple"},
                {"label": "Go", "description": "Fast"},
            ],
        }])
    assert not result.is_error
    assert "Python" in result.content
    assert "Pick a language?" in result.content


def test_single_select_other(tool):
    # When user types custom text on "Other", _select_one returns that text directly
    with patch("tools.ask_user._select_one", return_value="Haskell"):
        result = tool.execute(questions=[{
            "question": "Pick a language?",
            "options": [
                {"label": "Python", "description": "Simple"},
                {"label": "Go", "description": "Fast"},
            ],
        }])
    assert not result.is_error
    assert "Haskell" in result.content


def test_single_select_cancel(tool):
    with patch("tools.ask_user._select_one", return_value=None):
        result = tool.execute(questions=[{
            "question": "Pick?",
            "options": [
                {"label": "A", "description": "a"},
                {"label": "B", "description": "b"},
            ],
        }])
    assert result.is_error
    assert "cancelled" in result.content.lower()


# ---------------------------------------------------------------------------
# Multi-select (mock _select_multi)
# ---------------------------------------------------------------------------

def test_multi_select(tool):
    with patch("tools.ask_user._select_multi", return_value=["Python", "Go"]):
        result = tool.execute(questions=[{
            "question": "Pick languages?",
            "options": [
                {"label": "Python", "description": "Simple"},
                {"label": "Go", "description": "Fast"},
            ],
            "multiSelect": True,
        }])
    assert not result.is_error
    assert "Python" in result.content
    assert "Go" in result.content


def test_multi_select_with_other(tool):
    # _select_multi now returns typed text directly (not "Other" label)
    with patch("tools.ask_user._select_multi", return_value=["Python", "Zig"]):
        result = tool.execute(questions=[{
            "question": "Pick languages?",
            "options": [
                {"label": "Python", "description": "Simple"},
                {"label": "Go", "description": "Fast"},
            ],
            "multiSelect": True,
        }])
    assert not result.is_error
    assert "Python" in result.content
    assert "Zig" in result.content


def test_multi_select_cancel(tool):
    with patch("tools.ask_user._select_multi", return_value=None):
        result = tool.execute(questions=[{
            "question": "Pick?",
            "options": [
                {"label": "A", "description": "a"},
                {"label": "B", "description": "b"},
            ],
            "multiSelect": True,
        }])
    assert result.is_error


# ---------------------------------------------------------------------------
# Multiple questions
# ---------------------------------------------------------------------------

def test_multiple_questions(tool):
    select_results = iter(["Python", "React"])
    with patch("tools.ask_user._select_one", side_effect=select_results):
        result = tool.execute(questions=[
            {
                "question": "Language?",
                "options": [
                    {"label": "Python", "description": "py"},
                    {"label": "Go", "description": "go"},
                ],
            },
            {
                "question": "Framework?",
                "options": [
                    {"label": "React", "description": "r"},
                    {"label": "Vue", "description": "v"},
                ],
            },
        ])
    assert not result.is_error
    assert "Language?" in result.content
    assert "Python" in result.content
    assert "Framework?" in result.content
    assert "React" in result.content
