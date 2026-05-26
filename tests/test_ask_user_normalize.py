from __future__ import annotations

import pytest

from cairn.tools.interact import _validate_and_normalize_ask_user_questions


def test_validate_rejects_list_of_strings():
    with pytest.raises(ValueError, match="ask_user 参数错误"):
        _validate_and_normalize_ask_user_questions(["Q1", "Q2"])  # type: ignore[arg-type]


def test_validate_requires_header_and_options():
    with pytest.raises(ValueError, match="header"):
        _validate_and_normalize_ask_user_questions(
            [{"question": "Q1", "header": "  ", "options": [{"label": "A"}]}]
        )

    with pytest.raises(ValueError, match="options"):
        _validate_and_normalize_ask_user_questions(
            [{"question": "Q1", "header": "H1", "options": []}]
        )


def test_normalize_makes_duplicate_headers_unique():
    questions = [
        {"question": "Q1", "header": "H", "options": [{"label": "A"}]},
        {"question": "Q2", "header": "H", "options": [{"label": "B"}]},
    ]

    normalized = _validate_and_normalize_ask_user_questions(questions)
    assert [q.header for q in normalized] == ["H#1", "H#2"]


def test_normalize_collapses_header_whitespace():
    questions = [{"question": "Q1", "header": "  A   B  ", "options": [{"label": "A"}]}]
    normalized = _validate_and_normalize_ask_user_questions(questions)
    assert normalized[0].header == "A B"


def test_normalize_falls_back_question_to_header():
    questions = [{"question": " ", "header": "H1", "options": [{"label": "A"}]}]
    normalized = _validate_and_normalize_ask_user_questions(questions)
    assert normalized[0].question == "H1"
