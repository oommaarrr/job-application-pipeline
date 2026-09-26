# Job Pipeline

**Finds jobs that fit you and writes a tailored CV and cover letter for each
one, on your own computer.** Works on Mac, Windows and Linux.

[![selftest](https://github.com/oommaarrr/job-application-pipeline/actions/workflows/selftest.yml/badge.svg)](https://github.com/oommaarrr/job-application-pipeline/actions/workflows/selftest.yml)

You collect postings with one click. A free AI on your laptop reads every one
and keeps only the jobs that match you. Claude then writes a CV and a cover
letter for each match, and you review and send them.

![Dashboard: every step, and how many jobs survived each filter](docs/screenshots/dashboard.png)

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/ranking.png" alt="Fit ranking: every job scored against your profile, with the reason"></td>
    <td width="50%"><img src="docs/screenshots/applications.png" alt="Applications: each job with its CV, cover letter and posting"></td>
  </tr>
  <tr>
    <td align="center"><b>Fit ranking</b>: every job scored, with the reason</td>
    <td align="center"><b>Applications</b>: CV, cover letter and posting for each</td>
  </tr>
</table>

<sub>Screenshots show a made-up person and made-up companies.</sub>

---

## How it works

```mermaid
flowchart LR
    A["1. Set up<br/><i>once, ~15 min</i>"] --> B["2. Create your profile<br/><i>once, from your CV</i>"]
    B --> C["3. Collect jobs<br/><i>one click</i>"]
    C --> D["4. Rank<br/><i>automatic, free, local</i>"]
    D --> E["5. Build CVs<br/><i>Claude writes them</i>"]
    E --> F["6. Review and apply<br/><i>you</i>"]
    F --> G["7. Mark as applied<br/><i>never shown again</i>"]
    G -.->|next day| C
```

| Step | You do | It does |
|---|---|---|
| **Collect** | press **+ Arbeitnow**, or run your saved LinkedIn, Indeed and StepStone searches from the Chrome extension | gathers postings with their full descriptions |
| **Rank** | press **Rank pool** | a local AI reads every posting and drops the ones that don't fit: wrong language, too senior, wrong field |
| **Build** | choose how many, press **Build** | Claude writes a one-page CV and a cover letter for each of the best matches |
| **Apply** | open **Applications**, download, send | |
| **Mark applied** | press **Mark this job applied** in the extension | that job never comes back |

---

## What you need

- **A Mac, Windows or Linux computer.**
- **A Claude subscription**, for writing the CVs.
- **Google Chrome** (optional), only for collecting from LinkedIn, Indeed or StepStone.

Everything else (Python on Windows, [Ollama](https://ollama.com), [Claude
Code](https://claude.com/claude-code), and Git for Windows) is installed by the
first start, which asks before each one. On a Mac or Linux you need Python 3.11+
(`python3 --version`); on Windows it is installed for you too.

---

## Set up on Mac or Linux

**1. Get the code.** Download the ZIP from this page (green **Code** button →
**Download ZIP**) and unzip it, or `git clone` it.

**2. Start it.** Open a terminal in that folder (on a Mac: right-click the
folder in Finder → **New Terminal at Folder**) and run:

```bash
./start.sh
```

The first time, it sets everything up by itself and **asks before each step**:

- installs **Ollama** (the local AI that ranks jobs) and **Claude Code** (writes
  the CVs) if they are missing
- signs you in to Claude (your browser opens)
- creates your profile from your current CV: drag the CV file (PDF, Word or
  text) into the window when it asks
- asks to start Job Pipeline by itself at every login (say **yes**)

Then your browser opens the dashboard. If Ollama already has a model installed,
that one is used; otherwise the local model (a few GB, once) downloads in the
background. Every step can be skipped and is offered again next
time. **Open `profiles/me/profile.md` once and check it**: it decides which jobs
rank highly.

**You run `./start.sh` once.** Job Pipeline is a small program on your own
computer: the dashboard is a page it serves, and the Chrome extension talks to
it. With start-at-login on, it runs in the background from then on, starts by
itself when you log in, and restarts itself if it ever crashes. Open the
dashboard from a bookmark: <http://127.0.0.1:8765/dashboard>.

If you said no, it runs only while that terminal window is open (`Ctrl+C`
stops it), and `./start.sh` asks once more next time. Turn it on later with
`scripts/install-agent.sh`, off with `scripts/install-agent.sh --remove`.

**3. (Optional) Install the Chrome extension**: see [below](#chrome-extension-optional).

<details>
<summary>Doing the steps by hand instead</summary>

```bash
./setup.sh                                          # the same setup, on its own
curl -fsSL https://claude.ai/install.sh | bash     # install Claude Code
claude auth login                                   # sign in
claude "/make-profile ~/Downloads/my-cv.pdf"        # your profile, from your CV
scripts/install-agent.sh                            # start at every login (--remove undoes it)
```
</details>

---

## Set up on Windows

Windows 10 or 11. Everything runs natively; no WSL, no admin rights.

**1. Get the code.** Download the ZIP from this page (green **Code** button →
**Download ZIP**) and unzip it somewhere normal, like `Documents`.

**2. Start it:** double-click **`start.bat`** in that folder.

The first time, it sets everything up by itself and **asks before each step**:

- installs **Python** if there is none (press **Y**)
- installs **Git for Windows**, **Ollama** (the local AI that ranks jobs) and
  **Claude Code** (writes the CVs) if they are missing, and puts Claude Code on
  your PATH
- signs you in to Claude (your browser opens)
- creates your profile from your current CV: drag the CV file into the window
  when it asks
- asks to start Job Pipeline by itself at every login, in the background with
  no window (say **Y**)

Then your browser opens the dashboard; Ollama starts by itself. If it already
has a model installed, that one is used; otherwise the local model (a few GB,
once) downloads in the background. Every step can be skipped
and is offered again next time. **Open `profiles\me\profile.md` once and check
it**: it decides which jobs rank highly.

**You double-click `start.bat` once.** Job Pipeline is a small program on
your own computer: the dashboard is a page it serves, and the Chrome extension
talks to it. With start-at-login on, it runs in the background with no window
from then on, and starts by itself when you log in. Open the dashboard from a
bookmark: <http://127.0.0.1:8765/dashboard>.

If you said no, it runs only while that window is open (closing it stops it),
and `start.bat` asks once more next time. Turn it on later with
`scripts\install-agent.bat`, off with `scripts\install-agent.bat --remove`.

**3. (Optional) Install the Chrome extension**: see below.

<details>
<summary>Doing the steps by hand instead</summary>

In PowerShell, then **open a new window** so it finds what was installed:

```powershell
winget install -e --id Python.Python.3.12 --source winget
winget install -e --id Git.Git --source winget
winget install -e --id Ollama.Ollama --source winget
irm https://claude.ai/install.ps1 | iex
claude auth login
claude "/make-profile C:\Users\you\Downloads\my-cv.pdf"
```

`setup.bat` runs the same setup on its own, and `scripts\install-agent.bat`
(`--remove` to undo) starts it at every login. If `claude` is "not recognised"
after installing it, run `start.bat` again: it adds Claude Code to your PATH.
</details>

---

## Chrome extension (optional)

The extension, **Job Collector**, collects jobs from LinkedIn, Indeed and
StepStone and sends them to the pipeline. It reads only pages in your own
Chrome, where you are already signed in, at a human pace. Without it the
pipeline still works with Arbeitnow alone.

![The extension's three tabs: This page, Saved searches and More](docs/screenshots/extension.png)

**Install it once:**

1. Open `chrome://extensions` in Chrome
2. Turn on **Developer mode** (top right)
3. Click **Load unpacked** and choose the `extension` folder inside this project
4. Click the puzzle icon in the toolbar and pin **Job Collector**

**At the top**, on every tab:

- a green dot when the pipeline is running, with how many jobs came in today,
  how many you applied to and how many were ranked (red means it is not
  running; see [If something goes wrong](#if-something-goes-wrong))
- how many jobs this browser has collected, and how many are on the page open now
- **Dashboard**, **Applications** and **Fit ranking** open those pages

### This page

Open a job search on LinkedIn, Indeed or StepStone, then:

- **Collect this page** reads every job on the results page and sends them to
  the pipeline. Keep **Also capture descriptions** ticked: the local AI ranks
  jobs by reading their descriptions.
- **Auto-collect _N_ pages** does the same for the next few pages of results by
  itself. **Stop** ends it at any time.
- **Mark this job applied**: open a job you applied to and press it. That job
  never comes back in a future batch. When a CV was already built for the job,
  it says so above the button.

### Saved searches

Save the searches you run often, then collect all of them with one click.

**To save a search:**

1. On LinkedIn, Indeed or StepStone, search the way you normally would, and set
   the filters you want (location, remote, date posted...)
2. Open the extension, go to **Saved searches** and press **Add the page I am on**

Or open **New search from keywords** at the bottom of the tab: type the job
title and the city, choose the site, the distance and how recent, press
**Open this search**, then **Add the page I am on**.

Every saved search shows the site it runs on. Beside each one:

- **edit** changes its name or its link
- **on / off** skips it on the next run without deleting it
- **remove** deletes it

**Pages each** sets how many pages of results every search collects.

**Run all searches now** opens each saved search in a background tab, one after
the other, and collects it. Keep Chrome open while it runs; you can keep using
it. If it gets interrupted, the button becomes **Resume now** and carries on
where it stopped. The dashboard's **Run scrape** button starts the same run
(Chrome picks it up within a minute). The line under the buttons shows when it
last ran and what it found.

**Export JSON** copies your list of searches; **Import JSON** replaces it. Use
them to move your searches to another browser or computer.

### More

- **Resend everything to the pipeline**: sends every job this browser collected
  again, for example if the pipeline was not running when you collected
- **Export JSON (manual fallback)**: downloads the collected jobs as a file
- **Erase everything and start fresh**: see [Where your files are](#where-your-files-are)
- **Erase collected jobs only**: clears them here, while the pipeline still
  remembers them as already seen

---

## Everyday use

1. Open the dashboard: <http://127.0.0.1:8765/dashboard>. With start-at-login
   on it is always there; otherwise run `./start.sh` (Windows: `start.bat`) first.
2. **Collect.** Press **+ Arbeitnow**, or in the Chrome extension open
   **Saved searches** and press **Run all searches now**.
3. **Rank.** Press **Rank pool**. It takes a few minutes; the **Funnel** shows
   how many jobs survived each filter.
4. **Build.** Pick how many applications you want (5 is a good start) and press
   **Build**. Each one uses some of your Claude usage.
5. **Apply.** Click **Applications** at the top: each job has its CV, its cover
   letter and a link to the posting.
6. **Mark it.** After applying, open the job's page, click the extension, and
   press **Mark this job applied**.

The extension's **Dashboard**, **Applications** and **Fit ranking** buttons open
these pages from anywhere.

---

## Where your files are

| What | Where |
|---|---|
| Your profile | `profiles/me/`, never uploaded anywhere |
| Your CVs and cover letters | `builder/applications/<date>/<Company>/` |
| After **Erase everything** | moved to `builder/applications/archive/`, never deleted |
| Jobs you applied to | `scraper/history/applied.csv` (date, company, title, link), kept forever |
| Jobs collected in the last 30 days | `scraper/history/seen.csv` (title, company, and the day the local AI judged it). A job it already judged on an earlier day is skipped |

**Erase everything and start fresh** (in the extension, under **More**) clears
the collected jobs so the next collection starts from zero, and moves every
built CV and letter into the archive. It never touches your applied list or the
30-day job history, so jobs you applied to, and jobs the local AI already judged
on an earlier day, still never come back.

---

## If something goes wrong

**Look at the dashboard first.** The Setup panel checks everything, and every
step that fails shows why, the fix, and a **Retry** button. Retrying is always
safe: finished work is kept and skipped. The **Activity** panel lists everything
that happened.

| Problem | Fix |
|---|---|
| The dashboard or the extension says the pipeline is offline | It is not running. Double-click `start.bat` (Windows) or run `./start.sh` and answer **yes** to start-at-login; it then runs in the background for good. Already on? Its log is `scraper/out/bridge.log` |
| "Ollama is not running" | It starts by itself when installed; if it keeps failing, open the Ollama app |
| "Your profile is filled in" is red | Run setup again and give it your CV, or `claude "/make-profile <path to your CV>"` |
| Build stops straight away | Read the message in the build log; usually the profile or the Claude login |
| The extension shows old things | You have two copies loaded. Remove the old one at `chrome://extensions` |
| Windows: Python could not be installed | Install it from python.org, tick **Add python.exe to PATH**, then double-click `start.bat` again |
| Windows: typing `python` opens the Microsoft Store | Same fix: that is Windows' placeholder, not Python |
| Windows: "Git for Windows is not installed" | Double-click `start.bat` again and answer **Y**, or `winget install -e --id Git.Git --source winget` |
| Windows: `claude` is not recognised | Double-click `start.bat` again (it adds Claude Code to PATH), then open a new window |

---

## Learn more

[**docs/GUIDE.md**](docs/GUIDE.md) covers everything in depth: every extension
feature, the job sources, how ranking decides, the settings you can change,
how Claude writes the documents, the CV-writing rules, and privacy.

## A note on job sites

LinkedIn, Indeed and StepStone don't allow automated collection in their terms.
The extension reads only search pages you open yourself, at human pace, from
your own browser. Still, know it before you use it. The Arbeitnow source is a
public API and raises none of this.

## License

MIT, see [LICENSE](LICENSE).
