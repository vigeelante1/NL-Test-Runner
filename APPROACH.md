# Approach and limitations

## How it works

The tool is a loop, like guiding a friend at a computer over the phone:

1. **Observe.** Playwright reads the current page and builds a short text snapshot: URL, title,
   headings, a **numbered list** of everything you can interact with, and the visible text.
   ```
   [3] textbox "Email Address" type=email
   [4] textbox "Password" type=password
   [7] button "Sign In"
   [12] link "Hotels" href=/hotels
   [37] button "I Understand & Continue"
   ```
   Each element is tagged in the page with its number, so "click 7" maps back to exactly that element.
   Shadow DOM and iframes are included.
2. **Decide.** The test case, the snapshot and the steps taken so far go to the AI model (Gemini),
   which returns **one** action as JSON: `{"action":"click","id":7,"thought":"Submit the login form"}`.
3. **Act.** Playwright performs it with normal user actions (click, fill, select, press, goto...).
   If an action fails, the error goes back to the model on the next turn so it can try something else.
   A link that opens a new tab is followed automatically.
4. **Repeat** until the model says `done`, or the step limit is reached.

Because the model reads the page fresh at every step, nothing is hard-coded to a site: no selectors,
no site-specific code. The same tool runs on phptravels.net, saucedemo.com or any other site.

## Guarding against false passes (the most important part)

AI models sometimes say "done, it worked" when it didn't. So a **PASS is never taken on the model's word**:

- To pass, the model must give **evidence**: exact strings that should be in the URL or visible on the
  page right now (`url_contains "/dashboard"`, `text_visible "Welcome back"`).
- The tool **re-reads the real page and checks every piece**. If any doesn't hold, the PASS is rejected
  and the model is told why. A second unsupported PASS ends the test as FAIL.
- Evidence must be **specific**. Text that is on almost any page (`"1"`, `"ok"`) is ignored, and a
  PASS needs at least one piece of evidence that only holds if the test really passed.
- Some facts can't be proven by text: a checkbox label is visible whether or not the box is ticked.
  For those the model points at the element (`element_checked`, `element_unchecked`, `field_value`)
  and the tool reads the element's **real state** from the browser.
- `tests/self_test.py` includes a simulated lying model and a model giving weak evidence; both end as FAIL.

(This was added after a real run: on a checkbox test the model "proved" a tick with the label text
`checkbox 1`, which is visible either way. The verdict happened to be right, but the proof wasn't.)

Three different outcomes keep reports honest:
- **PASS**: the expectation is true, and the evidence was verified.
- **FAIL**: the steps ran but the application didn't behave as the test expects.
- **ERROR**: the test couldn't run (missing test data, captcha/bot check, AI unavailable, step limit).

Test cases that *expect* an error ("verify an error is shown for a wrong password") pass when the error appears.

## Credentials

phptravels.net does not publish demo logins; they're given out after filling a form at
phptravels.com/demo. The tool doesn't fill that kind of form with personal data. Logins live in a local
`testdata.env`. The model only sees placeholder names (`{{CUSTOMER_PASSWORD}}`); real values are swapped
in at the moment of typing and masked in logs and reports, so they never reach the AI provider.

## Choices

- **Python + Playwright**: auto-waiting makes actions far less flaky than raw Selenium for an agent
  that can't predict load times.
- **Text snapshot instead of screenshots**: cheaper, faster, and the model can name exact elements.
  `--vision` adds a screenshot for pages that rely on icons.
- **Gemini Flash** via REST: free tier, fast, JSON output mode. The `LLM` interface is one method,
  so another provider is a small class.
- **Temperature 0** and strict JSON to make runs as repeatable as possible.

## Limitations (honest list)

- **Counts are only as precise as the evidence.** "The cart contains 1 item" passed with evidence
  "/cart.html" + "Sauce Labs Backpack", which proves the backpack is in the cart, not that it is the
  *only* item. The model is told to quote text that states a count, but a page may not show one.
- **Not fully deterministic.** The same test can take slightly different paths on different runs;
  verdicts are stable for clear test cases, but vague ones ("check the site works") are interpreted
  by the model. Its interpretation is visible in the step thoughts.
- **Evidence covers text, URLs and element states** (checked, field values), not visual facts
  (layout, colours, images).
- **Bot checks / captchas** stop the run with ERROR. The tool does not try to bypass them.
- **Speed and limits.** Each step is one AI call (2–6 s, plus a pause for the free-tier rate limit),
  so a 10-step test takes about a minute. Long flows may hit the step limit (`--max-steps`).
- **Large pages** are truncated (250 elements, 2,500 characters of text), so something far down a very
  long page may need a scroll first.
- **Canvas / drag-and-drop / file uploads / 2FA / emails** are not supported.
- **Screenshots** in reports show typed non-password values (e.g. emails) as they appear on screen.
- **Tested** end-to-end against a local demo site with scripted decisions (`tests/self_test.py`);
  live runs depend on the target site being up and Gemini's free-tier quota.
