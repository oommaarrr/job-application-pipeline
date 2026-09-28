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

  /*
   * querySelectorAll that also looks inside shadow roots.
   *
   * The page carries an element marked interop-shadowdom, and a plain
   * document.querySelectorAll stops at its boundary. Anything rendered inside
   * would be invisible to every selector in this file, which is a silent
   * failure rather than an error.
   */
  function deepAll(sel) {
    const out = [...document.querySelectorAll(sel)];
    const roots = [document];
    for (let i = 0; i < roots.length && i < 50; i++) {
      for (const el of roots[i].querySelectorAll("*")) {
        if (!el.shadowRoot) continue;
        roots.push(el.shadowRoot);
        out.push(...el.shadowRoot.querySelectorAll(sel));
      }
    }
    return out;
  }

  /*
   * One canonical key per job, used everywhere a job is deduped.
   *
   * The old key was url.split("?")[0], which is right for LinkedIn and
   * StepStone, where the path identifies the job, and catastrophic for Indeed,
   * where it does not: every posting is /viewjob?jk=<id> or /rc/clk?jk=<id>, so
   * stripping the query reduced a whole page of results to a single key. That
   * is why a full Indeed search reported two jobs. Keep the identifying
   * parameter and the key is unique per job and stable across the tracking
   * parameters Indeed bolts on.
   */
  function jobKey(url) {
    const u = (url || "").trim();
    if (!u) return "";
    const jk = u.match(/[?&](?:jk|vjk)=([0-9a-zA-Z]{6,})/)?.[1];
    if (jk) return `indeed:${jk.toLowerCase()}`;
    const li = u.match(/\/jobs\/view\/(\d+)/)?.[1] ||
               u.match(/[?&]currentJobId=(\d+)/)?.[1];
    if (li) return `linkedin:${li}`;
    return u.split("?")[0].replace(/\/+$/, "").toLowerCase();
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
      // Fallback when the container classes above have been renamed. See
      // collectCards.
      link: "a[data-jk], a.jcs-JobTitle, a[href*='/viewjob'], a[href*='/rc/clk']",
      climb: "div.job_seen_beacon, [data-testid='slider_item'], td.resultContent, li, tr",
      /*
       * The jk is the job, the href is not.
       *
       * Indeed links every card through the same tracking path,
       * /rc/clk?jk=<id>&fccid=..., and collectCards dedupes on the URL with the
       * query stripped. So twenty-five different jobs all reduced to
       * "de.indeed.com/rc/clk" and collapsed into one row. That is the whole
       * reason a full page of results reported 2 jobs.
       *
       * Build the canonical /viewjob?jk= form and the key is unique per job,
       * stable across tracking parameters, and the same one the applied list
       * and the dedup history already use.
       */
      parse(card) {
        const a =
          card.querySelector("h2.jobTitle a") ||
          card.querySelector("a.jcs-JobTitle") ||
          card.querySelector("a[data-jk]") ||
          card.querySelector("a[href*='jk=']");
        if (!a) return null;
        const jk = a.getAttribute("data-jk") ||
                   (a.getAttribute("id") || "").match(/^(?:job|sj)_([0-9a-f]{8,})/i)?.[1] ||
                   (a.getAttribute("href") || "").match(/[?&](?:jk|vjk)=([0-9a-f]{8,})/i)?.[1];
        if (!jk) return null;
        const href = `${location.origin}/viewjob?jk=${jk}`;
        return {
          title: txt(card.querySelector("h2.jobTitle")) || txt(a),
          company: txt(card.querySelector("[data-testid='company-name']")),
          location: txt(card.querySelector("[data-testid='text-location']")),
          url: href,
          _el: a,
        };
      },
      /*
       * Class-free card discovery, the same shape as LinkedIn's.
       *
       * The class selectors above found 2 jobs on a page showing twenty-odd:
       * the detail pane on the right and one stray card. Indeed's container
       * classes are generated (css-1abc2de) and change on their own schedule,
       * so anything built on them decays exactly like this.
       *
       * The identifier does not decay. Every job on Indeed is a jk, and it
       * appears in a data-jk attribute, in the anchor id, or in the href.
       * Group by jk and the list is whatever the page happens to be wrapping
       * cards in today.
       */
      records() {
        const byId = new Map();
        const jkOf = (el) => {
          const d = el.getAttribute?.("data-jk");
          if (d) return d;
          const id = el.getAttribute?.("id") || "";
          const m1 = id.match(/^(?:job|sj)_([0-9a-f]{8,})/i);
          if (m1) return m1[1];
          const href = el.getAttribute?.("href") || "";
          return href.match(/[?&](?:jk|vjk)=([0-9a-f]{8,})/i)?.[1] || null;
        };

        for (const el of deepAll("[data-jk], a[id^='job_'], a[id^='sj_'], a[href*='jk=']")) {
          const jk = jkOf(el);
          if (!jk) continue;
          let node = el, card = null;
          for (let i = 0; i < 6 && node; i++, node = node.parentElement) {
            const len = (node.innerText || "").trim().length;
            // Below the floor is an empty wrapper; above it is the detail pane
            // or the whole results column.
            if (len >= 20 && len <= 700) { card = node; break; }
          }
          if (!card) continue;
          const prev = byId.get(jk);
          if (!prev || (card.innerText || "").length > (prev.card.innerText || "").length)
            byId.set(jk, { el, card });
        }

        const out = [];
        for (const [jk, { el, card }] of byId) {
          const lines = [];
          for (const raw of (card.innerText || "").split("\n")) {
            const line = raw.replace(/\s+/g, " ").trim();
            if (!line) continue;
            if (/^(new|easily apply|urgently hiring|hiring multiple|responsive employer|posted|active)\b/i.test(line)) continue;
            if (lines[lines.length - 1] === line) continue;
            lines.push(line);
          }
          const title = dedupeRepeat(lines[0] || "");
          if (!title) continue;
          out.push({
            title,
            company: txt(card.querySelector("[data-testid='company-name']")) || lines[1] || "",
            location: txt(card.querySelector("[data-testid='text-location']")) || lines[2] || "",
            url: `${location.origin}/viewjob?jk=${jk}`,
            _el: el,
          });
        }
        return out;
      },

      /*
       * Fetch the description instead of clicking, for the same reason
       * LinkedIn does: clicking depends on the pane rendering, the URL
       * updating and a class surviving, and fetching depends on none of it.
       * StepStone has been 49/49 on this approach while the click path was
       * 3/24.
       */
      fetchDesc: async (url) => {
        const jk = (url || "").match(/[?&](?:jk|vjk)=([0-9a-f]{8,})/i)?.[1];
        if (!jk) return "";
        const res = await fetch(`${location.origin}/viewjob?jk=${jk}`,
                                { credentials: "include" });
        if (!res.ok) return "";
        const doc = new DOMParser().parseFromString(await res.text(), "text/html");

        for (const tag of doc.querySelectorAll('script[type="application/ld+json"]')) {
          try {
            const data = JSON.parse(tag.textContent);
            const nodes = Array.isArray(data) ? data : [data, ...(data["@graph"] || [])];
            for (const n of nodes) {
              if (!n || !n.description) continue;
              const frag = new DOMParser().parseFromString(n.description, "text/html");
              const text = (frag.body.innerText || frag.body.textContent || "").trim();
              if (text.length > 300) return text.replace(/\n{3,}/g, "\n\n");
            }
          } catch { /* skip a malformed block */ }
        }

        doc.querySelectorAll("script,style,noscript,svg").forEach((n) => n.remove());
        for (const sel of ["#jobDescriptionText", "[id*='jobDescriptionText']",
                           "[class*='jobsearch-JobComponent-description']", "main"]) {
          const node = doc.querySelector(sel);
          const text = (node?.innerText || node?.textContent || "").trim();
          if (text.length > 300) return text.replace(/\n{3,}/g, "\n\n");
        }
        return "";
      },
      jobDelay: () => jitter(2000, 4000),   // real requests now, so pace them

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
      // A LinkedIn results page is 25 cards. Anything less means the lazy list
      // has not finished rendering, not that the search is small, so the
      // collector waits for the full set before it reads anything.
      expectCards: 25,
      // LinkedIn virtualises the results list: cards scroll out of the DOM as
      // new ones scroll in, so a single read at the bottom returns only the
      // handful still mounted. That is the real reason three searches came back
      // with 3, 7 and 4 cards on a page that holds 25 — the scrolling worked,
      // the reading did not. Records are therefore accumulated on every pass
      // and merged, instead of being read once at the end.
      accumulate: true,
      // And do not read a page at all until the full 25 are known. Their
      // instruction, 7 September 2026: scroll to the bottom first, and only
      // start fetching once the page has given up all its cards.
      requireCards: true,
      cards: () =>
        document.querySelectorAll(
          "div.job-card-container, li.jobs-search-results__list-item, " +
          "li[data-occludable-job-id], div[data-job-id], " +
          // Cards often carry the posting id as an URN and nothing else.
          "[data-entity-urn*='jobPosting'], [data-view-name='job-card']"
        ),
      link: "a[href*='/jobs/view/'], a[href*='currentJobId=']",
      climb: "li, div[data-job-id], div.job-card-container, " +
             "[class*='job-card'], [class*='jobs-search-results__list-item'], article",
      /*
       * A card's identity is the job id, not its href.
       *
       * On /jobs/search/ the card link is /jobs/view/<id>. On the newer
       * /jobs/search-results/ page it is /jobs/search-results/?currentJobId=<id>
       * instead, because selecting a card only updates the query string of the
       * page you are already on. Keying on the href therefore matched nothing
       * there, parse() returned null for every card, and the popup showed
       * "0 visible" on a page full of jobs with no indication why.
       *
       * The id itself is carried in four different places depending on the
       * layout, so take whichever answers, and always store the canonical
       * /jobs/view/<id>/ form. That keeps one URL per job across both layouts,
       * which matters because the URL is the dedup key everywhere downstream:
       * chrome.storage, seen_jobs.json and applied.json. Two spellings of one
       * job would mean applying to something already applied for.
       */
      parse(card) {
        const a =
          card.querySelector("a.job-card-container__link") ||
          card.querySelector("a.job-card-list__title") ||
          card.querySelector("a[href*='/jobs/view/']") ||
          card.querySelector("a[href*='currentJobId=']") ||
          card.querySelector("a[class*='card-link']") ||
          card.querySelector("a[href]");
        if (!a) return null;

        const href = a.getAttribute("href") || "";
        const holder = card.matches?.("[data-job-id], [data-occludable-job-id]")
          ? card
          : card.querySelector("[data-job-id], [data-occludable-job-id]");
        const urn = card.matches?.("[data-entity-urn]")
          ? card.getAttribute("data-entity-urn")
          : card.querySelector("[data-entity-urn]")?.getAttribute("data-entity-urn");
        const id =
          href.match(/\/jobs\/view\/(\d+)/)?.[1] ||
          href.match(/[?&]currentJobId=(\d+)/)?.[1] ||
          holder?.getAttribute("data-job-id") ||
          holder?.getAttribute("data-occludable-job-id") ||
          urn?.match(/jobPosting:(\d+)/)?.[1];
        if (!id || !/^\d+$/.test(id)) return null;

        // LinkedIn renders the title twice — once visible, once in an
        // aria-hidden / screen-reader span — so innerText comes back doubled
        // ("AI Engineer AI Engineer"). Collapse an exact repeat, with or
        // without a separator. aria-label is the last resort because the newer
        // cards put the title there and nowhere else readable.
        const rawTitle =
          txt(card.querySelector(".job-card-list__title--link")) ||
          txt(card.querySelector(".job-card-list__title")) ||
          txt(card.querySelector("[class*='job-card'] strong")) ||
          txt(a) ||
          (a.getAttribute("aria-label") || "");
        const title = dedupeRepeat(rawTitle);
        if (!title) return null;

        return {
          title,
          company:
            txt(card.querySelector(".job-card-container__primary-description")) ||
            txt(card.querySelector(".artdeco-entity-lockup__subtitle")) ||
            txt(card.querySelector("[class*='subtitle']")) ||
            txt(card.querySelector("[class*='company-name']")),
          location:
            txt(card.querySelector(".job-card-container__metadata-item")) ||
            txt(card.querySelector("[class*='metadata-item']")) ||
            txt(card.querySelector(".artdeco-entity-lockup__caption")) ||
            txt(card.querySelector("[class*='caption']")),
          url: `https://www.linkedin.com/jobs/view/${id}/`,
          _el: a,
        };
      },
      /*
       * Class-free card discovery.
       *
       * LinkedIn ships CSS modules, so every class is a content hash like
       * _0e18ba6f that changes each deploy. There is no data-job-id on the page
       * and the list cards carry no per-job href either: the only link with a
       * job id in it belongs to the detail pane, which is why every earlier
       * attempt read exactly one job.
       *
       * The id lives in componentkey. On a results page there are 25 distinct
       * componentkey values that are bare job ids, which is the page size, so
       * that attribute is the list. UUID and name-shaped componentkeys belong
       * to other widgets and are filtered out by shape.
       *
       * Two other id sources are merged in so older layouts keep working:
       * ?currentJobId= and /jobs/view/ hrefs. Whichever finds a job, finds it.
       */
      records() {
        const byId = new Map();
        const offer = (id, el) => {
          if (!/^\d{8,10}$/.test(id || "")) return;
          const len = (el.innerText || "").trim().length;
          // Below the floor is an empty wrapper, above the ceiling is the
          // detail pane or a whole column. A card sits between.
          if (len < 15 || len > 600) return;
          const prev = byId.get(id);
          if (!prev || len > (prev.innerText || "").trim().length) byId.set(id, el);
        };

        for (const el of deepAll("[componentkey]"))
          offer(el.getAttribute("componentkey"), el);

        for (const a of deepAll("a[href]")) {
          const href = a.getAttribute("href") || "";
          const id = href.match(/[?&]currentJobId=(\d+)/)?.[1] ||
                     href.match(/\/jobs\/view\/(\d+)/)?.[1];
          if (!id) continue;
          // Climb to the smallest ancestor that reads like a card. A company
          // photo carousel lives in the detail pane and all of its links carry
          // the open job's id, but its anchors wrap a figure and hold no text,
          // so nothing in range is found and they drop out.
          let el = a;
          for (let i = 0; i < 6 && el; i++, el = el.parentElement) {
            const len = (el.innerText || "").trim().length;
            if (len >= 15 && len <= 600) { offer(id, el); break; }
          }
        }

        const out = [];
        for (const [id, card] of byId) {
          const lines = [];
          for (const raw of (card.innerText || "").split("\n")) {
            const line = raw.replace(/\s+/g, " ").trim();
            if (!line) continue;
            if (BADGE.test(line) && line.length < 40) continue;  // "Promoted"
            if (lines[lines.length - 1] === line) continue;      // doubled text
            lines.push(line);
          }
          const title = dedupeRepeat(lines[0] || "");
          if (!title) continue;
          // "Upvest · Berlin" arrives as one line about as often as two.
          const split = (lines[1] || "").split("·").map((x) => x.trim());
          out.push({
            title,
            company: split[0] || "",
            location: split[1] || lines[2] || "",
            url: `https://www.linkedin.com/jobs/view/${id}/`,
            // Cards are clickable divs now, not anchors, so fall back to the
            // card itself as the click target.
            _el: card.tagName === "A" ? card : (card.querySelector("a[href]") || card),
          });
        }
        return out;
      },
      /*
       * Read the description by fetching the job page, not by clicking a card.
       *
       * The click-and-read-the-pane path collapsed on the current layout: 3 of
       * 24 descriptions on 3 September, against StepStone's 49 of 49, and
       * StepStone is the one site that already fetched instead of clicking.
       * Three things have to line up for a click to work here, and each is
       * fragile: the card must actually be clickable now that cards are divs
       * keyed by componentkey rather than links, the URL must update so the
       * right job can be confirmed, and the pane must be findable when every
       * class name is a build hash. Fetching needs none of that.
       *
       * Same-origin fetch from this tab carries the real session and TLS
       * fingerprint, which is the whole reason the extension works where
       * scripted HTTP is refused.
       *
       * JSON-LD is tried first because LinkedIn ships a JobPosting block in the
       * server-rendered HTML, so it is there before any script runs and does
       * not depend on a single class surviving the next deploy.
       */
      /*
       * LinkedIn descriptions, rewritten 18 September 2026.
       *
       * What this replaced was returning the PAGE, not the posting. The old
       * chain ended in `article, main`, and on a hashed-class LinkedIn page
       * every specific selector misses, so `main` matched the whole document.
       * Every LinkedIn "description" in the 18 September pool was therefore the
       * header, a Premium upsell, the footer and the language picker: about
       * 1750 characters of chrome, against 4000 of real text from StepStone and
       * Indeed. Five roles were dropped by the batch as untailorable because of
       * it, and the ranker had been judging German level and years against
       * LinkedIn boilerplate.
       *
       * The fix is a source that actually serves the description: the guest
       * endpoint LinkedIn uses for logged-out job pages. It needs no session,
       * returns the posting body in `.show-more-less-html__markup`, and gave
       * 4225 characters of real text where the page read gave none.
       *
       * The catch-all selectors are gone for good. A wrong description is worse
       * than no description: it is well-formed text about the wrong thing, it
       * survives every downstream check, and it silently poisons the gates and
       * any CV built from it. Empty is recoverable; wrong is not.
       */
      fetchDesc: async (url) => {
        const id = (url || "").match(/\/jobs\/view\/(\d+)/)?.[1];
        if (!id) return "";

        const textOf = (node) =>
          (node?.innerText || node?.textContent || "").replace(/\n{3,}/g, "\n\n").trim();

        // Anything matching this is LinkedIn's furniture, not a job ad. It is
        // the guard that would have caught the whole failure on day one.
        const CHROME_RX = new RegExp([
          "reactivate premium", "select language", "linkedin corporation ©",
          "get ai-powered advice", "community guidelines", "join or sign in",
          "user agreement",
        ].join("|"), "i");
        const isChrome = (t) => !t || CHROME_RX.test(t);

        const good = (t) => (t && t.length > 300 && !isChrome(t) ? t : "");

        // 1. The guest endpoint. No session needed and it serves the posting
        //    body directly, which the authenticated SPA page does not.
        try {
          const r = await fetch(
            `https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/${id}`,
            { credentials: "omit" });
          if (r.ok) {
            const doc = new DOMParser().parseFromString(await r.text(), "text/html");
            doc.querySelectorAll("script,style,noscript,svg").forEach((n) => n.remove());
            for (const sel of [".show-more-less-html__markup", ".description__text"]) {
              const t = good(textOf(doc.querySelector(sel)));
              if (t) return t;
            }
          }
        } catch { /* fall through to the authenticated page */ }

        // 2. JSON-LD on the full page, when LinkedIn serves it.
        try {
          const res = await fetch(`https://www.linkedin.com/jobs/view/${id}/`,
                                  { credentials: "include" });
          if (!res.ok) return "";
          const doc = new DOMParser().parseFromString(await res.text(), "text/html");

          for (const tag of doc.querySelectorAll('script[type="application/ld+json"]')) {
            try {
              const data = JSON.parse(tag.textContent);
              const nodes = Array.isArray(data) ? data : [data, ...(data["@graph"] || [])];
              for (const n of nodes) {
                if (!n || !n.description) continue;
                // The value is an HTML fragment, so let the parser decode it
                // rather than stripping tags with a regex.
                const frag = new DOMParser().parseFromString(n.description, "text/html");
                const t = good(textOf(frag.body));
                if (t) return t;
              }
            } catch { /* one malformed block should not stop the others */ }
          }

          // 3. Named containers only. No `article`, no `main`, no
          //    `[class*='description']`: those are how the whole page got in.
          doc.querySelectorAll("script,style,noscript,svg").forEach((n) => n.remove());
          for (const sel of ["#job-details", ".jobs-description__content",
                             ".jobs-box__html-content", ".description__text",
                             ".show-more-less-html__markup",
                             "[class*='jobs-description']"]) {
            const t = good(textOf(doc.querySelector(sel)));
            if (t) return t;
          }
        } catch { /* nothing usable */ }

        return "";
      },
      // Descriptions are real HTTP requests now, so pace them like StepStone's
      // rather than at the click-a-card rate.
      jobDelay: () => jitter(2000, 4000),
      descPane: () => {
        const known = document.querySelector(
          "#job-details, .jobs-description__content, .jobs-box__html-content");
        if (known) return known;
        // Hashed classes again. The pane is the tightest block that holds a lot
        // of text and belongs to exactly one job; anything containing several
        // job ids is the results list, not the description.
        let best = null, bestLen = Infinity;
        for (const el of document.querySelectorAll("article, section, div")) {
          const len = (el.innerText || "").length;
          if (len < 400 || len > 20000 || len >= bestLen) continue;
          const ids = new Set();
          for (const a of el.querySelectorAll("a[href*='currentJobId='], a[href*='/jobs/view/']"))
            ids.add((a.getAttribute("href") || "").match(/(\d{6,})/)?.[1]);
          if (ids.size > 1) continue;
          best = el; bestLen = len;
        }
        return best;
      },
      next: () =>
        document.querySelector("button[aria-label='View next page']") ||
        document.querySelector(".jobs-search-pagination__button--next"),
    },

    stepstone: {
      test: () => /stepstone\.de/.test(location.hostname),
      name: "StepStone",
      cards: () => document.querySelectorAll("[data-at='job-item']"),
      link: "a[data-at='job-item-title'], a[href*='/stellenangebote--']",
      climb: "[data-at='job-item'], article, li",
      /*
       * StepStone needs its own, much slower pace.
       *
       * On LinkedIn and Indeed a description is read by clicking a card and
       * reading a pane, so the page issues whatever requests it wants at its
       * own rhythm. StepStone navigates on click, so descriptions are read with
       * a real fetch per job instead. At the default 400-900 ms that is 25 HTTP
       * requests to detail pages in about 16 seconds, from one session, in a
       * dead-even rhythm. That is the exact shape that got this IP flagged by
       * Akamai when the Python scraper was doing it, and being in-session does
       * not make a burst stop looking like a burst.
       *
       * Roughly one page every three or four seconds is what reading actually
       * looks like, and it costs a minute and a half per page.
       */
      jobDelay: () => jitter(2500, 5000),
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

  /*
   * Say hello on load.
   *
   * A manifest v3 service worker sleeps after thirty seconds and only some
   * events wake it. Alarms are supposed to, and on this machine they stopped
   * firing entirely. A message always does, so loading any job page gives the
   * worker a chance to notice a parked "run now" request. One message per page
   * load, and the worker ignores it if there is nothing waiting.
   */
  chrome.runtime.sendMessage({ type: "hello" }).catch(() => {});

  // ------------------------------------------------------------ storage
  async function saveJobs(newJobs) {
    const { jobs = {} } = await chrome.storage.local.get("jobs");
    let added = 0;
    for (const j of newJobs) {
      const key = jobKey(j.url);
      if (!key) continue;
      if (!jobs[key]) added++;
      // Never let a later, description-less sighting clobber a captured one.
      jobs[key] = { ...jobs[key], ...j, description: j.description || jobs[key]?.description || "" };
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
  /*
   * Which element actually scrolls the results.
   *
   * The named selectors are LinkedIn class names, and LinkedIn now ships build
   * hashes, so they match nothing and the scroller fell back to moving the
   * window. On a page where the list is its own scrolling column that does
   * nothing at all: the window is already at the bottom, the list never
   * lazy-renders past the first handful, and the collector reads whatever was
   * painted before it arrived.
   *
   * So find it by behaviour instead. Climb from a real job card until an
   * ancestor is genuinely scrollable, which is true of the list column and
   * false of the wrappers around it.
   */
  /*
   * Find the element that actually scrolls the job list.
   *
   * Rewritten 18 September 2026, from evidence rather than from a theory. The
   * trace of a hands-off LinkedIn run said this, on every pass:
   *
   *   picked container: HEADER
   *   HEADER 0/64/64   HTML 0/861/861   BODY 0/861/861   window 0/861/861
   *
   * Two things there. The container we picked was the page HEADER, 64 pixels
   * tall and unable to scroll at all. And the document itself does not overflow
   * either: scrollHeight equals clientHeight equals 861 everywhere. So the list
   * lives in some nested pane with its own scrollbar, and the old climb-from-a-
   * card walk never found it.
   *
   * Climbing is the wrong shape of search anyway: it returns the FIRST ancestor
   * that looks scrollable, which on a page of nested panes is rarely the one
   * holding the results. Ask a better question instead: of all the elements on
   * the page that can genuinely scroll, which one CONTAINS THE JOB CARDS? That
   * has one answer, and it does not depend on class names, nesting depth, or
   * which pane LinkedIn ships this week.
   */
  function canScroll(el) {
    if (!el || el.nodeType !== 1) return false;
    if (el.scrollHeight <= el.clientHeight + 40) return false;
    const win = el.ownerDocument?.defaultView;
    let st = null;
    try { st = win ? win.getComputedStyle(el) : null; } catch { return true; }
    if (!st) return true;                 // unreadable: judge on size alone
    return /(auto|scroll|overlay)/.test(st.overflowY);
  }

  /*
   * Every scrollable element on the page, cards or not.
   *
   * Used to tell whether ANYTHING moved. The previous version only watched the
   * container it had picked plus html/body/window, so when scrollIntoView did
   * scroll the real pane, the movement was invisible to us: `moved` came back
   * false, three of those in a row tripped the dead-scroller break, and the
   * loader gave up after three passes with seven cards. The scrolling may well
   * have been working the whole time; the measurement was not.
   */
  function allScrollables(limit = 400) {
    const out = [];
    for (const el of deepAll("div, section, main, ul, ol, aside")) {
      if (out.length >= limit) break;
      if (canScroll(el)) out.push(el);
    }
    return out;
  }

  function cardElements() {
    return deepAll("[componentkey], [data-jk], [data-at='job-item'], " +
                   "a[href*='/jobs/view/'], a[href*='currentJobId=']");
  }

  function scrollContainer() {
    const known =
      document.querySelector(".jobs-search-results-list") ||
      document.querySelector("[class*='jobs-search-results-list']") ||
      document.querySelector("#mosaic-provider-jobcards");
    if (known && canScroll(known)) return known;

    const cards = cardElements();
    if (!cards.length) return null;

    // Score every ancestor by how many cards it holds.
    const holds = new Map();
    for (const card of cards) {
      let el = card.parentElement;
      for (let i = 0; el && i < 25; i++, el = el.parentElement) {
        holds.set(el, (holds.get(el) || 0) + 1);
      }
    }

    // Of those, keep the ones that can actually scroll, and prefer the one
    // holding the most cards. A tie goes to the SMALLEST, which is the list
    // pane rather than the page wrapper that happens to contain it.
    const ranked = [...holds.entries()]
      .filter(([el]) => el !== document.body && el !== document.documentElement)
      .filter(([el]) => canScroll(el))
      .sort((a, b) => b[1] - a[1] || a[0].scrollHeight - b[0].scrollHeight);

    if (ranked.length) return ranked[0][0];

    // Nothing scrollable holds a card yet. That is normal before the list has
    // grown past its pane, so fall back to the largest scrollable thing on the
    // page, and to the window if there is not one.
    const any = allScrollables().sort((a, b) => b.scrollHeight - a.scrollHeight);
    return any[0] || null;
  }

  // Filled by loadAllCards, read by runCollect on accumulating sites.
  let lastHarvest = new Map();

  async function loadAllCards(onProgress) {
    const container = scrollContainer();

    /*
     * Every pass merges what is currently mounted into one map keyed by URL.
     * On a virtualised list this is the only way to see the whole page: by the
     * time you reach the bottom, the top of the list has been unmounted.
     * harvest() returns the running total, which is what the loop below tests
     * against expectCards, so "25 cards" now means 25 distinct jobs seen, not
     * 25 simultaneously in the DOM.
     */
    lastHarvest = new Map();
    const harvest = () => {
      for (const r of collectCards()) {
        if (r && r.url && !lastHarvest.has(r.url)) lastHarvest.set(r.url, r);
      }
      return lastHarvest.size;
    };

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
    // True only when the page refused to move at all. This is the difference
    // between "this search really has twelve results" and "we never managed to
    // drive the list", and the two must never be confused: the first is a fine
    // page to read, the second is a page we would be reading a fragment of.
    let scrollDead = false;
    let scrollPasses = 0;
    // Distinguishes the three ways a scroll stops moving:
    //   1. we reached the end of a short list   — fine, we got everything
    //   2. the whole list fits on one screen    — fine, nothing to scroll
    //   3. the scroller never responded to us   — NOT fine, we saw a fragment
    // Only the third is a failure. "Nothing moved recently" catches all three,
    // so it is the wrong test; "nothing ever moved, and there was something to
    // scroll" is the right one.
    let everMoved = false;
    /*
     * A per-pass record of what actually moved and what appeared.
     *
     * Three different theories about LinkedIn's scroller have now been shipped
     * and none of them loaded the list unattended: on 18 September a hands-off
     * run read 7 cards where scrolling by hand read 26, while reporting that it
     * had scrolled successfully. Guessing again is not worth another run, so
     * this records the geometry of every candidate on every pass and sends it
     * with the audit. One run now says exactly which element moves, which one
     * grows, and whether the cards follow.
     */
    const trace = [];
    const snapGeom = () => {
      const g = candidates().slice(0, 6).map((el, i) => ({
        el: `${el.tagName || "?"}${el.id ? "#" + el.id : ""}[${i}]`,
        top: Math.round(el.scrollTop || 0),
        h: Math.round(el.scrollHeight || 0),
        ch: Math.round(el.clientHeight || 0),
      }));
      g.push({ el: "window", top: Math.round(window.scrollY),
               h: Math.round(document.documentElement.scrollHeight),
               ch: Math.round(window.innerHeight) });
      return g;
    };
    const hasOverflow = () => {
      for (const el of candidates()) {
        if (el.scrollHeight > el.clientHeight + 100) return true;
      }
      return document.documentElement.scrollHeight > window.innerHeight + 100;
    };
    /*
     * `driving` is the fix for a self-inflicted stall.
     *
     * LinkedIn animates scrolling. We set scrollTop, waited 60ms, recorded the
     * position as "expected", and then the animation carried on moving under
     * us. The very next scroll event was more than 40px away from what we had
     * recorded, which is exactly the signature of a user takeover, so the
     * loader concluded the reader had grabbed the wheel and stopped driving
     * after one or two passes. Every LinkedIn page then read whatever had
     * painted so far: seven or eight cards.
     *
     * So ignore scroll events while we are the ones scrolling, and only record
     * the expected position once the movement has actually stopped.
     */
    let driving = false;
    /*
     * When we last stopped driving. The `driving` flag silences scroll events
     * WHILE we scroll, but LinkedIn keeps moving after we let go: a
     * smooth-scroll tail finishes, and — the case that actually bit — its
     * virtualised list mounts the next batch of cards, which shifts scrollTop
     * on its own. Both fire a scroll event during the 0.4-1.1s sleep between
     * passes, land more than 40px from where we recorded "expected", and read
     * as a human grabbing the wheel. The loop then broke after one pass with
     * 7-8 cards, which is exactly the "YOU scrolled" runs in the audit. The
     * first search of a run escaped it because it was on screen and rendered
     * fast; the later ones did not.
     *
     * So ignore the position-delta heuristic for a grace window after we stop
     * driving — long enough to cover the slow-site sleep and the reflow. A real
     * reader is still caught immediately by the wheel/touch/keydown listeners
     * below, which are genuine input events the page cannot produce for itself.
     */
    let driveEndedAt = 0;
    const GRACE_MS = 1600;          // > slow-site inter-pass sleep (up to 1100) + settle
    const posOf = () => (container ? container.scrollTop : window.scrollY);
    const yield_ = () => { if (!driving) userScrolled = true; };
    const onScroll = () => {
      if (driving || expectedTop === null) return;
      if (Date.now() - driveEndedAt < GRACE_MS) return;   // our own tail/reflow, not a human
      if (Math.abs(posOf() - expectedTop) > 40) userScrolled = true;
    };

    /*
     * Do not guess which element scrolls. Ask the last card to show itself.
     *
     * 7 September 2026: scrolling by hand loaded all 25 cards, and the same
     * page driven by the collector loaded seven. That rules out the reading,
     * the lazy loader and the timing, and leaves exactly one explanation:
     * `container.scrollTop = …` was writing to an element that does not
     * scroll. scrollContainer() has to guess, because LinkedIn's class names
     * are build hashes, and on the /jobs/search-results/ layout it guessed
     * wrong. Every "scroll" was a no-op on a node with no overflow, so the
     * list was never asked for more cards.
     *
     * scrollIntoView() removes the guess. The browser walks up from the
     * element and scrolls whatever actually owns it, however deeply nested and
     * whatever it is called this week. Anchoring on the LAST card also means
     * each pass lands exactly at the current end of the list, which is the
     * position that triggers the next batch.
     *
     * The scrollTop path stays as a fallback for the case where there are no
     * cards yet, and every attempt is checked for real movement so a silent
     * no-op cannot happen again.
     */
    const candidates = () => {
      const out = [];
      if (container) out.push(container);
      const se = document.scrollingElement || document.documentElement;
      if (se && !out.includes(se)) out.push(se);
      if (document.body && !out.includes(document.body)) out.push(document.body);
      // Everything else on the page that can scroll. Without these, movement in
      // the real list pane was invisible and read as "nothing scrolls".
      for (const el of allScrollables(60)) if (!out.includes(el)) out.push(el);
      return out;
    };
    // One number summarising every scroller, so "did anything move" is a
    // single comparison rather than a guess about which one mattered.
    const snapshot = () =>
      candidates().map((el) => el.scrollTop).join(",") + "|" + window.scrollY;

    const lastCardEl = () => {
      const recs = collectCards();
      for (let i = recs.length - 1; i >= 0; i--) {
        const el = recs[i] && recs[i]._el;
        if (el && el.isConnected && el.scrollIntoView) return el;
      }
      return null;
    };

    // Push the view forward one screen. Returns true if anything actually
    // moved, which is the only trustworthy sign that we are driving a real
    // scroller rather than writing to an inert node.
    const advance = async () => {
      driving = true;
      try {
        const before = snapshot();
        const beforeCards = harvest();

        /*
         * Step the list by roughly one screen, and let the virtualiser mount
         * each row as it passes through the viewport. Their observation,
         * 19 September 2026, watching it run: it was jumping straight to the
         * bottom of both the results list AND the job-description pane, and not
         * loading everything.
         *
         * That is exactly what anchoring on the last card and revealing its END
         * does: it teleports past the unmounted middle of a virtualised list,
         * so LinkedIn mounts a single batch and the page reads 7-8 cards, and
         * scrollIntoView drags whatever else can scroll — the description pane,
         * the window — along to the bottom with it. A list like this only
         * mounts rows it scrolls THROUGH, so it has to be travelled, not
         * skipped.
         *
         * `container` is the pane that holds the cards (scrollContainer scores
         * ancestors by how many cards they hold, and the description pane holds
         * none), so stepping its scrollTop moves the results list and nothing
         * else. Progress is new cards OR movement: on a virtualised list the
         * top unmounts as the bottom mounts, so scrollHeight can hold steady
         * while the list is very much advancing.
         */
        if (container && container.scrollHeight > container.clientHeight + 40) {
          const step = Math.max(300, Math.round(container.clientHeight * 0.7));
          try {
            container.scrollTop = Math.min(container.scrollTop + step, container.scrollHeight);
          } catch {}
          await settle();
          if (snapshot() !== before || harvest() > beforeCards) {
            expectedTop = posOf(); return true;
          }
        }

        // Fallback 1: no container, or the one we picked does not actually
        // scroll (the 7 September case, where the class names had moved). Let
        // the browser find the scroller by revealing the last mounted card.
        // Still incremental in effect — we only ever reveal the LAST card that
        // exists, never a position past it.
        const anchor = lastCardEl();
        if (anchor) {
          try { anchor.scrollIntoView({ block: "end", inline: "nearest", behavior: "instant" }); }
          catch { try { anchor.scrollIntoView(false); } catch {} }
          await settle();
          if (snapshot() !== before || harvest() > beforeCards) {
            expectedTop = posOf(); return true;
          }
        }

        // Fallback 2, last resort: nudge every candidate. This can move the
        // wrong pane, which is why it is reached only when neither the results
        // list nor the last card moved anything at all.
        for (const el of candidates()) {
          const step = Math.max(200, Math.round((el.clientHeight || window.innerHeight) * 0.8));
          try { el.scrollTop = el.scrollTop + step; } catch {}
        }
        try { window.scrollBy(0, Math.round(window.innerHeight * 0.8)); } catch {}
        await settle();
        const moved = snapshot() !== before || harvest() > beforeCards;
        expectedTop = posOf();
        return moved;
      } finally {
        driving = false;
        driveEndedAt = Date.now();
      }
    };

    // Wait for movement to stop rather than for a fixed delay, so a
    // smooth-scroll animation cannot be mistaken for a reader taking over.
    const settle = async () => {
      let last = null;
      for (let i = 0; i < 12; i++) {
        await sleep(70);
        const now = snapshot();
        if (last !== null && now === last) break;
        last = now;
      }
    };

    const toTop = async () => {
      driving = true;
      try {
        for (const el of candidates()) { try { el.scrollTop = 0; } catch {} }
        try { window.scrollTo(0, 0); } catch {}
        await settle();
        expectedTop = posOf();
      } finally { driving = false; driveEndedAt = Date.now(); }
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
      /*
       * Stop on a full page, not on a quiet one.
       *
       * Two passes with an unchanged count used to be enough to declare the
       * list finished. On LinkedIn that is usually just the next batch not
       * having arrived yet: on 3 September three searches returned 3, 7 and 4
       * cards while StepStone, which renders its whole page at once, returned
       * 24 and 25. Everything downstream then looked fine, because a short page
       * is indistinguishable from a small search.
       *
       * So where a site declares how many a full page holds, hold out for that
       * number, and treat a plateau as evidence only after it has persisted for
       * several passes.
       */
      const target = site?.expectCards || 0;
      const patience = target ? 6 : 2;
      let prev = -1;
      let stable = 0;
      let n = 0;
      let deadPasses = 0;
      for (let i = 0; i < 40 && stable < patience; i++) {
        if (cancelled || userScrolled) break;
        n = harvest();
        if (target && n >= target) {
          onProgress?.(`${n} cards, full page`);
          break;
        }
        if (n === prev) stable++; else stable = 0;
        prev = n;
        onProgress?.(target ? `loading cards… ${n}/${target}` : `loading cards… ${n}`);

        // Anchor on the last card and let the browser find the scroller.
        const before = n;
        const moved = await advance();
        scrollPasses++;
        // New cards are progress even when we cannot see what moved, and that
        // is the common case on a pane we did not identify. Counting only
        // scroll movement is what stopped the loader after three passes.
        const progressed = moved || harvest() > before;
        if (progressed) everMoved = true;
        if (!progressed) deadPasses++; else deadPasses = 0;
        if (trace.length < 14) {
          trace.push({ pass: scrollPasses, cards: harvest(), moved,
                       progressed, geom: snapGeom() });
        }
        // Three passes where nothing on the page moved is not a full list, it
        // is a scroller we never found. Say so rather than reporting a short
        // page as if the search were small.
        if (deadPasses >= 3) {
          // A failure only if we never got it to move AND there was in fact
          // something to move. A three result search that fits on one screen
          // has nothing to scroll and is a complete page, not a broken one.
          scrollDead = !everMoved && hasOverflow();
          if (scrollDead) {
            console.warn("[job collector] nothing scrolls on this page — the list " +
              "cannot be driven, so only what is already rendered is visible.");
          }
          break;
        }
        await sleep(site?.slow ? jitter(700, 1100) : jitter(400, 700));
      }

      /*
       * Recovery sweep. If the page plateaued below a full set, go back to the
       * top and walk down again: the tail of a virtualised list is often only
       * requested once, and a second pass picks up anything that mounted after
       * we had already scrolled past its slot.
       */
      if (target && harvest() < target && !cancelled && !userScrolled) {
        onProgress?.(`only ${harvest()}/${target}, sweeping again…`);
        await toTop();
        await sleep(jitter(500, 800));
        let dead = 0;
        for (let i = 0; i < 30 && !cancelled && !userScrolled; i++) {
          if (harvest() >= target) break;
          if (!(await advance())) { if (++dead >= 3) break; } else dead = 0;
          await sleep(site?.slow ? jitter(600, 900) : jitter(350, 600));
          onProgress?.(`sweeping… ${harvest()}/${target}`);
        }
      }
    } finally {
      // Always unhook, including on cancel. Leaked listeners from earlier runs
      // used to keep firing against a dead closure.
      scrollTarget.removeEventListener("scroll", onScroll);
      window.removeEventListener("wheel", yield_);
      window.removeEventListener("touchmove", yield_);
      window.removeEventListener("keydown", keyYield);
    }
    /*
     * One last push to the very bottom, then wait properly.
     *
     * LinkedIn only requests the tail of the list once you actually reach the
     * bottom, and it does not have it painted the instant you arrive. The loop
     * above stops as soon as the count holds steady for two passes, which on a
     * slow response is "the next batch has not arrived yet" rather than "there
     * is no next batch". A final scroll and a real pause is the difference
     * between collecting a page and collecting the part of it that had
     * rendered.
     */
    if (!cancelled) {
      await advance();
      onProgress?.("at the bottom, letting the last cards render…");
      await sleep(jitter(1500, 2200));
    }
    const final = harvest();
    if (site?.expectCards && final < site.expectCards && !cancelled && !userScrolled) {
      console.warn(
        `[job collector] only ${final} of ${site.expectCards} cards rendered ` +
        `after ${scrollPasses} scroll pass(es)` +
        (scrollDead ? " — THE LIST NEVER MOVED" : " — this may simply be the last page"));
    }
    return { count: final, dead: scrollDead, passes: scrollPasses, userScrolled,
             everMoved, scrollables: allScrollables(60).length,
             trace, container: container
               ? `${container.tagName}${container.id ? "#" + container.id : ""}`
               : "none (window)" };
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
  function parseAll(cards) {
    const out = [];
    const seen = new Set();
    for (const card of cards) {
      const rec = site.parse(card);
      if (!rec || !rec.url || !rec.title) continue;
      const key = jobKey(rec.url);
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(rec);
    }
    return out;
  }

  /*
   * Find the cards, anchors last.
   *
   * Every adapter above keys off container class names, and those are exactly
   * what LinkedIn renames without notice. When it does, cards() returns an
   * empty list, the popup shows "0 visible", Collect greys out, and there is
   * nothing on screen to say why: the page looks completely normal and full of
   * jobs.
   *
   * The link to the job itself is the one thing that cannot be renamed, because
   * it is what the page is for. So when the container path finds nothing, walk
   * the job links instead and climb to whatever wraps each one. That container
   * is the wrong shape for exact class selectors, but parse() already falls
   * back to [class*='subtitle'] and [class*='metadata-item'] for company and
   * location, and to the anchor's own text for the title, so it still reads.
   *
   * The class-based path stays first because it is precise. This only runs when
   * that has already failed, so a layout change degrades to a slightly rougher
   * read instead of to zero.
   */
  // Climb from each job link to whatever wraps it, and parse those.
  function cardsFromLinks() {
    if (!site.link) return [];
    const cards = new Set();
    for (const a of document.querySelectorAll(site.link)) {
      const card = (site.climb && a.closest(site.climb)) || a.parentElement;
      if (card) cards.add(card);
    }
    return parseAll(cards);
  }

  /*
   * Find the cards, by class first and by link second, and keep the better
   * answer rather than the first non-empty one.
   *
   * "The class selectors found something" is not the same as "the class
   * selectors found everything", and the difference is not academic. On
   * /jobs/search-results/ the open job's detail pane on the right also carries
   * data-job-id, so div[data-job-id] matches exactly one element: the pane, not
   * the list. Returning early on the first non-empty result therefore reported
   * one job on a page showing twenty five, and the link fallback that would
   * have rescued it never ran.
   *
   * So both paths always run and the larger wins. Class selectors win ties
   * because they are precise; the link path is broader but reads company and
   * location off a container it had to guess at.
   */
  // Dedupe a finished record list by URL, keeping the first of each.
  function dedupe(recs) {
    const seen = new Set();
    return recs.filter((r) => {
      if (!r.url || !r.title) return false;
      const key = jobKey(r.url);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  /*
   * Three ways to find the cards, and the one that finds most wins.
   *
   * Precision first: class selectors, then links climbed to a container, then
   * the site's own class-free reader. "Found something" is not "found
   * everything" — the detail pane carries a job link too, so the first two can
   * each return exactly one on a page showing twenty five, and returning early
   * on a non-empty result is what reported 1 job twice in a row.
   */
  function collectCards() {
    if (!site) return [];
    const paths = [
      ["card selectors", parseAll(site.cards())],
      ["job links", cardsFromLinks()],
      ["text reader", dedupe(site.records ? site.records() : [])],
    ];
    // Rank on count first, then on how much of each record is filled in. Two
    // paths often find the same jobs while only one of them also finds the
    // company and location, and a tie broken by declaration order silently
    // picked the poorer one.
    const filled = (recs) => recs.filter((r) => r.company).length;
    paths.sort((a, b) => b[1].length - a[1].length || filled(b[1]) - filled(a[1]));
    const [name, best] = paths[0];
    if (name !== "card selectors" && best.length) {
      console.warn(`[job collector] using the ${name} path: ` +
        paths.map(([n, r]) => `${n} ${r.length} (${filled(r)} with company)`).join(", "));
    }
    return best;
  }

  /*
   * Description capture, verified by identity rather than by length.
   *
   * Clicking a card does not mount a new detail pane, it re-renders the
   * existing one, so the previous job's text stays on screen for as long as
   * the SPA takes to swap. "Wait until some pane has more than 200 characters"
   * is therefore satisfied on the first poll by the *previous* job: the loop
   * exited on iteration one every time and the 6 second deadline was never
   * reached. That is why captured descriptions were offset one to three
   * positions backwards, never forwards, and by a varying amount.
   *
   * It stayed hidden for as long as a duplicate selector made the loop click
   * each card twice, because the second click bought the pane an extra full
   * cycle and the correct text usually overwrote the stale read. Deduping
   * collectCards() by URL was the right fix and it stays; it only removed the
   * accident that was masking this.
   *
   * On timeout the description is empty, never the text that is on screen. A
   * missing description is recovered the next time the card is seen. A wrong
   * one is durable, indistinguishable from a real one because it is
   * well-formed text about a real job, and it silently poisons the German
   * gate, the scoring, and any CV built from it.
   */
  const paneText = () => {
    const p = site.descPane();
    return p ? p.innerText.trim() : "";
  };

  /*
   * The same job spelled two ways.
   *
   * LinkedIn's cards link to /jobs/view/<id> while the opened job lives in
   * ?currentJobId=<id>, and Indeed's cards are tracking redirects that share
   * nothing with /viewjob but the jk. Comparing hrefs would never match on
   * Indeed and would fail on LinkedIn the moment a tracking parameter changed,
   * so compare the identifier the site actually keys on.
   */
  function jobTokenOf(url, el) {
    const u = url || "";
    if (/linkedin\.com/.test(location.hostname)) {
      const m = u.match(/\/jobs\/view\/(\d+)/) || u.match(/[?&]currentJobId=(\d+)/);
      if (m) return m[1];
    }
    if (/indeed\.com/.test(location.hostname)) {
      const m = u.match(/[?&](?:jk|vjk)=([0-9a-z]+)/i);
      if (m) return m[1].toLowerCase();
      const jk = el && el.getAttribute && el.getAttribute("data-jk");
      if (jk) return jk.toLowerCase();
    }
    return jobKey(u);
  }

  async function readDescription(rec, el, prevText, ms = 8000) {
    const want = jobTokenOf(rec.url, el);
    const deadline = Date.now() + ms;
    let last = null;
    while (Date.now() < deadline) {
      if (cancelled) return "";          // Stop lands here too, not 6s later
      const cur = currentJob();
      const onTarget = cur && jobTokenOf(cur.url) === want;
      const text = onTarget ? paneText() : "";
      // Three conditions, all required, and no escape hatch on any of them.
      //   on target   the page says it is showing the job that was clicked
      //   changed     the swap actually happened, not just the URL
      //   stable      one poll unchanged, so a half-rendered pane is not read
      // An earlier draft accepted unchanged text once the page had been
      // settled for 1200 ms, to allow for two postings sharing identical copy.
      // A simulation showed that hands back stale text on every render slower
      // than the window, which is the original bug with extra steps. Identical
      // copy on consecutive cards now times out to empty instead, which is the
      // cheap failure.
      if (onTarget && text.length > 200 && text !== prevText && text === last) {
        return text;
      }
      last = text;
      await sleep(150);
    }
    return "";
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
    let load = { count: 0, dead: false, passes: 0 };
    if (!skipScroll) load = await loadAllCards(onProgress);
    if (cancelled) return { added: 0, total: 0, cancelled: true };
    const loaded = load.count;

    /*
     * Do not read a page we FAILED TO LOAD. Rewritten 18 September 2026.
     *
     * The first version of this refused any page with fewer than 25 cards, on
     * the reasoning that a LinkedIn results page holds 25 and fewer means the
     * lazy list never finished. That was wrong in one important case, and it
     * cost a whole run: a search that genuinely has twelve results also has
     * fewer than 25, and refusing it returns nothing at all. On 18 September
     * every LinkedIn search came back empty for exactly this reason.
     *
     * The real question is not "how many" but "did we manage to drive the
     * list". The loader now answers that directly: `dead` means the scroller
     * never moved under us, which is the genuine failure. A page that scrolled
     * to its end and yielded twelve is a twelve job search, and reading it is
     * correct.
     */
    if (!skipScroll && site?.requireCards && site?.expectCards
        && load.dead && loaded < site.expectCards && !cancelled) {
      const msg = `the list never scrolled — only ${loaded} of ${site.expectCards} `
                + `cards were reachable, not reading a partial page`;
      onProgress?.(msg);
      console.warn(`[job collector] ${msg}`);
      return { added: 0, total: 0, short: true, loaded,
               expected: site.expectCards, dead: true };
    }

    // On a virtualised list the accumulated map is the whole page; a fresh read
    // here would return only what is still mounted.
    const found = (site?.accumulate && lastHarvest.size)
      ? [...lastHarvest.values()]
      : collectCards();
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
      return { ...saved, found: found.length, loaded, scrollPasses: load.passes,
               scrollDead: load.dead, userScrolled: load.userScrolled,
               trace: load.trace, container: load.container,
               everMoved: load.everMoved, scrollables: load.scrollables };
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
          // The previous job's pane is still mounted and still long, so the
          // old "a pane exists with 200+ characters" test was already true on
          // the first poll. Read before the swap, every time. See
          // readDescription: identity is the readiness condition now.
          const prevText = paneText();
          _el.click();
          description = await readDescription(rec, _el, prevText);
        }
      } catch (e) {
        /* keep the card even if the description never loaded */
      }
      recs.push({ ...rec, source: site.name, query, description });
      onProgress?.(i + 1, found.length);
      await sleep(site.jobDelay ? site.jobDelay()
                  : site.slow ? jitter(1100, 2100) : jitter(400, 900));
    }
    // A cancelled run still keeps and pushes what it managed to read, so
    // stopping never throws away work already done.
    const saved = await saveJobs(recs);
    pushToBridge(recs);
    return { ...saved, cancelled, collected: recs.length, of: found.length,
             found: found.length, loaded, scrollPasses: load.passes,
             scrollDead: load.dead, userScrolled: load.userScrolled,
             trace: load.trace, container: load.container,
             everMoved: load.everMoved, scrollables: load.scrollables,
             withDesc: recs.filter((r) => r.description).length,
             mismatch: alignmentWarning(recs) };
  }

  /*
   * Cheap regression guard on description alignment.
   *
   * A description belonging to a different job is invisible in the data: it is
   * well-formed text about a real posting, just not this one. The one signal
   * that costs nothing is whether a record's own company name appears anywhere
   * in its own description. Agency listings and white-labelled ads legitimately
   * omit it, so this warns on a rate and never fails a run.
   *
   * Had this existed, the stale-pane bug would have surfaced on the first
   * scrape instead of at the point of writing applications from it.
   */
  function alignmentWarning(recs) {
    if (recs.length >= 5) {
      // The other way this can fail. readDescription refuses to hand back text
      // it cannot tie to the clicked job, so if the site stops reporting which
      // job is open, every capture times out to empty and the orphan check
      // below never runs, because it only looks at records that have a
      // description. Without this the whole thing would fail silently.
      const empty = recs.filter((r) => !r.description).length;
      if (empty / recs.length > 0.5) {
        console.warn(
          `[job collector] ${empty}/${recs.length} descriptions came back empty. ` +
          `The page may have stopped reporting which job is open.`);
        return { empty, of: recs.length };
      }
    }
    const checked = recs.filter((r) => r.description && r.company);
    if (checked.length < 5) return null;   // too few to read a rate from
    const orphans = checked.filter((r) => {
      // "Company Name · Berlin" and "Company Name (Remote)" both reduce here.
      const name = r.company.toLowerCase().split(/[·|,(]/)[0].trim();
      return name.length >= 3 && !r.description.toLowerCase().includes(name);
    }).length;
    if (orphans / checked.length <= 0.34) return null;
    console.warn(
      `[job collector] ${orphans}/${checked.length} descriptions never mention ` +
      `their own company. Descriptions may be misaligned with their cards.`);
    return { orphans, of: checked.length };
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

  /*
   * Why did no cards appear? Three very different answers, one useless message.
   *
   * "no job cards ever appeared" was reported for every Indeed search on
   * 20 September while LinkedIn worked in the same run. Indeed's entire card
   * model hangs off the data-jk attribute, so for zero of them to exist in the
   * whole deep DOM the page is almost never a thin results page — it is a
   * human-verification wall (Indeed fronts de.indeed.com with PerimeterV / a
   * "Press & Hold" or "Verify you are human" interstitial, and Cloudflare adds
   * its own "Just a moment"), or occasionally a genuine zero-results page. Each
   * needs a different response from the user, so name which it is instead of lumping
   * all three under one line the user cannot act on.
   */
  function pageBlockReason() {
    let hay = "";
    try {
      hay = ((document.title || "") + " " +
             (document.body ? document.body.innerText : "")).slice(0, 4000).toLowerCase();
    } catch { return null; }
    const challenge = [
      "press & hold", "press and hold", "verify you are human",
      "verifying you are human", "are you a human", "additional verification",
      "just a moment", "checking your browser", "unusual traffic",
      "confirm you are a human", "hcaptcha", "recaptcha", "px-captcha",
      "enable javascript and cookies to continue",
    ];
    if (challenge.some((m) => hay.includes(m))) {
      return "Indeed is showing a human-verification page (bot wall). Open the " +
             "search in this browser, pass the check once, then re-run.";
    }
    const empty = [
      "no jobs found", "keine jobs gefunden", "did not match any jobs",
      "keine stellen", "0 jobs", "wir konnten keine",
    ];
    if (empty.some((m) => hay.includes(m))) {
      return "Indeed returned a real page with zero results for this search.";
    }
    return null;
  }

  /*
   * Write the audit line to storage, not to the worker.
   *
   * A message is only delivered if the service worker happens to be alive, and
   * manifest v3 reclaims it after about thirty seconds of quiet. A LinkedIn
   * search takes nearly two minutes, so the worker is routinely torn down and
   * restarted mid-search, and anything it was holding in a variable goes with
   * it. That is exactly how "linkedin: Ai Engineer" ran on 18 September and
   * left no audit line at all while the run counted it as done.
   *
   * Storage survives the teardown. The worker drains it when the search ends.
   */
  async function report(detail) {
    const line = { ...detail, site: site?.name || "", url: location.href,
                   at: new Date().toISOString() };
    try {
      const { searchReports = [] } = await chrome.storage.local.get("searchReports");
      searchReports.push(line);
      await chrome.storage.local.set({ searchReports });
    } catch { /* storage unavailable; the run matters more than the audit */ }
  }

  async function stepAuto() {
    const auto = await getAuto();
    if (!auto || auto.remaining <= 0 || !site) return;

    // Indeed loads its list behind more redirects than LinkedIn and, when it
    // decides to, behind a verification wall; give it longer before judging the
    // page empty so a slow-but-fine load is not read as a failure.
    const waitMs = site.name === "Indeed" ? 22000 : 12000;
    if (!(await waitForCards(waitMs))) {
      const blocked = pageBlockReason();
      await setStatus(blocked ? "blocked by a verification page — stopped"
                              : "no job cards on this page — stopped", false);
      await report({ ok: false,
                     why: blocked || "no job cards ever appeared on the page",
                     found: 0, added: 0, loaded: 0 });
      return clearAuto();
    }

    const pageNo = auto.total - auto.remaining + 1;
    const res = await collectPage({
      withDescriptions: auto.withDescriptions,
      onProgress: (i, n) =>
        setStatus(`page ${pageNo}/${auto.total} · job ${i}/${n}`, true),
    });

    // One line per page, sent to the worker and on to the bridge. Without this
    // a search that refused to load and a search with nothing new both read as
    // "done · +0 new", which is exactly how three empty LinkedIn searches went
    // unnoticed on 18 September.
    await report({
      ok: !res.short,
      why: res.short
        ? `the list never scrolled — only ${res.loaded}/${res.expected} cards reachable`
        : "",
      found: res.found ?? 0,
      added: res.added ?? 0,
      loaded: res.loaded ?? 0,
      withDesc: res.withDesc ?? 0,
      scrollPasses: res.scrollPasses ?? 0,
      scrollDead: !!res.scrollDead,
      // The one field that says whose scrolling did the work. Without it a page
      // loaded by hand and a page loaded by the collector look identical in the
      // audit, and the whole point is knowing whether this runs unattended.
      userScrolled: !!res.userScrolled,
      trace: res.trace || [],
      container: res.container || "",
      everMoved: !!res.everMoved,
      scrollables: res.scrollables ?? 0,
      page: pageNo,
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
