"""Quarantine restore (engine/artifact_salvage.py): snapshots in numeric order beyond attempt 9, and the restored draft is the latest one with substance, not a later empty template."""
import os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import config
from engine import artifact_salvage as sv
from tools import fs

config.load_config()
tmp = tempfile.mkdtemp()
config.cfg["settings"]["workspace"] = {"type": "disk", "dir": tmp}
path = os.path.join(tmp, "final_report.md")
REAL = "# Report\n" + "A real finding about base editing and its clinical trials in liver disease. " * 60
EMPTY = "# Final Report\n## Abstract\nNot covered by this run's research.\n"


def snap(n, text):
    with open(f"{path}.rejected_attempt_{n}", "w", encoding="utf-8") as f:
        f.write(text)


# the real failed run: real drafts at 4 and 8, empty templates at 7, 9, 10 (lexicographic order put 9 last and range(1, 10) never saw 10)
for n, t in ((4, REAL + " four"), (7, EMPTY), (8, REAL + " eight"), (9, EMPTY), (10, "# A\n")):
    snap(n, t)
names = [os.path.basename(p) for p in sv._rejected_snapshots(path)]
assert names == [f"final_report.md.rejected_attempt_{n}" for n in (4, 7, 8, 9, 10)], names
assert sv._restore_quarantined_draft("final_report.md", "not_grounded")
out = open(path, encoding="utf-8").read()
assert "QUARANTINED DRAFT (restored)" in out and out.rstrip().endswith("eight") and "Not covered" not in out, out[-120:]
# content identity looks at the newest snapshot (10), not attempt 9
os.remove(path)
assert sv._content_unchanged_since_last_quarantine("final_report.md", "# A\n") and not sv._content_unchanged_since_last_quarantine("final_report.md", EMPTY)
# nothing substantive anywhere: still restores the latest (never worse than before)
for p in sv._rejected_snapshots(path):
    os.remove(p)
snap(2, EMPTY)
snap(3, "# A\n")
assert sv._restore_quarantined_draft("final_report.md", "x") and "# A" in open(path, encoding="utf-8").read()
assert fs  # imported so the workspace helpers are configured
print("test_artifact_restore: ok")

if __name__ == "__main__":
    pass
