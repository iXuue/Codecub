from codecub.token_budget import clip_to_budget, resolve_prompt_budget


class WordCounter:
    source = "test"
    quality = "exact"

    def count(self, text):
        return len(str(text).split())


def test_clip_to_budget_uses_binary_search_and_respects_token_limit():
    counter = WordCounter()
    clipped = clip_to_budget("one two three four five", 3, counter)

    assert counter.count(clipped) <= 3
    assert clipped.endswith("...")


def test_resolve_prompt_budget_reserves_output_and_safety_margin():
    assert resolve_prompt_budget(8192, 1024, 256) == 6912
    assert resolve_prompt_budget(None, 1024, 256) is None
