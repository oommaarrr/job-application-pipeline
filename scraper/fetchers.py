"""
Source fetchers.

StepStone  — plain HTTP. Serves full server-rendered results with stable
             data-at="job-item-*" hooks. Behind Akamai, which stalls requests
             that don't carry a browser-navigation header set (see __init__).
Imported   — Indeed and anything else that blocks scripted HTTP is collected by
             the Chrome extension in extension/ and read from inbox/*.json.
             No Playwright, no headless browser, no automation driver.

Every fetcher yields the same Job shape, so the scoring / gating / ranking
pipeline is source-agnostic.
"""

from __future__ import annotations

import json
from jobkey import job_key
import pathlib
import re
import random
import time
import urllib.parse
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

import config


@dataclass
class Job:
    title: str
    location: str
    url: str
    company: str = ""
    source: str = ""
    query: str = ""
    description: str = ""
    # filled in later by the pipeline
    score: float = 0.0
    score_detail: object = None
    language: object = None
    experience: object = None
    extras: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return job_key(self.url)


def _sleep():
    time.sleep(random.uniform(*config.REQUEST_DELAY))


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


# LinkedIn appends UI badge text to the card's innerText.
_BADGE_RX = re.compile(
    r"\s*(with verification|easy apply|promoted|viewed|new|verified|"
    r"be an early applicant|actively reviewing applicants|reposted)\s*$",
    re.IGNORECASE,
)


def _dedupe_repeat(s: str) -> str:
    """
    "AI Engineer AI Engineer with verification" -> "AI Engineer".

    LinkedIn renders each title twice (visible + a screen-reader copy) and then
    appends badge text, so innerText arrives as "A A <badge>". Strip the badges
    first, then collapse the largest immediately-repeated prefix. Working
    largest-first means a genuine title is never truncated to a coincidental
    short repeat.
    """
    s = (s or "").strip()
    for _ in range(4):                      # badges can stack
        stripped = _BADGE_RX.sub("", s).strip()
        if stripped == s:
            break
        s = stripped

    n = len(s)
    for h in range(n // 2, 3, -1):          # min 4 chars, avoids silly matches
        for sep in (" ", ""):
            j = h + len(sep)
            if j + h > n:
                continue
            if s[:h] == s[j:j + h]:
                return s[:h].strip()
    return s


def _tidy_location(s: str) -> str:
    """
    "Berlin, Berlin, Germany (On-site)" -> "Berlin, Germany (On-site)".

    LinkedIn emits city, region, country — and for city-states the first two are
    identical. Collapse only *consecutive* duplicate parts; never drop real ones.
    """
    s = (s or "").strip()
    if not s:
        return ""
    parts, out = [p.strip() for p in s.split(",")], []
    for p in parts:
        if not out or p.lower() != out[-1].lower():
            out.append(p)
    return ", ".join(x for x in out if x)


# ============================================================== StepStone
class StepStone:
    name = "StepStone"
    network = True       # detail fetches hit the site, so they must be paced
    BASE = "https://www.stepstone.de"

    # Akamai does not ban, it stalls. Once it decides you are a bot it holds the
    # socket open until read timeout, then lets you back in a minute or two
    # later. The old code treated the first stall as permanent and abandoned the
    # whole source, so one bad page cost eleven untried queries and every
    # description. These two constants are the recovery instead.
    COOLDOWN = 90        # seconds to wait out a stall before trying again
    MAX_COOLDOWNS = 2    # after this many, it really is not letting us in

    def __init__(self):
        self.s = requests.Session()
        # StepStone sits behind Akamai bot manager. Search pages tolerate a
        # minimal header set, but DETAIL pages silently stall the connection
        # (read timeout, no status) unless the request looks like a real
        # top-level browser navigation. The Sec-Fetch-* set plus a same-origin
        # Referer is what flips it from "hang" to "200 in ~1s".
        # Verified: with these headers both HTTP/1.1 and HTTP/2 work, so plain
        # requests is fine — the HTTP version was never the issue.
        # Note: no "br" in Accept-Encoding — requests can't decode brotli
        # without the extra package, and asking for it yields an empty body.
        self.s.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Connection": "keep-alive",
        })
        self._last_search_url = self.BASE + "/"
        self.blocked = False
        self._cooldowns = 0

    def search_url(self, query: str, page: int) -> str:
        slug = urllib.parse.quote(query.strip().lower().replace(" ", "-"))
        loc = urllib.parse.quote(config.LOCATION.lower())
        url = f"{self.BASE}/jobs/{slug}/in-{loc}?radius={config.RADIUS_KM}"
        if page > 1:
            url += f"&page={page}"
        return url

    def _get(self, url: str, label: str):
        """
        One request, with the backoff Akamai actually requires.

        Short retries first, because most stalls are momentary. If those all
        fail we are being deliberately held, and the only thing that works is
        waiting it out: sleep COOLDOWN, then try once more. Only after
        MAX_COOLDOWNS does the source count as genuinely blocked.

        Returns a Response, or None when the request could not be completed.
        """
        for attempt in range(3):
            try:
                r = self.s.get(url, timeout=20,
                               headers={"Referer": self._last_search_url,
                                        "Sec-Fetch-Site": "same-origin"})
                if r.status_code in (403, 429, 503):
                    print(f"      ! HTTP {r.status_code} on {label}, "
                          f"waiting {self.COOLDOWN}s")
                    break
                self._cooldowns = 0      # a good response clears the streak
                return r
            except requests.RequestException as e:
                if attempt < 2:
                    wait = 4 * (attempt + 1)
                    print(f"      ! {type(e).__name__} on {label}, "
                          f"retrying in {wait}s ({attempt + 1}/3)")
                    time.sleep(wait)

        # Short retries exhausted. Wait out the stall rather than abandoning.
        self._cooldowns += 1
        if self._cooldowns > self.MAX_COOLDOWNS:
            print(f"      ! StepStone still stalling after {self.MAX_COOLDOWNS} "
                  f"cooldowns — treating as BLOCKED, not empty")
            self.blocked = True
            return None

        print(f"      ⏸ StepStone is stalling us. Cooling down {self.COOLDOWN}s "
              f"({self._cooldowns}/{self.MAX_COOLDOWNS}), then resuming…")
        time.sleep(self.COOLDOWN)
        try:
            r = self.s.get(url, timeout=20,
                           headers={"Referer": self._last_search_url,
                                    "Sec-Fetch-Site": "same-origin"})
            if r.status_code == 200:
                print("      ▶ back in, continuing")
                self._cooldowns = 0
                return r
        except requests.RequestException:
            pass
        return None

    def fetch_page(self, query: str, page: int) -> list[Job]:
        url = self.search_url(query, page)
        r = self._get(url, f"page {page}")
        if r is None:
            return []
        if r.status_code != 200:
            print(f"      ! StepStone HTTP {r.status_code}")
            return []
        self._last_search_url = url  # detail fetches cite the search page

        soup = BeautifulSoup(r.text, "html.parser")
        jobs: list[Job] = []
        for card in soup.select('[data-at="job-item"]'):
            a = card.select_one('a[data-at="job-item-title"]') or card.select_one("a[href*='/stellenangebote--']")
            if not a or not a.get("href"):
                continue
            href = a["href"]
            jobs.append(Job(
                title=_text(a),
                location=_text(card.select_one('[data-at="job-item-location"]')),
                company=_text(card.select_one('[data-at="job-item-company-name"]')),
                url=href if href.startswith("http") else self.BASE + href,
                source=self.name,
                query=query,
                extras={"remote": bool(card.select_one('[data-at="job-item-work-from-home"]'))},
            ))
        return jobs

    def fetch_description(self, job: Job) -> str:
        # Goes through the same backoff as search. Previously this was a single
        # bare request with no retry, so the moment Akamai started stalling,
        # every remaining description failed in turn and each failure kept the
        # stall alive by hammering straight through it.
        # The Referer must be the search page we came from, or Akamai stalls.
        r = self._get(job.url, f"detail {job.title[:30]}")
        if r is None:
            return ""
        if r.status_code != 200:
            print(f"      ! detail HTTP {r.status_code} {job.title[:40]}")
            return ""

        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        for sel in ('[data-at="job-ad-content"]', '[class*="job-ad-display"]',
                    "article", "main"):
            node = soup.select_one(sel)
            if node and len(node.get_text(strip=True)) > 300:
                return node.get_text("\n", strip=True)
        return soup.get_text("\n", strip=True)



# ======================================================== Imported (extension)
class Imported:
    """
    Indeed (and anything else that blocks scripted HTTP) is collected by the
    Chrome extension in extension/ and dropped here as JSON.

    No Playwright, no headless browser, no automation fingerprint — the
    extension runs inside your own logged-in Chrome tab, so from the site's
    perspective it is simply you reading the page.

    The extension is a dumb collector: it captures title / location / company /
    url / description and nothing else. Every decision — scoring, the German
    gate, years extraction, ranking — stays in Python, so there is exactly one
    implementation of each rule.

    Drop the exported .json files into inbox/ and re-run.
    """

    name = "Imported"
    network = False      # descriptions were captured in the browser already
    INBOX = pathlib.Path(__file__).parent / "inbox"

    def __init__(self):
        self.INBOX.mkdir(exist_ok=True)
        self.files = sorted(self.INBOX.glob("*.json"))
        self._jobs: list[Job] | None = None

    @property
    def available(self) -> bool:
        return bool(self.files)

    @property
    def reason(self) -> str:
        return (f"no files in {self.INBOX.name}/ — collect with the Chrome "
                f"extension, then drop the export here")

    def _load(self) -> list[Job]:
        if self._jobs is not None:
            return self._jobs
        jobs: list[Job] = []
        for path in self.files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                print(f"      ! skipping {path.name}: {e}")
                continue
            records = payload.get("jobs", payload) if isinstance(payload, dict) else payload
            for rec in records:
                url = (rec.get("url") or "").strip()
                title = (rec.get("title") or "").strip()
                if not url or not title:
                    continue
                jobs.append(Job(
                    # Only the TITLE suffers the doubled-render artifact.
                    # "Berlin, Berlin, Germany" is real data (city, region,
                    # country), so it gets tidied, never halved.
                    title=_dedupe_repeat(title),
                    location=_tidy_location(rec.get("location") or ""),
                    company=(rec.get("company") or "").strip(),
                    url=url,
                    source=rec.get("source") or "Imported",
                    query=rec.get("query") or "",
                    description=rec.get("description") or "",
                ))
        self._jobs = jobs
        return jobs

    def fetch_page(self, query: str, page: int) -> list[Job]:
        """
        Imported jobs were already collected by the extension, so there is
        nothing to page through. Everything is returned on page 1 of the first
        query and the runner's drift logic simply doesn't apply here.
        """
        if page > 1 or query != config.QUERIES[0]:
            return []
        jobs = self._load()
        print(f"      loaded {len(jobs)} jobs from {len(self.files)} export"
              f"{'s' if len(self.files) != 1 else ''}")
        return jobs

    def fetch_description(self, job: Job) -> str:
        # Already captured in the browser by the extension.
        return job.description


def build_sources() -> list:
    sources: list = [StepStone()]
    imported = Imported()
    if imported.available:
        sources.append(imported)
    else:
        print(f"  ⓘ Imported source empty — {imported.reason}")
    return sources
