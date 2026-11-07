# nlqa: plain-English test cases, run in a real browser

Give it a test case in plain English:

```
python -m nlqa "Log in as a customer and verify the dashboard is shown" --url https://phptravels.net
```

It opens a real browser (Playwright), and an AI model (Google Gemini) works out each click and keystroke
by looking at the page. It reports **PASS / FAIL / ERROR** with the steps it took, screenshots of each
step, and the evidence for its verdict. Nothing is specific to one website.

```
[01] A notice pop-up covers the page; dismiss it first.
     click [37] -> clicked
[02] Fill the email field with the customer email.
     fill [3] with "{{CUSTOMER_EMAIL}}" -> filled
...
✅ PASS  Logged in and the customer dashboard is shown.
   evidence: url_contains '/dashboard'
   evidence: text_visible 'Dashboard'
   Report: reports\20261106-151500\01-log-in-as-a-customer...\report.html
```

See [APPROACH.md](APPROACH.md) for how it works and its limitations.

## Setup (Windows, macOS, Linux)

Requires Python 3.10+.

```powershell
python -m venv .venv
.venv\Scripts\activate              # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

Then create two local files (both are git-ignored):

1. **`.env`**: copy `.env.example` and paste your Gemini API key
   (free at <https://aistudio.google.com/apikey>).
2. **`testdata.env`** (only needed for tests that log in): copy `testdata.env.example` and fill in
   logins.

   **phptravels.net customer login:** new sign-ups on the public demo can't be activated. The site
   can't send the verification email and then refuses to log in until the email is verified. So the
   customer-login test lives in `cases/phptravels-login.txt` and needs an account activated by
   PHPTravels. The same login flow (fill credentials, submit, verify the logged-in page) is
   demonstrated on saucedemo.com in `cases/other-sites.txt`.

## Run

```powershell
# one test case
python -m nlqa "Log in as a customer and verify the dashboard is shown" --url https://phptravels.net

# watch the browser
python -m nlqa "Open the hotels page and search for hotels in Dubai" --url https://phptravels.net --headed

# no --url: the test case names the site itself
python -m nlqa "Go to https://www.saucedemo.com, log in as the standard user and verify products are shown"

# a file of test cases (one per line, '@url' sets the start page)
python -m nlqa --file cases/phptravels.txt
python -m nlqa --file cases/other-sites.txt
python -m nlqa --file cases/phptravels-login.txt   # needs an activated account, see below
```

| Option | Meaning |
|---|---|
| `--url` | Start page (optional if the test case names the site) |
| `--file` | Run every test case in a text file; writes `summary.html` |
| `--headed` | Show the browser window |
| `--vision` | Also send a screenshot to the model each step (helps with icon-only buttons; slower) |
| `--max-steps` | Give up after N steps (default 25) |
| `--model` | Gemini model (default `gemini-3.5-flash-lite`, or `GEMINI_MODEL` in `.env`) |
| `--data` | Test-data file (default `testdata.env`) |
| `--list-models` | Show the Gemini models your key can use |

**Free-tier quota:** each step is one AI call, and a test takes ~3–8 steps. Gemini Flash-Lite models
allow about 500 requests/day on the free tier, Flash models only about 20, so Flash-Lite is the default.
Check your limits at <https://aistudio.google.com/rate-limit>.

If the chosen model is overloaded (HTTP 503), out of free quota or unavailable, the tool switches
automatically to another model your key can use and keeps using it for the rest of the run.

Exit code: `0` all tests passed, `1` at least one FAIL/ERROR, `2` setup problem.

Reports go to `reports/<timestamp>/`: one folder per test with `report.html`, `report.json`
and a screenshot per step.

## Test data and secrets

Test cases say things like "log in as a customer". The AI is told which test data **names** exist,
e.g. `{{CUSTOMER_EMAIL}}` and `{{CUSTOMER_PASSWORD}}`, and types those placeholders. The tool swaps
in the real values from `testdata.env` at the moment of typing, so **passwords are never sent to the AI
model**, and values are masked as `****` in logs and reports. Add any names you need, e.g.
`ADMIN_EMAIL`, `AGENT_PASSWORD`.

## Testing the tool itself (no API key needed)

```powershell
python tests/self_test.py
```

Runs a small local demo site with scripted "AI" decisions and checks that the tool:

| Scenario | Expected |
|---|---|
| Correct login | PASS |
| Wrong password, test expects an error | PASS |
| App shows an error instead of the dashboard | FAIL |
| **Model claims PASS but the page doesn't show it** | **FAIL (claim rejected)** |
| Link opens a new tab | PASS (tool follows the tab) |
| Model uses a non-existent element, then recovers | PASS |
| Checkbox proven ticked by its real state | PASS |
| **Model "proves" a tick with label text or the number '1'** | **FAIL (weak evidence rejected)** |
| Model guesses a URL no link points to | guess refused, test continues |
| Model repeats the same failing action | stopped early with ERROR ("Stuck") |
| Pop-up covers the page; model clicks behind it | click reported as blocked (not silently swallowed) |
| Search box is a custom `<div>` widget, not an `<input>` | found and usable (hotel search flow) |

It also checks that the password never appears in any step log.

## Project layout

```
nlqa/
  __main__.py   command line, test files, test data
  agent.py      observe -> decide -> act loop, evidence verification, secret masking
  browser.py    Playwright: page snapshot (numbered elements) and actions; follows new tabs, iframes
  snapshot.js   in-page script that lists what a user can see and interact with
  prompts.py    instructions given to the AI
  llm.py        Gemini client (REST, retries on rate limits) + scripted client for self-tests
  report.py     HTML/JSON reports
cases/          example test cases (phptravels + other sites)
tests/          self-test: local demo site + scripted decisions
```
