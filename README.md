# REVERE MONITOR v3

Personal situation monitor for Revere, MA.

---

## GITHUB SETUP — STEP BY STEP

### STEP 1 — Create a GitHub account (if you don't have one)
1. Go to **https://github.com**
2. Click **Sign up** in the top right
3. Enter your email, create a password, choose a username
4. Verify your email

---

### STEP 2 — Create a new repository
1. Once logged in, click the **+** button in the top-right corner
2. Click **New repository**
3. Fill in:
   - **Repository name:** `revere-monitor` (or whatever you want)
   - **Visibility:** select **Public** ← this is required for free hosting
   - Leave everything else as default
4. Click **Create repository**

---

### STEP 3 — Upload the files
You'll see an empty repository page. Click **uploading an existing file** (it's a link in the middle of the page, says "upload an existing file").

Drag and drop ALL of these files/folders into the uploader:
```
fetch.py
.gitignore
README.md
public/
  index.html
  config.js
  data.json
```

**Important:** The `.github/workflows/fetch.yml` file needs to be in a folder called `.github` → `workflows`. GitHub's file uploader handles this fine — just drag the whole folder structure.

At the bottom of the upload page, click **Commit changes**.

---

### STEP 4 — Enable GitHub Pages
1. In your repository, click the **Settings** tab (top of page)
2. Scroll down to **Pages** in the left sidebar
3. Under **Source**, select **Deploy from a branch**
4. Under **Branch**, choose `main` and set the folder to `/public`
5. Click **Save**

Your dashboard will be live at:
`https://YOUR_USERNAME.github.io/revere-monitor`

(It takes 1-2 minutes to deploy the first time.)

---

### STEP 5 — Give GitHub Actions permission to write
1. Still in **Settings**, click **Actions** in the left sidebar
2. Click **General**
3. Scroll down to **Workflow permissions**
4. Select **Read and write permissions**
5. Click **Save**

---

### STEP 6 — Run the Action manually (to get your first data)
1. Click the **Actions** tab at the top of your repo
2. You'll see **Fetch Monitor Data** listed on the left
3. Click it, then click the **Run workflow** button (dropdown on the right)
4. Click the green **Run workflow** button
5. Wait about 30-60 seconds for it to complete

After this, it will run automatically every hour.

---

### STEP 7 — Set up your personal config (optional but recommended)
Open `public/config.js` in a text editor and fill in:

```js
const MONITOR_CONFIG = {
  QUICK_LINKS: [
    // Edit labels and URLs here — add/remove/reorder freely
    { label: "Todoist", url: "https://todoist.com/app", icon: "✓" },
    // ...
  ],

  // Get this from: calendar.google.com → ⋮ → Settings → Integrate calendar → Embed code (src URL)
  GOOGLE_CALENDAR_EMBED_URL: "",

  REVERE_TV_CHANNEL_ID: "UCq-Ej7V3_v7NuGUVRnqv8Aw",
};
```

After editing, re-upload `config.js` to your repo (same upload flow as Step 3).

**Note:** config.js is in `.gitignore` so it won't be overwritten by the Action. Your personal tokens/URLs stay local.

---

## WHAT EACH FILE DOES

| File | What it does |
|------|-------------|
| `fetch.py` | Fetches all data (weather, tides, Logan, news, etc.) |
| `.github/workflows/fetch.yml` | Runs fetch.py every hour automatically |
| `public/index.html` | The dashboard itself |
| `public/config.js` | Your personal settings (quick links, calendar URL) |
| `public/data.json` | Generated data file (updated by Action hourly) |
| `.gitignore` | Prevents config.js from going to GitHub |

---

## DATA SOURCES

| Source | Data |
|--------|------|
| NWS api.weather.gov | Current, hourly, 7-day weather |
| sunrise-sunset.org | Sunrise, sunset, civil twilight, day length |
| NOAA Tides & Currents | Boston Harbor hi/lo tide predictions (48h) |
| FAA soa.smext.faa.gov | Logan/BOS airport status and delays |
| MBTA API v3 | Blue Line alerts |
| revere.org/calendar | City events (scraped) |
| Reddit JSON API | r/boston, r/massachusetts, r/NASCAR, r/neoliberal hot posts |
| YouTube RSS feeds | Revere TV latest videos |
| Various RSS feeds | National, Boston, Sports, BC, community news |

---

## TROUBLESHOOTING

**Dashboard shows old data:** The Action hasn't run yet. Go to Actions → Fetch Monitor Data → Run workflow.

**TV embeds show an error:** YouTube live embed channel IDs can change. Find the correct channel ID from the station's YouTube page (view source → search for "channelId") and update in `index.html`.

**Reddit panels fail to load:** Reddit's API occasionally blocks requests. The panel will show a direct link to the subreddit as fallback.

**City calendar shows no events:** revere.org may block scrapers. The "View Full Calendar" link will still work.
