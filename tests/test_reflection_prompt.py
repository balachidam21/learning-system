from pathlib import Path

PROMPT = Path(__file__).parent.parent / "prompts" / "reflection.v1.txt"


def test_prompt_exists_and_nonempty():
    assert PROMPT.exists()
    assert PROMPT.read_text().strip()


def test_prompt_pins_the_proposal_types():
    text = PROMPT.read_text()
    for t in ("new_skill", "improve_skill", "workflow_fix", "new_check"):
        assert t in text


def test_prompt_states_the_bounds_and_dedup_contract():
    text = PROMPT.read_text().lower()
    assert "at most 3" in text or "maximum of 3" in text
    assert "at least 2" in text or "two pieces of evidence" in text
    # the in-prompt semantic-dedup placeholder the reflector fills in
    assert "{open_titles}" in PROMPT.read_text()
    assert "do not re-propose" in text or "do not propose" in text
