# Tyreek Hill's Daycare: league website

A website for our ESPN fantasy football league: standings, weekly recaps, power rankings,
team pages with rosters by year, league history, head-to-head records, all-time records
and transactions. It rebuilds itself four times a week during the season (Sunday night, Monday, Tuesday and Friday), and you never have to touch it.

**Contents**

1. [What this is and what it costs](#1-what-this-is-and-what-it-costs)
2. [Finding the league ID](#2-finding-the-league-id)
3. [Getting espn_s2 and SWID](#3-getting-espn_s2-and-swid)
4. [Running it on your computer](#4-running-it-on-your-computer)
5. [Putting it on GitHub](#5-putting-it-on-github)
6. [Changing settings (config.toml)](#6-changing-settings-configtoml)
7. [Writing your own recap for a week](#7-writing-your-own-recap-for-a-week)
8. [Using your own domain name](#8-using-your-own-domain-name)
9. [Troubleshooting](#9-troubleshooting)
10. [Once a year: starting a new season](#10-once-a-year-starting-a-new-season)

---

## 1. What this is and what it costs

**It costs nothing.** Everything runs on GitHub's free services:

- **GitHub Pages** hosts the website.
- **GitHub Actions** is a free robot that, four times a week, downloads the latest results from ESPN,
  rebuilds the site and publishes it.

No paid services, no AI services, no server to look after. The written recaps come from
templates filled in with each week's stats, so they cost nothing to generate either.

**How it works, in one line:** ESPN → `data/` folder (saved results) → `site/` folder (web pages) → GitHub Pages.

- Finished seasons are downloaded **once** and saved in `data/seasons/`. Only the current season is re-downloaded each time.
- If ESPN is down, or your cookies have expired, the build stops **before** publishing, so the live site keeps showing its last good version.

> **Privacy note.** On a free GitHub account, Pages only works for **public** repositories.
> That means the code and the saved results in `data/seasons/` (team names, scores, manager names)
> can be seen by anyone who finds the repository. Your ESPN cookies are **never** in the repository;
> they're stored as encrypted GitHub "secrets". The website tells search engines not to list it,
> but anyone with the link can view it.

---

## 2. Finding the league ID

1. Open your league on fantasy.espn.com.
2. Look at the address bar. It looks like `https://fantasy.espn.com/football/league?leagueId=860820`.
3. The number after `leagueId=` is the league ID. It's already set in `config.toml` (`id = 860820`).

---

## 3. Getting espn_s2 and SWID

Our league is private, so the site needs two "cookies" from your ESPN login to read it.

> ⚠️ **Treat these like a password.** Anyone who has them can see our league as you.
> Never paste them into a chat, an email, a screenshot, or any file except `.env` on your own
> computer (or GitHub's secret settings, section 5).

### Chrome or Edge

1. Go to **espn.com** and log in.
2. Press **F12** (Mac: **Cmd+Option+I**) to open Developer Tools.
3. Click the **Application** tab (click **»** if you don't see it).
4. On the left, open **Cookies** and click **https://www.espn.com**.
5. In the filter box, type `espn_s2`. Copy the long text in the **Value** column.
6. Type `SWID` in the filter box. Copy its value, **including the curly braces** `{…}`.

### Safari

1. Safari menu → **Settings** → **Advanced** → tick **Show features for web developers**.
2. Go to **espn.com** and log in.
3. **Develop** menu → **Show Web Inspector** → **Storage** tab → **Cookies** → `espn.com`.
4. Copy the values of `espn_s2` and `SWID` (with braces).

### Firefox

1. Go to **espn.com** and log in.
2. Press **F12** (Mac: **Cmd+Option+I**) → **Storage** tab → **Cookies** → `https://www.espn.com`.
3. Copy the values of `espn_s2` and `SWID` (with braces).

The cookies last a long time (often a year), but logging out of ESPN everywhere can end them early.
Section 9 covers what to do when they expire.

---

## 4. Running it on your computer

You only need this to preview changes or run things by hand. GitHub does the weekly work on its own.

### One-time setup

1. **Install Python 3.11 or newer** from [python.org](https://www.python.org/downloads/).
   On Windows, tick **"Add python.exe to PATH"** on the first screen of the installer.
2. **Open a terminal** in the project folder:
   - Windows: open the folder in File Explorer, right-click → **Open Git Bash here**
     (or open VS Code in the folder and use **Terminal → New Terminal**).
   - Mac: open **Terminal** and type `cd ` followed by the folder path.
3. **Create a "virtual environment"** (a private copy of Python just for this project) and install what it needs:

   ```bash
   python -m venv .venv
   source .venv/Scripts/activate      # Windows (Git Bash)
   # source .venv/bin/activate        # Mac / Linux
   pip install -r requirements.txt
   ```

   When it's active, your terminal prompt starts with `(.venv)`. Run the `source` line again
   each time you open a new terminal.
4. **Add your cookies.** Copy `.env.example` to a new file named `.env` (same folder), open it,
   and paste your values after the `=` signs:

   ```
   ESPN_S2=AEB...long value...
   ESPN_SWID={1234ABCD-....}
   ```

   `.env` is listed in `.gitignore`, so git will never upload it. Leave `.env.example` blank:
   it's the empty template, and it **does** get uploaded.

### Everyday commands

| Command | What it does |
|---|---|
| `python -m src.main all` | Download the latest from ESPN, then build the site (what GitHub runs) |
| `python -m src.main fetch` | Only download from ESPN |
| `python -m src.main build` | Only rebuild the web pages from what's already downloaded (no internet needed) |
| `python -m src.main build --offline` | Build a preview from small fake test data (no cookies needed) |
| `python -m src.main fetch --season 2021 --force` | Re-download one past season |
| `python -m pytest` | Run the automatic checks |

### Previewing the site

```bash
python -m src.main build
python -m http.server 8000 --directory site
```

Open **http://localhost:8000** in your browser, and press **Ctrl+C** in the terminal to stop.
After a rebuild, press **Ctrl+Shift+R** (Mac: **Cmd+Shift+R**) to make sure you see the new version.

---

## 5. Putting it on GitHub

### a. Create the repository

1. Sign in at [github.com](https://github.com) (create a free account if needed).
2. Top right, click **+** → **New repository**.
3. **Repository name:** anything, e.g. `daycare-league`. The site's address will be
   `https://YOUR-USERNAME.github.io/daycare-league/`.
4. Choose **Public** (required for free Pages; see the privacy note in section 1).
5. **Don't** tick "Add a README" or any other box. Click **Create repository**.

### b. Upload the project

In the terminal, in the project folder (use the address GitHub shows you):

```bash
git add .
git commit -m "League website"
git remote add origin https://github.com/YOUR-USERNAME/daycare-league.git
git push -u origin main
```

If git asks you to sign in, follow the prompt; a browser window usually opens.
Before pushing, `git status` should **not** list `.env`. If it does, stop and ask for help.

### c. Add the two secrets

1. In the repository on GitHub: **Settings** → **Secrets and variables** → **Actions**.
2. Click **New repository secret**. Name: `ESPN_S2`, Secret: your espn_s2 value. Click **Add secret**.
3. Click **New repository secret** again. Name: `ESPN_SWID`, Secret: your SWID value (with braces).

### d. Turn on GitHub Pages

1. **Settings** → **Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.

### e. Run it the first time

1. Click the **Actions** tab. If asked, click **I understand my workflows, go ahead and enable them**.
2. Click **Build and deploy site** on the left → **Run workflow** → **Run workflow**.
3. Wait 2–4 minutes for both jobs (**build** and **deploy**) to get a green ✓.
4. The site's link is under **Settings → Pages**, and in the **deploy** step of the run.

From now on it runs by itself, and whenever you push a change:

| When (Toronto time, summer) | What you'll see |
|---|---|
| **Sunday 7:45 PM** | Live scores after the afternoon games |
| **Monday 10 AM** | Live scores after Sunday Night Football |
| **Tuesday 10 AM** | The finished week: final scores, recap and awards |
| **Friday 10 AM** | Thursday Night Football, live |

Times are an hour earlier in winter, and GitHub sometimes starts scheduled runs a little late. Scores update
at these times only, not continuously. For an update right now, use **Run workflow** (step 2 above).

---

## 6. Changing settings (config.toml)

**Everything league-specific lives in `config.toml`.** Edit it in any text editor, or directly on
GitHub (open the file → pencil icon → **Commit changes**; the site then rebuilds on its own).

| Setting | What it does |
|---|---|
| `[league] name` | Name shown at the top of every page |
| `[league] current_season` | The season to treat as "now" (see section 10) |
| `[site] hide_from_search_engines` | `true` asks Google & co. not to list the site |
| `[site] custom_domain` | Your own web address (section 8); leave `""` for none |
| `[colors]` | Site colours as hex codes, e.g. `"#202123"` |
| `[power_rankings]` | Weights for the power ranking formula (should add up to 1.0) |
| `[recaps] manual_override_mode` | `"replace"` or `"above"` (section 7) |

### Nicknames, merges and former members

People are tracked by a permanent **manager code**, so their history stays together even when
they rename their team. Co-owners of a team count as one manager automatically. The code is a
scrambled version of the person's ESPN member ID, because ESPN member IDs are the same value
as their SWID cookie, and the real IDs shouldn't end up in a public repository.

Run `python -m src.main fetch` and the output lists every code with its name, like:

```
m-3f9a1c0e2b7d   Jane Smith — Team Name
```

Then add sections like these at the bottom of `config.toml`:

```toml
# Show a nickname instead of the ESPN name
[manager_overrides."m-3f9a1c0e2b7d"]
display_name = "Big Jane"

# Someone made a new ESPN account: point the new code at their old one
[manager_overrides."m-NEW-ACCOUNT-CODE"]
merge_into = "m-OLD-ACCOUNT-CODE"

# Mark someone as a former member (anyone not in the current season is marked automatically)
[manager_overrides."m-SOME-CODE"]
former = true
```

Keep the quotes exactly as shown. (A raw ESPN ID like `"{ABC12345-...}"` also works here, but
it will then be visible in `config.toml` on GitHub, so the codes are better.)

---

## 7. Writing your own recap for a week

Each week gets an automatic recap. To write your own:

1. Create a file in the `recaps/` folder named `SEASON-weekN.md`, e.g. `recaps/2026-week3.md`.
2. Write normal paragraphs, with a blank line between them. You can use:
   - `**bold**`, `*italic*`
   - `# A heading`
   - `- bullet points`
   - `[link text](https://example.com)`
3. Commit it (or upload it on GitHub: open the `recaps` folder → **Add file** → **Create new file**).

`manual_override_mode` in `config.toml` decides what happens:

- `"replace"` (default): only your recap is shown.
- `"above"`: your recap is shown, with the automatic one underneath.

---

## 8. Using your own domain name

1. In `config.toml`, set `custom_domain = "fantasy.example.com"`, then commit and push.
2. On GitHub: **Settings** → **Pages** → **Custom domain** → type the same address → **Save**.
3. At the company where you bought the domain (GoDaddy, Namecheap, Cloudflare…), add DNS records:
   - **Subdomain** (like `fantasy.example.com`), recommended: one **CNAME** record.
     Name `fantasy`, value `YOUR-USERNAME.github.io`.
   - **Whole domain** (like `example.com`): four **A** records for name `@`, with values
     `185.199.108.153`, `185.199.109.153`, `185.199.110.153` and `185.199.111.153`.
4. DNS changes can take from a few minutes to a day. Then go back to **Settings → Pages** and tick
   **Enforce HTTPS** (it becomes clickable once GitHub has issued the certificate).

---

## 9. Troubleshooting

**The Actions run failed and the log says "ESPN refused access" or mentions cookies.**
Your ESPN cookies expired. Get fresh ones (section 3), then on GitHub go to **Settings → Secrets and
variables → Actions**, click the pencil next to `ESPN_S2`, paste the new value, and save. Do the same
for `ESPN_SWID`, then re-run the workflow (Actions tab → the failed run → **Re-run all jobs**).
Update your local `.env` too.

**The log mentions "ESPN may have changed its data format" or "upgrade espn-api".**
ESPN changes things now and then. On your computer:

```bash
pip install --upgrade espn-api
pip show espn-api        # note the new version number
```

Put that version number in `requirements.txt` (the `espn-api==...` line), run
`python -m pytest` and `python -m src.main all` to check everything works, then commit and push.

**A run failed. Is the site broken?**
No. A failed run never publishes, so the live site keeps its last good version. Open the red run on
the Actions tab and click the failed step to read the message; it's written to explain what to do.

**The site didn't update.**
- Check the Actions tab: did the latest run finish with a green ✓?
- GitHub's servers cache pages for up to 10 minutes, so wait a bit.
- Hard-refresh the page: **Ctrl+Shift+R** (Mac: **Cmd+Shift+R**). On a phone, close the tab and reopen it.

**The automatic runs stopped.**
GitHub pauses scheduled runs in repositories with no commits for 60 days. The workflow makes a small
"keep alive" commit to prevent that, but if it ever happens: Actions tab → **Build and deploy site**
→ **Enable workflow**.

**A past season looks wrong.**
Re-download it: `python -m src.main fetch --season 2021 --force`, then commit and push the updated
file in `data/seasons/`.

**I just want to see the design without any ESPN setup.**
`python -m src.main build --offline` builds a preview from fake test data.

---

## 10. Once a year: starting a new season

When ESPN opens the new season (usually August), edit `config.toml`:

```toml
current_season = 2027
```

Commit and push. The site shows "Season starts soon" with last season's champion until week 1 is played.
The finished season was already saved automatically in `data/seasons/` when its championship ended.

---

### For the curious: how the code is organised

```
config.toml          all league settings
src/fetch.py         talks to ESPN (through the espn-api library)
src/normalize.py     turns ESPN's data into our own simple format
src/stats.py         every calculation (standings, all-play, luck, power rankings, records…)
src/recaps.py        the automatic weekly recaps
src/build.py         turns data + stats into the web pages
src/main.py          the commands in section 4
templates/           page layouts (HTML)
static/              style.css, app.js, favicon
data/seasons/        saved finished seasons (committed)
data/current.json    the current season (re-downloaded every run, not committed)
recaps/              your hand-written recaps
tests/               automatic checks, using small fake seasons in tests/fixtures/
.github/workflows/   the GitHub robot that rebuilds the site
```
