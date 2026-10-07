"""Every tool the workflow invokes can actually be run.

Run: python test_tools_are_runnable.py

On 2026-10-06 an edit to connecteam_push.py replaced everything from a marker to
the end of the file, and the end of the file was

    if __name__ == "__main__":
        raise SystemExit(main())

So the script imported, defined main(), called nothing, printed nothing and
exited 0. GitHub reported the run as a SUCCESS, and the board was not touched.
Thirty-five test suites passed, because every one of them imports functions and
none of them starts a tool the way the workflow starts it.

That is the gap this file closes. It tests that the entry point exists and that
the module imports, which is exactly the class of fault the other suites are
structurally blind to.
"""
import ast
import io
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
WORKFLOWS = HERE / ".github" / "workflows"


def tools_the_workflows_run() -> set:
    """Every `python ... <name>.py` the workflow files invoke.

    Read out of the YAML rather than listed by hand, so a tool added to a
    workflow is covered without anyone remembering to add it here.
    """
    found = set()
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        text = io.open(wf, encoding="utf-8").read()
        for m in re.finditer(r"python\s+(?:-u\s+)?([A-Za-z0-9_]+\.py)", text):
            found.add(m.group(1))
    return found


def test_the_workflows_reference_real_files():
    missing = sorted(t for t in tools_the_workflows_run()
                     if not (HERE / t).exists())
    assert not missing, f"the workflows invoke files that do not exist: {missing}"
    print(f"OK: all {len(tools_the_workflows_run())} tool(s) named in the "
          f"workflows exist")


def test_every_tool_has_an_entry_point():
    """The exact fault of 2026-10-06. A module with no __main__ guard runs, prints
    nothing and exits 0, which reads as success everywhere that looks."""
    broken = []
    for tool in sorted(tools_the_workflows_run()):
        tree = ast.parse(io.open(HERE / tool, encoding="utf-8").read())
        has_guard = any(
            isinstance(node, ast.If)
            and any(isinstance(n, ast.Compare)
                    and isinstance(n.left, ast.Name)
                    and n.left.id == "__name__"
                    for n in ast.walk(node.test))
            for node in tree.body)
        has_main = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and n.name == "main" for n in tree.body)
        if not has_guard:
            broken.append(f"{tool} (no __main__ block)")
        elif not has_main:
            broken.append(f"{tool} (guard present but no main())")
    assert not broken, "tools that cannot be run: " + "; ".join(broken)
    print("OK: every tool the workflows run has a __main__ guard and a main()")


def test_every_tool_imports():
    """A tool that cannot import is as broken as one with no entry point.

    Imported IN-PROCESS rather than run as a subprocess. Spawning thirty-three
    interpreters that each load pandas took over two minutes, and a suite slow
    enough to get skipped protects nothing. Importing is safe precisely because
    every tool has the __main__ guard the test above insists on: nothing runs on
    import.
    """
    import importlib
    bad = []
    for tool in sorted(tools_the_workflows_run()):
        try:
            importlib.import_module(tool[:-3])
        except Exception as e:  # noqa: BLE001 - any import failure is the finding
            bad.append(f"{tool}: {type(e).__name__}: {e}")
    assert not bad, "tools that do not import: " + "; ".join(bad)
    print(f"OK: all {len(tools_the_workflows_run())} tools import cleanly")


def test_the_push_really_runs_as_a_command():
    """One genuine subprocess, for the tool that actually broke.

    The rest are covered statically and by import. This one is started the way
    the workflow starts it, because a structural check is not the same as
    watching it run, and this is the file where the fault happened.

    Deliberately run with no credentials: it must fail LOUDLY. Exiting 0 with an
    empty stdout is the signature of the bug.
    """
    env = {k: v for k, v in os.environ.items()
           if k in ("SYSTEMROOT", "PATH", "PATHEXT", "TEMP", "TMP")}
    p = subprocess.run([sys.executable, "connecteam_push.py", "--city", "Austin",
                        "--test"], capture_output=True, text=True, timeout=180,
                       cwd=str(HERE), env=env)
    out = (p.stdout or "") + (p.stderr or "")
    assert "Traceback" not in out, out[-400:]
    assert out.strip(), ("connecteam_push.py printed NOTHING -- the 2026-10-06 "
                         "fault exactly: no __main__ guard, so the module defines "
                         "main() and calls nothing.")
    assert p.returncode != 0, f"expected a clean failure, got 0 with: {out[:200]}"
    first = out.strip().splitlines()[0][:60]
    print(f"OK: the push starts and says why it cannot run ({first}...)")


if __name__ == "__main__":
    test_the_workflows_reference_real_files()
    test_every_tool_has_an_entry_point()
    test_every_tool_imports()
    test_the_push_really_runs_as_a_command()
    print("")
    print("ALL TOOL-RUNNABILITY TESTS PASSED")
