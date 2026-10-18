"""Shared SSRF host check (originally tools/web.py's own, 2026-08-02 audit). Every URL this
project fetches is attacker-influenced -- a raw web_search/fetch_url_to_workspace argument the
model produced from (possibly prompt-injected) page content, or a link resolved out of a page's
own content. Pulled out to its own module so utils/browser_fetch.py's headless-Chromium fallback
can apply the SAME check Playwright-side (page.route) that tools/web.py already applies httpx-side
(event_hooks) -- without this, a bot-wall page's client-side JS/meta-refresh redirect during the
headless retry reached the network completely unchecked, since Chromium's navigation is a separate
stack httpx's event hook never sees (2026-10-17 security audit)."""
import ipaddress
import socket


def is_unsafe_fetch_host(hostname: str) -> bool:
    """True if `hostname` IS, or resolves to, a loopback/link-local/private/reserved/multicast
    address. Resolves the hostname first (via socket.getaddrinfo) rather than pattern-matching
    the string, so a DNS name that merely POINTS at an internal address (not just a bare IP
    literal like 169.254.169.254) is caught the same way -- a bare string check alone would miss
    that. A DNS failure is not an SSRF risk (nothing to reach) -- let the fetch itself surface
    that error normally instead of blocking it here."""
    if not hostname:
        return True
    try:
        ipaddress.ip_address(hostname)
        addrs = {hostname}
    except ValueError:
        try:
            addrs = {info[4][0] for info in socket.getaddrinfo(hostname, None)}
        except socket.gaierror:
            return False
    for addr in addrs:
        ip = ipaddress.ip_address(addr)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False
