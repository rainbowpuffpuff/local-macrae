"""Engine tests that need no Docker, Harbor or accounts: parsing, dependencies, templates, a script-only run."""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_runner import flows  # noqa: E402


def analyze(text):
    return flows.analyze(flows.load_flow(textwrap.dedent(text))[0])


def test_dependencies_are_inferred_from_templates():
    steps, deps, problems = analyze("""
        name: t
        steps:
          - {id: a, run: echo 1}
          - {id: b, run: "echo {{ a.output }}"}
          - {id: c, when: "b.ok", run: echo 3}
          - {id: d, needs: [a], run: echo 4}
    """)
    assert problems == []
    assert deps == {"a": set(), "b": {"a"}, "c": {"b"}, "d": {"a"}}
    assert flows.levels(deps) == [["a"], ["b", "d"], ["c"]]


def test_problems_are_reported():
    _, _, problems = analyze("""
        name: t
        steps:
          - {id: a, run: "echo {{ b.output }}"}
          - {id: b, run: "echo {{ a.output }}"}
          - {id: bad-id, run: x}
          - {id: c, instrucion: typo}
    """)
    text = " ".join(problems)
    assert "cycle" in text
    assert "python-style name" in text
    assert "unknown keys" in text


def test_render_keeps_types_for_a_single_expression():
    ctx = {"x": flows.AttrDict(output=[1, 2]), "vars": flows.AttrDict(n=3)}
    assert flows.render("{{ x.output }}", ctx) == [1, 2]
    assert flows.render("n={{ vars.n }}", ctx) == "n=3"
    assert flows.render({"k": ["{{ vars.n * 2 }}"]}, ctx) == {"k": [6]}


def test_script_flow_runs_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        flow = Path(tmp) / "f.yaml"
        flow.write_text(textwrap.dedent("""
            name: script-only
            vars: {letters: [a, b, c]}
            steps:
              - id: plan
                run: python3 -c 'import json; print(json.dumps({{ vars.letters }}))'
                outputs: json
              - id: each
                foreach: "{{ plan.output }}"
                run: echo "item-{{ item }}"
                outputs: text
              - id: boom
                run: exit 3
                retry: 1
              - id: skipped
                needs: [boom]
                run: echo never
              - id: rescue
                needs: [boom]
                always: true
                when: "{{ not boom.ok }}"
                run: echo "rescued after {{ boom.attempts }}"
                outputs: text
        """))
        env = dict(os.environ, AGENT_RUNNER_HOME=str(Path(tmp) / "home"), PYTHONPATH=str(ROOT))
        r = subprocess.run([sys.executable, "-m", "agent_runner", "flow", "run", str(flow)], env=env,
                           capture_output=True, text=True, timeout=120)
        assert r.stdout.strip().endswith("failed"), r.stdout + r.stderr  # boom fails on purpose
        state = json.loads(next((Path(tmp) / "home" / "runs").glob("*/state.json")).read_text())
        st = state["steps"]
        assert [st[f"each[{i}]"]["output"] for i in range(3)] == ["item-a", "item-b", "item-c"]
        assert st["boom"]["status"] == "failed" and st["boom"]["attempt"] == 2
        assert st["skipped"]["status"] == "skipped"
        assert st["rescue"]["output"] == "rescued after 2"


def test_vars_named_like_dict_methods_are_flagged():
    _, _, problems = analyze("""
        name: t
        vars: {items: [1], fine: 2}
        steps:
          - {id: a, run: echo 1}
    """)
    assert any("dict method" in p and "items" in p for p in problems)
