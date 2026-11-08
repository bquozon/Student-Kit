"""Is a URL alive? Used by the suggest form (5 s) and the weekly link check (10 s).

Only public addresses are fetched, redirects included, so a suggested URL can't make the server call into the VPS's
private network. Only a yes/no comes back; nothing from the page is shown or stored."""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit

UA = "Mozilla/5.0 (compatible; SwissStudentKit-linkcheck/1.0)"


def public(url: str) -> bool:
    """http(s) URL whose host resolves only to public IPs.
    shortcut: DNS is resolved again by urllib (rebinding window); fine while only a bool is returned."""
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except (OSError, UnicodeError):
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0].split("%")[0]).is_global for i in infos)


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not public(newurl):
            raise urllib.error.URLError("redirect to a non-public address")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_PublicRedirects)


def reachable(url: str, timeout: float) -> bool:
    """HEAD, then GET. A site that answers 401/403/429 is up (it just dislikes bots); 404, 410, 5xx or no answer
    count as a failure."""
    if not public(url):
        return False
    for method in ("HEAD", "GET"):
        try:
            with _opener.open(urllib.request.Request(url, method=method, headers={"User-Agent": UA}), timeout=timeout):
                return True
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 429):
                return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
    return False
