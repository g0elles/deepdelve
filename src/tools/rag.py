import time

from agent_framework import tool
from tools.core import with_quota
from utils import rag_cache


@tool
@with_quota
def search_verified_findings(query: str) -> str:
    """Check whether this topic was already researched and verified in a past run, before
    searching the live web. A hit already includes verified analysis - cite its source URL
    directly in your findings, you do NOT need to delegate it to an Analyzer again. If nothing
    relevant comes back, or a result looks stale/off-topic, proceed with web_search as normal."""
    import config as app_config

    settings = app_config.cfg.get("settings", {}).get("rag_cache", {})
    results = rag_cache.lookup(
        query,
        top_k=settings.get("top_k", 3),
        min_similarity=settings.get("min_similarity", 0.75),
        max_age_days=settings.get("max_age_days", 7),
    )
    if not results:
        return "No cached findings found for this query."

    lines = []
    for r in results:
        age_days = (time.time() - r["timestamp"]) / 86400
        lines.append(
            f"### {r['source_url']}\n"
            f"- Verified {age_days:.1f} days ago in a prior run (similarity {r['similarity']:.2f}) - "
            f"confirm this still looks current before relying on it for a fast-changing fact.\n"
            f"{r['summary']}"
        )
    return "\n\n".join(lines)
