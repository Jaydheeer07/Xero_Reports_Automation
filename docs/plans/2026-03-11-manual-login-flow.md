# Manual Login Flow Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the broken automated Xero login button with a two-phase manual login flow that uses the existing `/api/auth/setup` and `/api/auth/complete` endpoints.

**Architecture:** The Login button calls `POST /api/auth/setup` to open Chrome at the Xero login page, then transforms into a "Complete Login" button. When the user clicks "Complete Login" after manually logging in, `POST /api/auth/complete` captures and saves cookies, restarts the browser in headless mode, and refreshes the auth badge. No backend changes required — all endpoints already exist.

**Tech Stack:** Vanilla JS (app.js), plain HTML (index.html), plain CSS (styles.css), FastAPI backend (unchanged)

---

### Task 1: Add instruction element to index.html

**Files:**
- Modify: `playwright-service/frontend/index.html:17`

The login button is on line 17. Add a `<p>` element immediately after it to display login instructions.

**Step 1: Add the instruction paragraph**

In `playwright-service/frontend/index.html`, find:
```html
      <button id="login-btn" class="login-btn" style="display:none">Login</button>
```

Replace with:
```html
      <button id="login-btn" class="login-btn" style="display:none">Login</button>
      <p id="login-instruction" class="login-hint" style="display:none"></p>
```

**Step 2: Verify visually**

Open `playwright-service/frontend/index.html` in a browser (or just confirm the element is present in the source). No visible change expected since `display:none`.

**Step 3: Commit**

```bash
git add playwright-service/frontend/index.html
git commit -m "feat: add login instruction element to UI"
```

---

### Task 2: Add `.login-hint` CSS to styles.css

**Files:**
- Modify: `playwright-service/frontend/styles.css` (append at end)

**Step 1: Append the new styles**

At the end of `playwright-service/frontend/styles.css`, add:
```css
/* Login instruction hint */
.login-hint {
  font-size: 0.82rem;
  color: #4a5568;
  margin-top: 6px;
  line-height: 1.5;
}
.login-hint.error {
  color: #c53030;
}
```

**Step 2: Commit**

```bash
git add playwright-service/frontend/styles.css
git commit -m "feat: add login-hint CSS for manual login instruction text"
```

---

### Task 3: Rewrite handleLogin and add handleCompleteLogin in app.js

**Files:**
- Modify: `playwright-service/frontend/app.js:72-83` (the `handleLogin` function)

This is the main change. The current `handleLogin()` calls `/api/auth/automated-login`. We replace it with a two-phase flow and add two helper functions.

**Step 1: Replace `handleLogin()` (lines 72-83)**

Find:
```js
async function handleLogin() {
  const btn = $('login-btn');
  btn.disabled = true;
  btn.textContent = 'Logging in...';
  try {
    await API.post('/api/auth/automated-login', {});
  } catch (e) {
    console.error('Login failed:', e);
  }
  btn.textContent = 'Login';
  await loadAuthStatus();
}
```

Replace with:
```js
async function handleLogin() {
  const btn = $('login-btn');
  btn.disabled = true;
  btn.textContent = 'Opening Xero...';
  hideLoginInstruction();
  try {
    await API.post('/api/auth/setup', {});
    btn.textContent = 'Complete Login';
    btn.disabled = false;
    btn.onclick = handleCompleteLogin;
    showLoginInstruction('Log into Xero in the browser window, then click Complete Login.');
  } catch (e) {
    console.error('Setup failed:', e);
    btn.textContent = 'Login';
    btn.disabled = false;
    showLoginInstruction('Failed to open browser. Is the app running?', true);
  }
}

async function handleCompleteLogin() {
  const btn = $('login-btn');
  btn.disabled = true;
  btn.textContent = 'Saving session...';
  try {
    const result = await API.post('/api/auth/complete', {});
    if (result.success) {
      btn.onclick = handleLogin;
      hideLoginInstruction();
      await loadAuthStatus();
    } else {
      btn.textContent = 'Complete Login';
      btn.disabled = false;
      showLoginInstruction('Login not detected yet — please finish logging in and try again.', true);
    }
  } catch (e) {
    console.error('Complete login failed:', e);
    btn.textContent = 'Complete Login';
    btn.disabled = false;
    showLoginInstruction('Error saving session. Please try again.', true);
  }
}

function showLoginInstruction(msg, isError = false) {
  const el = $('login-instruction');
  el.textContent = msg;
  el.className = isError ? 'login-hint error' : 'login-hint';
  el.style.display = 'block';
}

function hideLoginInstruction() {
  const el = $('login-instruction');
  el.style.display = 'none';
  el.textContent = '';
}
```

**Step 2: Verify the event binding in DOMContentLoaded still works**

The `DOMContentLoaded` block at line 37 already does:
```js
$('login-btn').addEventListener('click', handleLogin);
```
This sets the initial handler to `handleLogin`. Our new code changes `btn.onclick` dynamically. This is fine — `addEventListener` and `onclick` coexist, but to avoid double-firing, change the DOMContentLoaded line to use `onclick` instead:

Find in DOMContentLoaded:
```js
  $('login-btn').addEventListener('click', handleLogin);
```

Replace with:
```js
  $('login-btn').onclick = handleLogin;
```

**Step 3: Commit**

```bash
git add playwright-service/frontend/app.js
git commit -m "feat: replace automated login with manual two-phase login flow"
```

---

### Task 4: End-to-end verification

**Step 1: Start the app**

Ensure `tray.py` is running (for Chrome CDP connection) and the FastAPI service is running:
```bash
cd playwright-service
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Step 2: Open the UI**

Navigate to `http://localhost:8000` in a browser.

Expected: Badge shows "Not Logged In" (red), Login button visible, Run button disabled.

**Step 3: Click "Login"**

Expected:
- Button briefly shows "Opening Xero..."
- Chrome window opens to `https://login.xero.com`
- Button changes to "Complete Login"
- Instruction text appears: "Log into Xero in the browser window, then click Complete Login."

**Step 4: Log in manually**

Complete the Xero login in the Chrome window (email, password, MFA if prompted).

**Step 5: Click "Complete Login" before finishing login (negative test)**

Click "Complete Login" while still on the Xero login page.

Expected: Button shows "Saving session..." then returns to "Complete Login" with error hint: "Login not detected yet..."

**Step 6: Finish login, then click "Complete Login"**

Once on the Xero dashboard, click "Complete Login".

Expected:
- Button shows "Saving session..."
- The Xero login tab in Chrome closes
- Badge turns green: "Logged In"
- Login button hidden
- Run button enabled
- No instruction text visible

**Step 7: Verify session persistence**

Restart the FastAPI server. Refresh the UI.

Expected: Badge shows "Logged In" immediately (cookies restored from DB).

**Step 8: Run a report**

Select an organisation, period, and click "Run Report".

Expected: Report runs in headless mode with no visible Chrome activity.
