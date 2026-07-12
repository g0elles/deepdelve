try:
    from markitdown import MarkItDown
    _markitdown_available = True
    _markitdown = MarkItDown()
except ImportError:
    import logging
    logging.warning("markitdown not installed. Markdown conversion will be limited.")
    _markitdown_available = False

def convert_to_markdown(url_or_filepath: str) -> str:
    """
    Attempts to fetch and convert a URL or raw file to markdown using markitdown.
    Returns None if markitdown is unavailable or fails, allowing graceful fallback.
    """
    if not _markitdown_available:
        return None

    try:
        # Pass the URL directly to markitdown
        result = _markitdown.convert(url_or_filepath)
        if result and result.text_content:
            return result.text_content
        return None
    except Exception:
        return None
