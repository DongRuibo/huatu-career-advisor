from dataclasses import fields, replace

import pytest

from app.dictionary import TagDefinition
from app.etl import DEFAULT_WORKBOOK, load_import_data
from app.matcher import JobTag, MatchJob, ScoringCoverage, match_jobs
from app.profile import Preference, PreferenceSuggestion, Profile


@pytest.fixture(scope="module")
def dataset():
    data = load_import_data(DEFAULT_WORKBOOK)
    dictionary = {
        tag_id: TagDefinition(**{field.name: definition[field.name] for field in fields(TagDefinition)})
        for tag_id, definition in data.definitions.items()
    }
    jobs = {
        job_id: MatchJob(**{field.name: row[field.name] for field in fields(MatchJob) if field.name != "tags"})
        for job_id, row in data.jobs.items()
    }
    for (job_id, tag_id), tag in data.tags.items():
        jobs[job_id].tags[tag_id] = JobTag(tag["value"], tag["evidence"])
    return jobs, dictionary


def preference(tag_id, value, strength="soft", constraint_type="prefer"):
    return Preference(tag_id, value, strength, constraint_type, None, None)


SA = Profile({
    "T001": preference("T001", ["成都"], "hard", "in"),
    "T002": preference("T002", ["体制内"], "hard", "in"),
    "T003": preference("T003", "偶尔", "hard", "max"),
    "T004": preference("T004", "高"),
    "T005": preference("T005", "多"),
    "T008": preference("T008", "中上"),
}, {}, [])

SB = Profile({
    "T003": preference("T003", "偶尔", "hard", "max"),
    "T005": preference("T005", "多"),
    "T006": preference("T006", "一般", "soft", "max"),
    "T013": preference("T013", {"preferred": "少", "acceptable_max": "偶尔"}),
}, {}, [], pending_suggestions={
    "T007": PreferenceSuggestion("T007", {"avoid": "办公室", "needs_confirmation": True}, 1, "不太喜欢天天埋头整理资料"),
    "T012": PreferenceSuggestion("T012", {"avoid": "材料管理", "needs_confirmation": True}, 1, "不太喜欢天天埋头整理资料"),
})

SC = Profile({
    "T001": SA.preferences["T001"],
    "T002": SA.preferences["T002"],
    "T003": preference("T003", "无", "hard", "max"),
    "T005": SA.preferences["T005"],
}, {"education": "本科", "political_status": "群众", "experience_years": 0}, [])

SA_EXPANDED = replace(SA, preferences={**SA.preferences, "T001": preference("T001", ["成都", "重庆"], "hard", "in")})
SC_REVISED = replace(SC, preferences={
    **SC.preferences, "T001": SA_EXPANDED.preferences["T001"], "T003": SA.preferences["T003"],
})

SA_CANDIDATES = {"P001", "P005", "P009", "P011", "P012", "P018"}
EXPANDED_CANDIDATES = SA_CANDIDATES | {"P002", "P008", "P010", "P017", "P019"}


@pytest.mark.parametrize(("profile", "expected"), [
    (SA, SA_CANDIDATES),
    (SA_EXPANDED, EXPANDED_CANDIDATES),
    (SB, {"P001", "P002", "P004", "P005", "P007", "P008", "P009", "P010", "P011", "P012",
          "P013", "P014", "P015", "P017", "P018", "P019", "P020"}),
    (SC, {"P001", "P005", "P012", "P018"}),
    (SC_REVISED, EXPANDED_CANDIDATES),
], ids=["S-A-6", "S-A-11", "S-B-17", "S-C-4", "S-C-11"])
def test_candidate_baselines(profile, expected, dataset):
    jobs, dictionary = dataset
    result = match_jobs(profile, jobs.values(), dictionary)

    assert {match.job_id for match in result} == expected
    assert len(result) == len(expected)
    assert all(match.hard_condition_status == "confirmed" for match in result)
    assert "P021" not in {match.job_id for match in result}


@pytest.mark.parametrize("tag_id", ["T001", "T002", "T003"])
def test_unknown_hard_condition_retains_candidate_but_is_not_a_match_reason(dataset, tag_id):
    jobs, dictionary = dataset
    source = jobs["P001"]
    changes = {
        "T001": {"city": None},
        "T002": {"direction": None},
        "T003": {"tags": {tag: fact for tag, fact in source.tags.items() if tag != "T003"}},
    }
    missing_value = replace(source, **changes[tag_id])
    different = replace(missing_value, **({"direction": "央国企"} if tag_id == "T001" else {"city": "重庆"}))
    result, = match_jobs(SA, [missing_value, different], dictionary)

    assert result.hard_condition_status == "pending_confirmation"
    assert result.unknown_tags == [tag_id]
    condition = next(condition for condition in result.hard_conditions if condition.tag_id == tag_id)
    assert condition.status == "pending_confirmation" and condition.job_value is None
    assert len([item for item in result.hard_conditions if item.status == "confirmed"]) == 2
    assert tag_id not in {reason.tag_id for reason in result.match_reasons}


def test_score_coverage_distinguishes_unknown_from_known_zero(dataset):
    jobs, dictionary = dataset
    results = {match.job_id: match for match in match_jobs(SA, jobs.values(), dictionary)}
    partially_known = results["P012"]
    assert partially_known.score == 0.75
    assert partially_known.scoring_coverage == ScoringCoverage(2, 1)
    assert partially_known.unknown_tags == ["T008"]
    assert {item.tag_id: item.score for item in partially_known.soft_scores} == {"T004": 1.0, "T005": 0.5, "T008": None}
    assert all(reason.evidence for reason in partially_known.match_reasons)

    known_zero = results["P005"]
    assert known_zero.score == 0.5 and known_zero.scoring_coverage == ScoringCoverage(3, 0)
    assert [(item.tag_id, item.job_value, item.score) for item in known_zero.unmet_preferences] == [("T005", "少", 0.0)]


def test_soft_overtime_and_tentative_suggestions(dataset):
    jobs, dictionary = dataset
    results = {match.job_id: match for match in match_jobs(SB, jobs.values(), dictionary)}
    assert not {"T007", "T012"}.intersection(item.tag_id for result in results.values() for item in result.soft_scores)
    assert results["P001"].scoring_coverage == ScoringCoverage(3, 0)
    assert next(item.score for item in results["P009"].soft_scores if item.tag_id == "T013") == 0.5
    assert next(item.score for item in results["P014"].soft_scores if item.tag_id == "T013") == 0.0

    confirmed = replace(SB, preferences={**SB.preferences, "T007": preference("T007", {
        "avoid": "办公室", "needs_confirmation": False,
    })}, pending_suggestions={"T012": SB.pending_suggestions["T012"]})
    office, hybrid = [next(item.score for item in match.soft_scores if item.tag_id == "T007")
                      for match in sorted(match_jobs(confirmed, [jobs["P001"], jobs["P009"]], dictionary), key=lambda item: item.job_id)]
    assert (office, hybrid) == (0.0, 1.0)


@pytest.mark.parametrize(("tag_id", "wanted", "kind", "values", "scores"), [
    ("T008", "中上", "prefer", ["中等", "中上", "较高"], [0.5, 1.0, 1.0]),
    ("T003", "偶尔", "max", ["无", "偶尔", "频繁"], [1.0, 1.0, 0.5]),
    ("T004", "中", "min", ["低", "中", "高"], [0.5, 1.0, 1.0]),
    ("T005", "中", "max", ["少", "中", "多"], [1.0, 1.0, 0.5]),
    ("T016", "中", "min", ["低", "中", "高"], [0.5, 1.0, 1.0]),
    ("T001", ["成都", "重庆"], "in", ["成都", "重庆", "绵阳"], [1.0, 1.0, 0.0]),
    ("T007", "混合", "prefer", ["办公室", "混合", "外勤"], [0.0, 1.0, 0.0]),
])
def test_soft_scoring_rules_keep_all_candidates(dataset, tag_id, wanted, kind, values, scores):
    jobs, dictionary = dataset
    profile = Profile({tag_id: preference(tag_id, wanted, constraint_type=kind)}, {}, [])
    candidates = [replace(jobs["P001"], job_id=f"JOB{index}", city=value if tag_id == "T001" else "成都",
                          tags={tag_id: JobTag(value, "样本依据")}) for index, value in enumerate(values)]
    result = {match.job_id: match for match in match_jobs(profile, candidates, dictionary)}
    assert [result[f"JOB{index}"].score for index in range(3)] == scores


def test_unscored_candidates_follow_known_scores_and_same_score_orders_by_job_id(dataset):
    jobs, dictionary = dataset
    profile = Profile({"T004": preference("T004", "低")}, {}, [])
    candidates = [replace(jobs["P001"], job_id="A-unknown", tags={}), jobs["P005"], jobs["P001"]]
    matches = match_jobs(profile, candidates, dictionary)
    assert [(item.job_id, item.score) for item in matches] == [("P001", 0.0), ("P005", 0.0), ("A-unknown", None)]
    assert matches[-1].scoring_coverage == ScoringCoverage(0, 1)

    empty = match_jobs(Profile({}, {}, []), candidates, dictionary)
    assert all(item.score is None and item.scoring_coverage == ScoringCoverage(0, 0) for item in empty)


def test_qualification_hints_do_not_filter_or_rank(dataset):
    jobs, dictionary = dataset
    results = match_jobs(SC_REVISED, jobs.values(), dictionary)
    unqualified = match_jobs(replace(SC_REVISED, qualifications={}), jobs.values(), dictionary)
    assert [(item.job_id, item.score) for item in results] == [(item.job_id, item.score) for item in unqualified]
    by_id = {match.job_id: match for match in results}
    p008 = {hint.tag_id: (hint.status, hint.requirement, hint.current) for hint in by_id["P008"].qualification_hints}
    assert p008 == {
        "T017": ("preparation_needed", "硕士", "本科"),
        "T018": ("preparation_needed", 2, 0),
        "T020": ("reference_only", "文科优先", None),
    }
    p019 = next(hint for hint in by_id["P019"].qualification_hints if hint.tag_id == "T019")
    assert p019.status == "preparation_needed" and (p019.current, p019.requirement) == ("群众", "中共党员")
    empty_p008 = next(match for match in unqualified if match.job_id == "P008")
    assert next(hint.status for hint in empty_p008.qualification_hints if hint.tag_id == "T017") == "pending_confirmation"
