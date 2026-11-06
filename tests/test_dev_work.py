"""ops/dev-work.py: the 5:30 PM dev session starts only when there is something to do (owner 2026-11-06: no more
"nothing on the roadmap — needs you" notices)."""
import contextlib
import importlib.util
import io
import os
import subprocess
from datetime import date
from unittest import SkipTest

from base import BotTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "ops", "dev-work.py")

ROADMAP = """# Charles roadmap

## Goal
1. **todo — Not a task: this is the Goal section.**

## Tasks
1. **done (2026-10-16, see Log) — Monthly report shows the plan.** reports.py's monthly report adds:
   2. **todo — a wrapped line that only looks like a task.**
5. **done 2026-11-03 (owner OK) — plan_vs_spy.py, used by charles-status — charles-status: same moment.**
4. **needs owner (not before 2027-04-15) — Six-month check.** Write docs/six-month-check.md.

## Log
- 2026-11-03 — dev: task 2 — done.
"""


def load():
    if not os.path.exists(PATH):
        raise SkipTest("ops/ isn't in a public copy")
    spec = importlib.util.spec_from_file_location("dev_work", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Roadmap(BotTestCase):
    def setUp(self):
        super().setUp()
        self.dw = load()

    def test_a_finished_roadmap_has_nothing_until_the_dated_task_is_due(self):
        self.assertEqual(self.dw.open_tasks(ROADMAP, date(2026, 11, 6)), [])
        self.assertEqual(self.dw.open_tasks(ROADMAP, date(2027, 4, 14)), [])
        got = self.dw.open_tasks(ROADMAP, date(2027, 4, 15))
        self.assertEqual(len(got), 1)
        self.assertIn("Six-month check", got[0])

    def test_a_written_proposal_waits_for_the_owner(self):
        text = ROADMAP.replace("needs owner (not before 2027-04-15) — Six-month check.",
                               "needs owner (not before 2027-04-15) — proposal written 2027-04-15 — Six-month check.")
        self.assertEqual(self.dw.open_tasks(text, date(2027, 5, 1)), [])
        text = ROADMAP.replace("needs owner (not before 2027-04-15)", "needs owner — proposal written 2026-11-02")
        self.assertEqual(self.dw.open_tasks(text, date(2026, 11, 6)), [])

    def test_todo_doing_and_unreadable_tasks_are_work(self):
        for line in ("6. **todo — Grade the crypto vetoes.**", "6. **doing — Grade the crypto vetoes.**",
                     "6. todo - plain text task", "6. **Grade the crypto vetoes.**"):
            text = ROADMAP.replace("\n## Log", line + "\n\n## Log")
            self.assertEqual(self.dw.open_tasks(text, date(2026, 11, 6)), [line], line)

    def test_weekday_tasks_wait_for_their_day(self):
        text = ROADMAP.replace("\n## Log", "6. **todo (Mondays) — Weekly look at the picks.**\n\n## Log")
        self.assertEqual(self.dw.open_tasks(text, date(2026, 11, 6)), [])          # a Friday
        self.assertEqual(len(self.dw.open_tasks(text, date(2026, 11, 9))), 1)      # a Monday

    def test_the_real_roadmap_parses(self):
        with open(os.path.join(ROOT, "ops", "roadmap.md")) as f:
            text = f.read()
        self.assertIn("## Tasks", text)
        self.assertIsInstance(self.dw.open_tasks(text, date(2026, 11, 6)), list)


class Uncommitted(BotTestCase):
    def setUp(self):
        super().setUp()
        self.dw = load()
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "ops"))
        for name, text in (("strategy.json", "{}\n"), ("a.py", "x = 1\n"), ("ops/roadmap.md", ROADMAP)):
            with open(os.path.join(self.repo, name), "w") as f:
                f.write(text)
        git = ["git", "-C", self.repo, "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(git[:3] + ["init", "-q"], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "start"], check=True)

    def main(self, day):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.dw.main(["dev-work.py", day], self.repo)

    def write(self, name, text):
        with open(os.path.join(self.repo, name), "w") as f:
            f.write(text)

    def test_settings_saves_are_not_work_but_code_left_behind_is(self):
        self.assertEqual(self.dw.unfinished(self.repo), [])
        self.assertEqual(self.main("2026-11-06"), self.dw.NOTHING)
        self.write("strategy.json", '{"x": 1}\n')
        self.assertEqual(self.main("2026-11-06"), self.dw.NOTHING)
        self.write("a.py", "x = 2\n")
        self.assertEqual(self.dw.unfinished(self.repo), ["a.py"])
        self.assertEqual(self.main("2026-11-06"), 0)

    def test_new_files_count_and_a_due_task_counts(self):
        self.write("b.py", "y = 1\n")
        self.assertEqual(self.dw.unfinished(self.repo), ["b.py"])
        os.remove(os.path.join(self.repo, "b.py"))
        self.assertEqual(self.main("2027-04-15"), 0)
