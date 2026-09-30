"""Bridges a stage-graph pipeline (pipeline/run.py) evidence table into RunState, so the
EXISTING findings_writer_agent/builder_agent can write a report from it without the main agent's
free-form delegate_tasks research loop ever running. See ROADMAP.md's stage-graph-pipeline Pending
entry for why this exists and what it replaces (the evidence-gathering phase only, not the writer).
"""
import hashlib
from pathlib import Path

from utils.run_state import record_fetched_url


def seed_run_state_from_evidence(run_state, facets: list[dict], evidence: dict, sources_dir: Path) -> int:
    """For each kept (facet, url, quotes) triple: registers the url as fetched (copying its saved
    source content into the run's own workspace so a citation resolves to a real file, same as a
    normal fetch would) and adds an add_finding entry in the exact shape
    _build_findings_source_material already reads. depth=1 mirrors the Planner's own top-level
    delegate_tasks findings (RunState.add_finding: depth==1 = top-level, no top_level_task_name).
    Returns the number of findings added."""
    from tools.fs import _get_safe_path

    added = 0
    seen_urls = set()
    for f in facets:
        fid, name = f["id"], f["name"]
        for url, quotes in evidence.get(fid, {}).items():
            if not quotes:
                continue
            if url not in seen_urls:
                seen_urls.add(url)
                fname = f"src_{hashlib.sha1(url.encode()).hexdigest()[:8]}.md"
                src_path = sources_dir / fname
                if src_path.exists():
                    dst = _get_safe_path(fname)
                    if dst:
                        Path(dst).parent.mkdir(parents=True, exist_ok=True)
                        Path(dst).write_text(src_path.read_text())
                record_fetched_url(url, fname, title=name)
            run_state.add_finding(source_url=url, summary=" ".join(quotes), task_name=name, depth=1)
            added += 1
    return added
