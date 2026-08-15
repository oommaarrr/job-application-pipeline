/*
 * Job Collector — content script.
 *
 * This is a DUMB COLLECTOR on purpose. It captures title / location / company /
 * url / description and nothing else. Every decision — relevance scoring, the
 * German language gate, years-of-experience extraction, ranking — lives in the
 * Python side, so there is exactly one implementation of each rule.
 *
 * It runs inside the tab you are already looking at, using your own session.
 * There is no automation driver and no headless browser, which is why it works
 * on sites that reject scripted HTTP.
 */

(() => {
  "use strict";
  if (window.__jobCollectorLoaded) return;
  window.__jobCollectorLoaded = true;

  const txt = (el) => (el ? el.innerText.replace(/\s+/g, " ").trim() : "");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const jitter = (a, b) => a + Math.random() * (b - a);

  /*
   * Cancellation.
   *
   * Stopping used to mean clearing the auto-run counter in storage, which did
   * nothing to the loop already walking 25 cards: it kept clicking and fetching
   * to the end, so Stop appeared to do nothing. These two flags are what an
   * in-flight run actually checks, and they are module-level so a message
   * handler can flip them while the loop is mid-await.
   */
  let cancelled = false;   // set by the stop message, read between every job
  let collecting = false;  // true while a page is being walked, for the popup

  // LinkedIn appends badge text after the (already doubled) title.
  const BADGE =
    /\s*(with verification|easy apply|promoted|viewed|new|verified|be an early applicant|actively reviewing applicants|reposted)\s*$/i;

  /**
   * "AI Engineer AI Engineer with verification" -> "AI Engineer".
   * Strips badges first, then collapses the largest immediately-repeated
   * prefix. Largest-first so a real title is never cut to a short coincidence.
   */
  function dedupeRepeat(s) {
    s = (s || "").trim();
    for (let i = 0; i < 4; i++) {
      const stripped = s.replace(BADGE, "").trim();
      if (stripped === s) break;
      s = stripped;
    }
    const n = s.length;
    for (let h = Math.floor(n / 2); h > 3; h--) {
      for (const sep of [" ", ""]) {
        const j = h + sep.length;
        if (j + h > n) continue;
        if (s.slice(0, h) === s.slice(j, j + h)) return s.slice(0, h).trim();
      }
    }
    return s;
  }

  // ------------------------------------------------------------ site adapters
  const SITES = {
    indeed: {
      test: () => /indeed\.com/.test(location.hostname),
      name: "Indeed",
      cards: () =>
        document.querySelectorAll(
          "div.job_seen_beacon, [data-testid='slider_item'], td.resultContent"
        ),
      parse(card) {
        const a =
          card.querySelector("h2.jobTitle a") ||
          card.querySelector("a.jcs-JobTitle") ||
          card.querySelector("a[data-jk]");
        if (!a) return null;
        const jk = a.getAttribute("data-jk") || "";
        let href = a.getAttribute("href") || "";
        if (href.startsWith("/")) href = location.origin + href;
        if (!href && jk) href = `${location.origin}/viewjob?jk=${jk}`;
        return {
          title: txt(card.querySelector("h2.jobTitle")) || txt(a),
          company: txt(card.querySelector("[data-testid='company-name']")),
          location: txt(card.querySelector("[data-testid='text-location']")),
          url: href,
          _el: a,
        };
      },
      // Clicking a card loads the description into the right-hand pane.
      descPane: () =>
        document.querySelector("#jobDescriptionText") ||
        document.querySelector("[id*='jobDescriptionText']"),
      next: () =>
        document.querySelector("a[data-testid='pagination-page-next']") ||
        document.querySelector("a[aria-label='Next Page']") ||
        document.querySelector("a[aria-label='Next']"),
    },

    // LinkedIn changes its markup often, so every selector has fallbacks and
    // the parser tolerates any single one going missing.
    linkedin: {
      test: () => /linkedin\.com/.test(location.hostname),
      name: "LinkedIn",
      slow: true, // LinkedIn is the most automation-sensitive of the three
      cards: () =>
        document.querySelectorAll(
          "div.job-card-container, li.jobs-search-results__list-item, " +
          "li[data-occludable-job-id], div[data-job-id]"
        ),
      parse(card) {
        const a =
          card.querySelector("a.job-card-container__link") ||
          card.querySelector("a.job-card-list__title") ||
          card.querySelector("a[href*='/jobs/view/']");
        if (!a) return null;
        let href = a.getAttribute("href") || "";
        if (href.startsWith("/")) href = location.origin + href;
        href = href.split("?")[0];
        // LinkedIn renders the title twice — once visible, once in an
        // aria-hidden / screen-reader span — so innerText comes back doubled
        // ("AI Engineer AI Engineer"). Collapse an exact repeat, with or
        // without a separator.
        const rawTitle =
          txt(card.querySelector(".job-card-list__title--link")) ||
          txt(card.querySelector(".job-card-list__title")) ||
          txt(a);
        const title = dedupeRepeat(rawTitle);
        return {
          title,
          company:
            txt(card.querySelector(".job-card-container__primary-description")) ||
            txt(card.querySelector(".artdeco-entity-lockup__subtitle")) ||
            txt(card.querySelector("[class*='subtitle']")),
          location:
            txt(card.querySelector(".job-card-container__metadata-item")) ||
            txt(card.querySelector("[class*='metadata-item']")) ||
            txt(card.querySelector(".artdeco-entity-lockup__caption")),
          url: href,
          _el: a,
        };
      },
      descPane: () =>
        document.querySelector("#job-details") ||
        document.querySelector(".jobs-description__content") ||
        document.querySelector(".jobs-box__html-content"),
      next: () =>
        document.querySelector("button[aria-label='View next page']") ||
        document.querySelector(".jobs-search-pagination__button--next"),
    },

    stepstone: {
      test: () => /stepstone\.de/.test(location.hostname),
      name: "StepStone",
      cards: () => document.querySelectorAll("[data-at='job-item']"),
      parse(card) {
        const a =
          card.querySelector("a[data-at='job-item-title']") ||
          card.querySelector("a[href*='/stellenangebote--']");
        if (!a) return null;
        let href = a.getAttribute("href") || "";
        if (href.startsWith("/")) href = location.origin + href;
        return {
          title: txt(a),
          company: txt(card.querySelector("[data-at='job-item-company-name']")),
          location: txt(card.querySelector("[data-at='job-item-location']")),
          url: href,
          _el: a,
        };
      },
      descPane: () => document.querySelector("[data-at='job-ad-content']"),
      /*
       * StepStone opens a job by navigating, not by filling a side pane, so the
       * click-and-read-the-pane approach would destroy this content script on
       * every single job. Instead we fetch the detail page from inside the tab.
       *
       * This is the whole reason the extension beats the Python scraper here.
       * The same fetch from a script gets stalled by Akamai until it times out;
       * issued by the page you are already sitting on it is an ordinary
       * same-origin request carrying your real session, and returns in about
       * half a second.
       */
      fetchDesc: async (url) => {
        const res = await fetch(url, { credentials: "include" });
        if (!res.ok) return "";
        const doc = new DOMParser().parseFromString(await res.text(), "text/html");
        doc.querySelectorAll("script,style,noscript,svg").forEach((n) => n.remove());
        for (const sel of ["[data-at='job-ad-content']", "[class*='job-ad-display']",
                           "article", "main"]) {
          const node = doc.querySelector(sel);
          const text = node && node.innerText ? node.innerText.trim() : "";
          if (text.length > 300) return text.replace(/\n{3,}/g, "\n\n");
        }
        return "";
      },
      next: () =>
        document.querySelector("a[aria-label='Nächste']") ||
        document.querySelector("[data-at='pagination-next']"),
    },
  };

  const site = Object.values(SITES).find((s) => s.test());

  // ------------------------------------------------------------ storage
  async function saveJobs(newJobs) {
    const { jobs = {} } = await chrome.storage.local.get("jobs");
    let added = 0;
    for (const j of newJobs) {
      const key = (j.url || "").split("?")[0].toLowerCase();
      if (!key) continue;
      if (!jobs[key]) added++;
      // Never let a later, description-less sighting clobber a captured one.
      jobs[key] = { ...(jobs[key] || {}), ...j, description: j.description || jobs[key]?.description || "" };
    }
    await chrome.storage.local.set({ jobs });
    return { added, total: Object.keys(jobs).length };
  }

  /*
   * Hand what we just read to the local pipeline.
   *
   * The whole page is sent, not only the rows that were new to chrome.storage:
   * the bridge dedupes by URL anyway, and a second sighting is how a card that
   * was captured without its description gets one. Failure is silent by design
   * — the jobs are already in storage, so a missing bridge costs nothing but
   * the automatic rerank.
   */
  function pushToBridge(records) {
    if (!records.length) return;
    chrome.runtime
      .sendMessage({ type: "bridge:push", jobs: records })
      .catch(() => {});
  }

  /**
   * Which job is the user actually looking at right now.
   *
   * On a search page LinkedIn keeps the list URL and moves the opened job into
   * ?currentJobId, so reading location.href alone would mark the wrong thing.
   */
  function currentJob() {
    const href = location.href;
    if (/linkedin\.com/.test(location.hostname)) {
      const direct = location.pathname.match(/\/jobs\/view\/(\d+)/);
      const id = direct?.[1] || new URLSearchParams(location.search).get("currentJobId");
      if (!id) return null;
      const card =
        document.querySelector(".jobs-unified-top-card__job-title, .job-details-jobs-unified-top-card__job-title") ||
        document.querySelector(`[data-job-id="${id}"] a`);
      return { url: `https://www.linkedin.com/jobs/view/${id}/`, title: dedupeRepeat(txt(card)) };
    }
    if (/indeed\.com/.test(location.hostname)) {
      const jk = new URLSearchParams(location.search).get("vjk") ||
                 new URLSearchParams(location.search).get("jk");
      const url = jk ? `${location.origin}/viewjob?jk=${jk}` : href;
      return { url, title: txt(document.querySelector("h1, .jobsearch-JobInfoHeader-title")) };
    }
    if (/stepstone\.de/.test(location.hostname)) {
      if (!/stellenangebote/.test(location.pathname)) return null;
      return { url: href.split("?")[0], title: txt(document.querySelector("h1")) };
    }
    return null;
  }

  // ------------------------------------------------------------ lazy loading
  /**
   * LinkedIn (and Indeed to a lesser degree) only render the cards near the
   * viewport. Collecting without scrolling first captures a handful of jobs out
   * of ~25 — which is exactly why an early LinkedIn run returned only 7.
   * Scroll the results container until the card count stops growing.
   */
  async function loadAllCards(onProgress) {
    const container =
      document.querySelector(".jobs-search-results-list") ||
      document.querySelector("[class*='jobs-search-results-list']") ||
      document.querySelector("#mosaic-provider-jobcards") ||
      null;

    /*
     * Two rules here, both learned the hard way.
     *
     * 1. NEVER move the scroll position back afterwards. The previous attempt
     *    restored the position captured before loading started, which is 0 for
     *    anyone who was at the top when they hit collect, so "restore" and
     *    "yank to the top" were the same instruction. The scroll is the
     *    reader's, not ours: we borrow it to force lazy rendering and we leave
     *    it wherever it ends up.
     *
     * 2. Detect the takeover by POSITION, not only by input events. Listening
     *    for wheel and touchmove misses trackpad momentum, scrollbar drags,
     *    and any scroll the site's own handlers swallow before it reaches
     *    window. After each programmatic scroll we record where we put it; if
     *    the position later differs by more than a wobble, somebody else moved
     *    it and we stop driving immediately.
     */
    let userScrolled = false;
    let expectedTop = null;              // where WE last put it
    const posOf = () => (container ? container.scrollTop : window.scrollY);
    const yield_ = () => { userScrolled = true; };
    const onScroll = () => {
      if (expectedTop === null) return;
      if (Math.abs(posOf() - expectedTop) > 40) userScrolled = true;
    };
    const keyYield = (e) => {
      if (["PageDown", "PageUp", "Home", "End", "ArrowDown", "ArrowUp", " "]
          .includes(e.key)) yield_();
    };
    const scrollTarget = container || window;
    scrollTarget.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("wheel", yield_, { passive: true });
    window.addEventListener("touchmove", yield_, { passive: true });
    window.addEventListener("keydown", keyYield);

    try {
      let prev = -1;
      let stable = 0;
      for (let i = 0; i < 25 && stable < 2; i++) {
        if (cancelled || userScrolled) break;
        const n = collectCards().length;
        if (n === prev) stable++; else stable = 0;
        prev = n;
        onProgress?.(`loading cards… ${n}`);

        if (container && container.scrollHeight > container.clientHeight) {
          container.scrollTop = container.scrollHeight;
        } else {
          window.scrollBy(0, Math.round(window.innerHeight * 0.9));
        }
        // Let the browser settle on the new offset before recording it, or the
        // very next scroll event reads as a user takeover and stops the run.
        await sleep(60);
        expectedTop = posOf();
        await sleep(site?.slow ? jitter(700, 1100) : jitter(400, 700));
      }
    } finally {
      // Always unhook, including on cancel. Leaked listeners from earlier runs
      // used to keep firing against a dead closure.
      scrollTarget.removeEventListener("scroll", onScroll);
      window.removeEventListener("wheel", yield_);
      window.removeEventListener("touchmove", yield_);
      window.removeEventListener("keydown", keyYield);
    }
    await sleep(400);
    return collectCards().length;
  }

  // ------------------------------------------------------------ collectors
  /*
   * One record per job, deduped by URL.
   *
   * The card selectors overlap on purpose, because each site renames its
   * classes without warning and a spare selector is what keeps the collector
   * working. On LinkedIn they overlap on the SAME job: the outer
   * li[data-occludable-job-id] and the div.job-card-container nested inside it
   * both match, and both parse to the same posting. Every count downstream was
   * therefore exactly doubled, which is why the popup said 50 for a page of 25.
   *
   * The saved file was always right, because saveJobs and the bridge both key
   * by URL. What was not right: the description loop ran twice per job, so a
   * 25-job page made 50 authenticated detail requests. That is half the traffic
   * on a bot-protected site for nothing.
   */
  function collectCards() {
    if (!site) return [];
    const out = [];
    const seen = new Set();
    for (const card of site.cards()) {
      const rec = site.parse(card);
      if (!rec || !rec.url || !rec.title) continue;
      const key = rec.url.split("?")[0].replace(/\/+$/, "").toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(rec);
    }
    return out;
  }

  async function collectPage({ withDescriptions, onProgress, skipScroll }) {
    collecting = true;
    try {
      return await runCollect({ withDescriptions, onProgress, skipScroll });
    } finally {
      collecting = false;
    }
  }

  async function runCollect({ withDescriptions, onProgress, skipScroll }) {
    // Force every card to render before reading them, or we silently collect
    // only the handful the site has lazily painted.
    if (!skipScroll) await loadAllCards(onProgress);
    if (cancelled) return { added: 0, total: 0, cancelled: true };
    const found = collectCards();
    // Indeed uses ?q=, LinkedIn uses ?keywords=, StepStone puts it in the path.
    const params = new URLSearchParams(location.search);
    const query =
      params.get("q") ||
      params.get("keywords") ||
      (location.pathname.match(/\/jobs\/([^/]+)\//)?.[1] || "").replace(/-/g, " ");

    if (!withDescriptions) {
      const recs = found.map(({ _el, ...r }) => ({
        ...r, source: site.name, query, description: "",
      }));
      const saved = await saveJobs(recs);
      pushToBridge(recs);
      return saved;
    }

    const recs = [];
    for (let i = 0; i < found.length; i++) {
      // Checked before every job so Stop lands within one job, not at the end
      // of the page. Whatever was already read is still saved below.
      if (cancelled) break;
      const { _el, ...rec } = found[i];
      let description = "";
      try {
        if (site.fetchDesc) {
          // Sites that navigate on click are read by fetching the detail page
          // from this tab instead, which keeps the script alive and is faster.
          description = await site.fetchDesc(rec.url);
        } else {
          _el.click();                     // loads the description pane
          const deadline = Date.now() + 6000;
          let pane = null;
          while (Date.now() < deadline) {
            pane = site.descPane();
            if (pane && pane.innerText.trim().length > 200) break;
            await sleep(200);
          }
          description = pane ? pane.innerText.trim() : "";
        }
      } catch (e) {
        /* keep the card even if the description never loaded */
      }
      recs.push({ ...rec, source: site.name, query, description });
      onProgress?.(i + 1, found.length);
      await sleep(site.slow ? jitter(1100, 2100) : jitter(400, 900));
    }
    // A cancelled run still keeps and pushes what it managed to read, so
    // stopping never throws away work already done.
    const saved = await saveJobs(recs);
    pushToBridge(recs);
    return { ...saved, cancelled, collected: recs.length, of: found.length };
  }

  /*
   * Auto-collect has to survive page navigation.
   *
   * On Indeed the "next page" control is a real link, so clicking it reloads
   * the document and destroys this content script along with any in-memory
   * loop — auto-collect would silently stop after page 1. So the run state
   * lives in chrome.storage instead, and a fresh script instance resumes it on
   * load. LinkedIn paginates in place, so there the same function just
   * continues itself.
   */
  const AUTO = "autoRun";
  const getAuto = async () => (await chrome.storage.local.get(AUTO))[AUTO] || null;
  const setAuto = (v) => chrome.storage.local.set({ [AUTO]: v });
  const clearAuto = () => chrome.storage.local.remove(AUTO);

  async function setStatus(text, running) {
    await chrome.storage.local.set({ autoStatus: { text, running } });
    chrome.runtime.sendMessage({ type: "progress", text }).catch(() => {});
  }

  async function waitForCards(ms = 12000) {
    const deadline = Date.now() + ms;
    while (Date.now() < deadline) {
      if (collectCards().length) return true;
      await sleep(300);
    }
    return false;
  }

  async function stepAuto() {
    const auto = await getAuto();
    if (!auto || auto.remaining <= 0 || !site) return;

    if (!(await waitForCards())) {
      await setStatus("no job cards on this page — stopped", false);
      return clearAuto();
    }

    const pageNo = auto.total - auto.remaining + 1;
    const res = await collectPage({
      withDescriptions: auto.withDescriptions,
      onProgress: (i, n) =>
        setStatus(`page ${pageNo}/${auto.total} · job ${i}/${n}`, true),
    });

    const remaining = auto.remaining - 1;
    const collected = (auto.collected || 0) + res.added;
    const next = site.next();

    if (cancelled) {
      await setStatus(`stopped · +${collected} new across ${pageNo} page(s)`, false);
      return clearAuto();
    }

    if (remaining <= 0 || !next) {
      await setStatus(
        `done · +${collected} new across ${pageNo} page(s)` +
          (next ? "" : " · no further pages"),
        false
      );
      return clearAuto();
    }

    await setAuto({ ...auto, remaining, collected });
    await setStatus(`page ${pageNo} done — opening next…`, true);
    await sleep(site.slow ? jitter(4000, 6500) : jitter(2200, 3800));
    next.click();

    // If pagination happened in place (no reload), no new script instance will
    // spawn to pick the run up — so continue it here.
    await sleep(site.slow ? jitter(3000, 4500) : jitter(1800, 3000));
    if (await getAuto()) stepAuto();
  }

  async function startAuto({ pages, withDescriptions }) {
    await setAuto({ total: pages, remaining: pages, withDescriptions,
                    collected: 0, startedAt: Date.now() });
    stepAuto();          // fire and forget — progress lives in chrome.storage
    return { started: true, pages };
  }

  /*
   * Resume an in-flight run after a navigation destroyed the previous instance.
   *
   * The resume must be time-boxed. A run that dies mid-flight — the tab is
   * closed, pagination has no next page, the site starts refusing requests —
   * leaves autoRun sitting in storage with remaining > 0, and without a guard
   * EVERY later page load on a matched site silently restarts it: scrolling the
   * page, fetching descriptions and clicking through pagination with nobody
   * asking for it. That is indistinguishable from the extension being broken,
   * and against a bot-protected site it is worse than that.
   */
  // Sized against a real run, not a guess. LinkedIn is throttled to roughly
  // 1.1 to 2.1 s per job plus 4 to 6.5 s between pages, so three pages of 25
  // is already several minutes. The old 5 minute ceiling could expire a run
  // that was still working, and the next navigation would quietly discard it.
  const AUTO_MAX_AGE_MS = 20 * 60 * 1000;
  getAuto().then((a) => {
    if (!a || a.remaining <= 0 || !site) return;
    const age = Date.now() - (a.startedAt || 0);
    if (!a.startedAt || age > AUTO_MAX_AGE_MS) {
      console.info("[Job Collector] discarding a stale auto run", { age });
      return clearAuto();
    }
    stepAuto();
  });

  // ------------------------------------------------------------ message API
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    const progress = (text) =>
      chrome.runtime.sendMessage({ type: "progress", text }).catch(() => {});

    if (msg.type === "status") {
      sendResponse({
        ok: !!site,
        site: site?.name || null,
        visible: site ? collectCards().length : 0,
        current: currentJob(),
        collecting,
      });
      return true;
    }

    if (msg.type === "current") {
      sendResponse(currentJob());
      return true;
    }

    if (msg.type === "collect") {
      if (collecting) return sendResponse({ error: "already collecting" }), true;
      cancelled = false;
      // Overwrite the stored status immediately. It outlives the run that
      // wrote it, and the popup falls back to it on every storage change, so a
      // leftover "stopped by user" from an earlier run kept reappearing over
      // the live progress line of this one.
      setStatus("collecting…", true);
      collectPage({
        withDescriptions: msg.withDescriptions,
        onProgress: (i, n) => {
          const text = typeof i === "string" ? i : `job ${i}/${n}`;
          progress(text);
          setStatus(text, true);
        },
      })
        .then((r) => {
          setStatus(
            r?.cancelled
              ? `stopped at ${r.collected}/${r.of} · +${r.added} new kept`
              : `done · +${r?.added ?? 0} new`,
            false
          );
          sendResponse(r);
        })
        .catch((e) => {
          setStatus(`error: ${e}`, false);
          sendResponse({ error: String(e) });
        });
      return true;
    }

    if (msg.type === "auto") {
      if (collecting) return sendResponse({ error: "already collecting" }), true;
      cancelled = false;
      setStatus("starting…", true);
      startAuto({ pages: msg.pages, withDescriptions: msg.withDescriptions })
        .then(sendResponse)
        .catch((e) => sendResponse({ error: String(e) }));
      return true;
    }

    if (msg.type === "stop") {
      // Flip the flag first. Clearing storage alone stops the next page from
      // starting but leaves the current one running to the end, which is
      // exactly what made Stop look broken.
      cancelled = true;
      clearAuto()
        .then(() => setStatus("stopped by user", false))
        .then(() => sendResponse({ stopped: true, wasCollecting: collecting }));
      return true;
    }
    return false;
  });
})();
