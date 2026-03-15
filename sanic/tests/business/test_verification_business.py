import business.verification as verification_business


def test_get_challenge_word_returns_existing_without_creating_new(monkeypatch):
    monkeypatch.setattr(
        verification_business,
        "get_challenge_for_character_by_character_id",
        lambda character_id: "kobold",
    )
    monkeypatch.setattr(
        verification_business,
        "set_challenge_for_character_by_character_id",
        lambda character_id, challenge_word: (_ for _ in ()).throw(
            AssertionError("Should not persist when challenge already exists")
        ),
    )
    monkeypatch.setattr(
        verification_business.random,
        "choice",
        lambda words: (_ for _ in ()).throw(
            AssertionError("Should not pick random word when challenge already exists")
        ),
    )

    result = verification_business.get_challenge_word_for_character_by_character_id(99)

    assert result == "kobold"


def test_get_challenge_word_creates_and_persists_when_missing(monkeypatch):
    persisted = []

    monkeypatch.setattr(
        verification_business,
        "get_challenge_for_character_by_character_id",
        lambda character_id: None,
    )
    monkeypatch.setattr(
        verification_business.random,
        "choice",
        lambda words: "orc",
    )
    monkeypatch.setattr(
        verification_business,
        "set_challenge_for_character_by_character_id",
        lambda character_id, challenge_word: persisted.append(
            (character_id, challenge_word)
        ),
    )

    result = verification_business.get_challenge_word_for_character_by_character_id(42)

    assert result == "orc"
    assert persisted == [(42, "orc")]


def test_get_challenge_word_treats_empty_cached_value_as_missing(monkeypatch):
    persisted = []

    monkeypatch.setattr(
        verification_business,
        "get_challenge_for_character_by_character_id",
        lambda character_id: "",
    )
    monkeypatch.setattr(
        verification_business.random,
        "choice",
        lambda words: "elf",
    )
    monkeypatch.setattr(
        verification_business,
        "set_challenge_for_character_by_character_id",
        lambda character_id, challenge_word: persisted.append(
            (character_id, challenge_word)
        ),
    )

    result = verification_business.get_challenge_word_for_character_by_character_id(7)

    assert result == "elf"
    assert persisted == [(7, "elf")]
