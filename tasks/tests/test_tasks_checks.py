import json
from pathlib import Path

import pytest

from tasks import checks
from tasks.tests.fake_harbor import CALC_RESULT, GOOD_CARD

SOURCES = [{"n": 1, "key": "[1]"}, {"n": 2, "key": "[2]"}]


@pytest.fixture
def srcs(tmp_path):
    p = tmp_path / "sources.json"
    p.write_text(json.dumps(SOURCES))
    return str(p)


def test_cited_numbers():
    assert checks.cited_numbers("a [1] b [2, 4] c [5–7] d [3-3] [x]") == {1, 2, 3, 4, 5, 6, 7}


def card(tmp_path, text):
    p = tmp_path / "methods-card.md"
    p.write_text(text)
    return p


def test_good_card_passes(tmp_path, srcs):
    assert checks.check_card(card(tmp_path, GOOD_CARD), srcs) == []


def test_uncited_claim_is_reported(tmp_path, srcs):
    text = GOOD_CARD.replace("emerge from the probabilistic representation of parameters and data [1].",
                             "emerge from the probabilistic representation of parameters and data.")
    problems = checks.check_card(card(tmp_path, text), srcs)
    assert len(problems) == 1 and "probabilistic representation" in problems[0]


def test_uncited_bullet_is_reported(tmp_path, srcs):
    text = GOOD_CARD.replace("- Not stated in the available sources.",
                             "- Free energies were measured by isothermal titration calorimetry at 298 K.")
    assert any("calorimetry" in p for p in checks.check_card(card(tmp_path, text), srcs))


def test_unknown_numbers_missing_sections_and_refs(tmp_path, srcs):
    text = GOOD_CARD.replace("molecular fragments [1]", "molecular fragments [9]").replace("## Analysis", "## Other")
    problems = " ".join(checks.check_card(card(tmp_path, text), srcs))
    assert "[9]" in problems or "9" in problems
    assert "'## Analysis'" in problems
    assert "References section does not list [9]" in problems


def test_missing_card(tmp_path, srcs):
    assert checks.check_card(tmp_path / "nope.md", srcs) == ["nope.md is missing or empty"]


def calc_dir(tmp_path, result=None, **files):
    d = tmp_path / "calc"
    d.mkdir()
    (d / "result.json").write_text(json.dumps(CALC_RESULT if result is None else result))
    defaults = {"run_calc.py": "print(1)\n", "output.log": "E=-23\n",
                "explanation.md": "word " * 130 + "The group scales charges [1].\n"}
    for name, text in {**defaults, **files}.items():
        if text is not None:
            (d / name).write_text(text)
    return d


def test_good_calc_passes(tmp_path, srcs):
    assert checks.check_calc(calc_dir(tmp_path), srcs) == []


@pytest.mark.parametrize("change,needle", [
    ({"e_int_kcal_mol": "-23"}, "'e_int_kcal_mol' must be a finite number"),
    ({"e_int_kcal_mol": 23.4}, "outside the plausible range"),
    ({"r_min_angstrom": None}, "'r_min_angstrom'"),
    ({"scan": [{"r_angstrom": 2.0, "e_int_kcal_mol": -1}]}, "at least 5 points"),
    ({"e_coulomb_ecc_kcal_mol": -30.0}, "should be 0.75"),
    ({"software": ""}, "'software'"),
])
def test_bad_numbers_fail(tmp_path, srcs, change, needle):
    problems = checks.check_calc(calc_dir(tmp_path, {**CALC_RESULT, **change}), srcs)
    assert any(needle in p for p in problems), problems


def test_calc_must_be_for_the_requested_ion(tmp_path, srcs):
    d = calc_dir(tmp_path)
    assert checks.check_calc(d, srcs, "Na+") == []
    assert "asked for 'K+'" in checks.check_calc(d, srcs, "K+")[0]


def test_calc_files_and_citations(tmp_path, srcs):
    d = calc_dir(tmp_path, **{"run_calc.py": None, "output.log": "", "explanation.md": "too short [7]"})
    problems = " ".join(checks.check_calc(d, srcs))
    assert "output.log" in problems and "words" in problems and "[7]" in problems
    (d / "result.json").write_text("{nope")
    assert "not valid JSON" in checks.check_calc(d, srcs)[0]


def test_cli_exit_codes(tmp_path, srcs, capsys):
    d = calc_dir(tmp_path)
    assert checks.main(["calc", str(d), "--references", srcs]) == 0
    (d / "result.json").unlink()
    assert checks.main(["calc", str(d), "--references", srcs]) == 1
    assert "FAILED" in capsys.readouterr().out
