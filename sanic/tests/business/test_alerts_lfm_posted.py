import pytest
from pydantic import ValidationError

from business.alerts.base import AlertRule
from business.alerts.types.lfm_posted import (
    LfmPostedAlertType,
    LfmPostedCriteria,
    criteria_matches,
)


def _data(**overrides):
    data = {
        "server_name": "argonnessen",
        "lfm_id": 10,
        "quest_id": 100,
        "difficulty": "Elite",
        "minimum_level": 20,
        "maximum_level": 24,
        "leader_name": "Leader",
        "member_count": 3,
    }
    data.update(overrides)
    return data


def _rule(rule_id=1, user_id=1, **criteria):
    criteria.setdefault("quest_ids", [100])
    return AlertRule(
        id=rule_id,
        user_id=user_id,
        type="lfm_posted",
        criteria=LfmPostedCriteria(**criteria),
    )


# ----- criteria validation -----


def test_criteria_requires_at_least_one_quest():
    with pytest.raises(ValidationError):
        LfmPostedCriteria(quest_ids=[])


def test_criteria_rejects_too_many_quests():
    with pytest.raises(ValidationError):
        LfmPostedCriteria(quest_ids=list(range(1, 22)))


def test_criteria_rejects_non_positive_quest_ids():
    with pytest.raises(ValidationError):
        LfmPostedCriteria(quest_ids=[0])


def test_criteria_dedupes_quests_and_canonicalises_names():
    c = LfmPostedCriteria(
        quest_ids=[5, 5, 6],
        server_names=["argonnessen", "ARGONNESSEN", "Cannith"],
        difficulties=["elite", "Epic Hard"],
    )
    assert c.quest_ids == [5, 6]
    assert c.server_names == ["Argonnessen", "Cannith"]
    assert c.difficulties == ["Elite", "Epic Hard"]


def test_criteria_rejects_unknown_server():
    with pytest.raises(ValidationError):
        LfmPostedCriteria(quest_ids=[1], server_names=["Nowhere"])


def test_criteria_rejects_unknown_difficulty():
    with pytest.raises(ValidationError):
        LfmPostedCriteria(quest_ids=[1], difficulties=["Impossible"])


def test_criteria_rejects_inverted_level_range():
    with pytest.raises(ValidationError):
        LfmPostedCriteria(quest_ids=[1], min_level=20, max_level=10)


def test_criteria_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        LfmPostedCriteria(quest_ids=[1], leader_name="x")


# ----- matching -----
# Keep in sync with the frontend summary/form semantics (src/components/alerts/types/lfmPosted).

MATCH_CASES = [
    ("quest only", {}, {}, True),
    ("other quest", {}, {"quest_id": 101}, False),
    ("no quest", {}, {"quest_id": None}, False),
    ("server listed", {"server_names": ["Argonnessen"]}, {}, True),
    ("server case-insensitive", {"server_names": ["Argonnessen"]}, {"server_name": "ARGONNESSEN"}, True),
    ("server not listed", {"server_names": ["Cannith"]}, {}, False),
    ("levels overlap", {"min_level": 22, "max_level": 30}, {}, True),
    ("levels touch at top", {"min_level": 24, "max_level": 30}, {}, True),
    ("levels touch at bottom", {"min_level": 10, "max_level": 20}, {}, True),
    ("levels above", {"min_level": 25}, {}, False),
    ("levels below", {"max_level": 19}, {}, False),
    ("lfm without levels", {"min_level": 25}, {"minimum_level": None, "maximum_level": None}, True),
    ("difficulty listed", {"difficulties": ["Elite"]}, {}, True),
    ("difficulty case-insensitive", {"difficulties": ["Elite"]}, {"difficulty": "elite"}, True),
    ("difficulty not listed", {"difficulties": ["Hard"]}, {}, False),
    ("difficulty missing", {"difficulties": ["Hard"]}, {"difficulty": None}, False),
    (
        "all constraints",
        {"server_names": ["Argonnessen"], "min_level": 20, "max_level": 20, "difficulties": ["Elite", "Reaper"]},
        {},
        True,
    ),
]


@pytest.mark.parametrize("name,criteria,data_overrides,expected", MATCH_CASES)
def test_criteria_matches(name, criteria, data_overrides, expected):
    criteria = {"quest_ids": [100], **criteria}
    assert criteria_matches(LfmPostedCriteria(**criteria), _data(**data_overrides)) is expected


def test_index_and_match_returns_only_matching_rules():
    t = LfmPostedAlertType(quest_name_loader=lambda: {})
    rules = [
        _rule(1, user_id=1),
        _rule(2, user_id=2, quest_ids=[100, 200], server_names=["Cannith"]),
        _rule(3, user_id=3, quest_ids=[200]),
    ]
    index = t.build_index(rules)
    assert sorted(index) == [100, 200]
    assert [r.id for r in t.match(_data(), index)] == [1]
    assert [r.id for r in t.match(_data(quest_id=200), index)] == [3]
    assert t.match(_data(quest_id=None), index) == []


# ----- rendering -----


def test_render_uses_loaded_quest_name_and_details():
    t = LfmPostedAlertType(quest_name_loader=lambda: {100: "The Pit"})
    t.refresh()
    alert = t.render(_data(), _rule())
    assert alert.title == "The Pit on Argonnessen"
    assert alert.body == "Leader · Level 20-24 · Elite · 3 members"
    assert alert.url == "/grouping/argonnessen"
    assert alert.tag == "lfm-argonnessen-10"
    assert alert.rule_id == 1
    assert alert.type == "lfm_posted"


def test_render_falls_back_when_quest_name_unknown():
    t = LfmPostedAlertType(quest_name_loader=lambda: {})
    alert = t.render(_data(member_count=1, leader_name=None), _rule())
    assert alert.title == "A quest on Argonnessen"
    assert alert.body == "Level 20-24 · Elite · 1 member"


def test_refresh_keeps_old_names_when_loader_fails():
    calls = []

    def loader():
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("db down")
        return {100: "The Pit"}

    t = LfmPostedAlertType(quest_name_loader=loader)
    t.refresh()
    t.refresh()
    assert t.render(_data(), _rule()).title == "The Pit on Argonnessen"


def test_dedupe_key_identifies_lfm_and_quest():
    t = LfmPostedAlertType(quest_name_loader=lambda: {})
    assert t.dedupe_key(_data(server_name="Argonnessen")) == "lfm:argonnessen:10:100"
