from features.compact import (
    AUTOCOMPACT_BUFFER_TOKENS,
    auto_compact_threshold,
    estimate_tokens,
    estimate_tokens_conservative,
)


def _messages(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


def test_conservative_estimate_matches_ascii_approximation():
    messages = _messages("abcdefgh")
    assert estimate_tokens(messages) == 2
    assert estimate_tokens_conservative(messages) == 2


def test_conservative_estimate_counts_non_ascii_individually():
    messages = _messages("中文测试")
    assert estimate_tokens(messages) == 1
    assert estimate_tokens_conservative(messages) == 4


def test_conservative_estimate_handles_mixed_content():
    assert estimate_tokens_conservative(_messages("abcd中文")) == 3


def test_auto_compact_threshold_uses_known_model_window():
    assert auto_compact_threshold("claude-sonnet-4-6") == (
        1_000_000 - 20_000 - AUTOCOMPACT_BUFFER_TOKENS
    )


def test_auto_compact_threshold_uses_default_window():
    assert auto_compact_threshold("unknown-model") == (
        200_000 - 20_000 - AUTOCOMPACT_BUFFER_TOKENS
    )
