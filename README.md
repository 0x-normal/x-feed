# X Feed

A local X activity feed with profiles you choose, category filters, search, pause/resume, and CSV export.

## Deploy to a VPS

See [Ubuntu deployment instructions](deploy/README.md) for GitHub clone/pull, services that start at boot, and encrypted data migration. Account files, session cookies, vaults, and databases are excluded from Git. Transfer existing data separately through an encrypted bundle using SCP or Termius SFTP.

## Start and stop

From this directory in PowerShell:

```powershell
.\stop.ps1
.\start.ps1
```

Keep that PowerShell window running. Ctrl+C stops the combined session; stop.ps1 also stops this workspace's background services. Logs are in data/worker.log and data/worker-error.log.

For a fresh installation, install Python 3.11+ and Node/npm, then run setup.ps1. setup.ps1 installs the Python dependencies, Rettiwt 7.1.3, and a project-local Node 22 runtime.

## Use the feed

Enter an @handle in Build your watchlist and click Track profile. Tracking settings let you choose an interval and login session. Automatic assignment prefers verified sessions. Adding an existing handle updates its settings and resumes it while preserving its following baseline.

The feed combines posts, replies, reposts (retweets), quotes, and newly observed follows. Use the category buttons, watchlist, and search to narrow it. Quote/repost cards include original-post context; replies link to their conversation. Search matches collected post text or the tracked handle. View on X opens the original content.

Pause feed freezes automatic feed refresh; it does not stop collection. Pause on a tracked profile stops its future checks. An already running check can finish. Check now queues an enabled profile, subject to the same cooldowns as scheduled checks.

The browser refreshes every 10 seconds. The worker checks each profile after its interval, with checks processed sequentially; adding many profiles increases latency.

## Following strategy: recent IDs plus a count-increase guard

This follows the strategy in the supplied twitter-tracker-main project: save a recent following window and the profile's total following count, then compare both on later checks.

Each window has a page budget of min(5, max(2, ceil((saved IDs + 20) / 20))). The first baseline uses up to two pages; later windows use up to five. Requests ask for 20 accounts per page, but X may return a different number. Pagination stops early when the cursor ends. This is a bounded recent window, never a full-list crawl. Every page uses the same login session. The adapter supports the string cursors returned by the installed Rettiwt version as well as object cursors.

The first successful window is saved silently. On later checks:

1. Compare current IDs against the previous saved window.
2. Compute current total following count minus previous total following count.
3. Only if that difference is positive, emit unfamiliar IDs in response order, capped at that difference.
4. Save the current count and window together, including when no alerts are emitted.

A failed page leaves both the saved count and IDs unchanged. Missing counts and unexpectedly empty lists also preserve the baseline. A valid zero-following profile can establish an empty baseline. Existing data from the previous strategy gets a fresh silent count baseline on its first successful check because it has no saved total count; prior feed content remains available.

This count guard suppresses page drift when the total is unchanged or lower, but it cannot prove each selected identity is a new follow. New follows can be missed when unfollows offset them between checks, when X reorders the window, or when activity falls outside the five-page limit. There is no unfollow detection. Events record observation time, not exact follow time. Following-start time on each profile shows when its current strategy baseline was established.

Only the reference project's following-detection strategy is applied. The local feed, profile schedules, encrypted session store, and suspended-account exclusions continue to serve this app. No Telegram integration is enabled.

## Post coverage

Each check fetches one recent timeline page and one recent replies page, requesting 20 entries each. X conversation context from other authors is filtered out. Results are merged by post ID and classified as post, reply, repost, or quote; metrics refresh on subsequent checks. The first fetch includes recent existing posts so the feed is useful immediately. There is no crawl of complete post history, and a busy account can publish more than these windows capture between checks. Deleted content is not removed automatically. Image/video attachments currently open through View on X rather than rendering in the feed.

HTTP 429 sets a persistent global cooldown and respects the supplied reset time. The system does not rotate accounts to evade limits. Invalid, locked, or suspended sessions need attention; network failures back off. A changed X user ID for the same handle stops comparisons.

## Login and storage

Login uses existing cookies, not the stored passwords. The Rettiwt bridge constructs authentication in memory from auth_token, ct0, and twid, passes secrets through an anonymous pipe, and never puts them in command-line arguments or logs. The returned authenticated handle must match the assigned login account.

Credentials are encrypted with Fernet; on Windows the key is protected by DPAPI for the current Windows user. Run the app as that user. Back up the SQLite database and vault together. Public collected content is stored unencrypted. Original account text files remain untouched. The dashboard binds only to loopback and requires same-origin JSON for mutations.

To replace an eligible session, stop the worker, export fresh X browser cookies as a JSON object or browser cookie array, then use:

```powershell
.\.venv\Scripts\python.exe -m x_engine refresh-session login_handle 'C:\path\cookies.json'
.\.venv\Scripts\python.exe -m x_engine verify --account login_handle
```

Import new eligible login records with import-accounts. Files are parsed as data, never instructions. Supported colon-delimited records are username:password:email:unused:auth_token:ct0 and username:password:totp_secret:email:unused:auth_token:base64_cookie_json. Rettiwt also requires twid; six-field records need a fresh cookie export before use.

## CLI and export

```powershell
.\.venv\Scripts\python.exe -m x_engine add-target another_handle --interval 60
.\.venv\Scripts\python.exe -m x_engine pause-target another_handle
.\.venv\Scripts\python.exe -m x_engine run --once --target heyfrens1
.\.venv\Scripts\python.exe -m x_engine export posts --format csv --output exports\posts.csv
.\.venv\Scripts\python.exe -m x_engine export events --format json --output exports\events.json
```

Stop the running worker before a one-shot scan or login check; an OS lock prevents concurrent collectors. Exports include all stored matching records, including retained legacy records. For new following observations filter events by kind=follow_observed. CSV formula prefixes are neutralized; credentials cannot be exported. Output files must not already exist.

The active collector uses [Rettiwt-API](https://github.com/Rishikant181/Rettiwt-API). Its interface may need maintenance when X changes. The legacy twifork adapter remains for compatibility; use Rettiwt for the classified feed.

## Development

The feed's **Sound** switch is off by default and remembers its setting in the current browser. When enabled, new activity matching the current feed filters plays one short chime per fetched batch. Initial loading, filter changes, pagination, and repeated polls are silent. Pausing the feed also pauses activity sounds. Keep the dashboard open; this is a browser sound, not a background push notification. Use **Test sound** to preview the chime or activate audio after reloading. Browser interaction may be required before audio can play ([Web Audio autoplay guidance](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API/Best_practices#autoplay_policy)).

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\tools\rettiwt\node_modules\node-win-x64\bin\node.exe tools\rettiwt\bridge.test.mjs
.\tools\rettiwt\node_modules\node-win-x64\bin\node.exe tools\rettiwt\dashboard.test.mjs
.\tools\rettiwt\node_modules\node-win-x64\bin\node.exe tools\rettiwt\sound.test.mjs
```

Tests use synthetic credentials and local HTTP; they do not call X. Windows vault tests need the same user's DPAPI access. The test suite includes synthetic encrypted migration checks.
