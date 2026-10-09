import copy
from http.cookiejar import CookieJar


def share_cookies_across_subdomains(jar: CookieJar, base_domain: str) -> None:
    """Rewrite cookies scoped to one subdomain (e.g. login.example.com) so they
    are scoped to .example.com and get sent to every subdomain of the base domain.

    Cookies set without a Domain attribute are host-only, so the cookie jar would
    never send them to dashboard.example.com after logging in on login.example.com.
    """
    shared_domain = "." + base_domain
    for cookie in list(jar):
        bare_domain = cookie.domain.lstrip(".")
        if bare_domain != base_domain and not bare_domain.endswith("." + base_domain):
            continue
        if cookie.domain == shared_domain:
            continue

        jar.clear(cookie.domain, cookie.path, cookie.name)
        shared = copy.copy(cookie)
        shared.domain = shared_domain
        shared.domain_specified = True
        shared.domain_initial_dot = True
        jar.set_cookie(shared)
