# Walk-In Job Scanner — Bangalore Tech Roles
# Complete Setup Guide (Every Step)

This guide walks you through everything from zero — creating accounts, 
getting API keys, setting up Google Sheets authentication, and deploying 
the scanner — to having Telegram alerts firing every 30 minutes.

---

## What You Need Before Starting

You need a GitHub account, a Google account (Gmail), and a Telegram account. 
That is it. No credit card. No server. Everything runs free.

---

## PART 1: Set Up Your GitHub Repository

### Step 1.1 — Create the repository

Go to github.com and click the + button → New repository. Name it 
`walkin-scanner` (or anything you like). Set it to **Public** — this is 
important because GitHub Actions gives unlimited free minutes for public repos, 
but limits private repos to 2,000 minutes/month. Click "Create repository".

### Step 1.2 — Clone it to your local machine

```bash
git clone https://github.com/YOUR_USERNAME/walkin-scanner.git
cd walkin-scanner
```

### Step 1.3 — Copy the project files

Copy all the Python files from this project into your cloned directory:
`scanner.py`, `sources.py`, `scorer.py`, `storage.py`, `notifier.py`, 
`config.py`, `requirements.txt`, and the `.github/workflows/walkin_alert.yml` file.

---

## PART 2: Get a Free Groq API Key

Groq hosts Llama 3 models for free. This is what scores your listings for legitimacy.

### Step 2.1 — Create a Groq account

Go to **console.groq.com** and sign up (Google login works). No credit card needed.

### Step 2.2 — Generate an API key

In the Groq console, click "API Keys" in the left sidebar → "Create API Key". 
Name it "walkin-scanner". Copy the key — it starts with `gsk_`. 
**Save it somewhere safe** — Groq only shows it once.

Your key looks like: `gsk_abc123def456...` (long random string)

---

## PART 3: Create Your Telegram Bot

### Step 3.1 — Create the bot

Open Telegram and search for `@BotFather`. Start a chat with it and send:
```
/newbot
```
BotFather will ask for a name (e.g. "Bangalore Jobs Alert") and a username 
(must end in "bot", e.g. `blr_walkin_bot`). After you confirm, BotFather 
sends you a **bot token** that looks like:
```
7123456789:AAFBhSKJDHskjdhKJHskdjhKJHsdkjhKJH
```
Save this. This is your `TELEGRAM_TOKEN`.

### Step 3.2 — Find your chat ID

1. Open Telegram and start a conversation with your new bot (search by username, click Start).
2. Send it any message, like "hello".
3. Open this URL in your browser (replace TOKEN with your actual token):
   ```
   https://api.telegram.org/botTOKEN/getUpdates
   ```
4. You'll see a JSON response. Find the part that says `"chat": {"id": 123456789}`.
   That number is your `TELEGRAM_CHAT_ID`. Copy it.

If the response is empty `{"ok":true,"result":[]}`, send another message to the bot 
and refresh the URL.

---

## PART 4: Set Up Google Sheets Authentication

This is the most involved part but you only do it once.

### Step 4.1 — Create a Google Cloud project

Go to **console.cloud.google.com**. Click the project dropdown at the top → 
"New Project". Name it "walkin-scanner". Click Create.

### Step 4.2 — Enable the APIs

In your new project, go to "APIs & Services" → "Enable APIs and Services". 
Search for and enable these two APIs (separately):
- **Google Sheets API**
- **Google Drive API**

Both are free. They're disabled by default to prevent accidental usage.

### Step 4.3 — Create a Service Account

A service account is like a "robot Google account" — it authenticates on 
behalf of your application rather than a real person.

Go to "APIs & Services" → "Credentials" → "Create Credentials" → 
"Service Account". Fill in:
- Service account name: `walkin-scanner`
- Service account ID: auto-filled
- Description: optional

Click "Create and Continue", skip the optional steps, click "Done".

### Step 4.4 — Download the JSON key

Click on the service account you just created. Go to the "Keys" tab. 
Click "Add Key" → "Create new key" → select "JSON" → "Create".

A JSON file will download automatically. Open it — it looks like:
```json
{
  "type": "service_account",
  "project_id": "walkin-scanner",
  "private_key_id": "...",
  "private_key": "-----BEGIN RSA PRIVATE KEY-----\n...",
  "client_email": "walkin-scanner@walkin-scanner.iam.gserviceaccount.com",
  ...
}
```

**Keep this file secure.** This JSON is what gives our script access to 
your Google Sheets. You'll need the entire contents as a GitHub secret.

### Step 4.5 — Create and share the Google Sheet

Go to **sheets.google.com** and create a new spreadsheet. Name it exactly:
```
WalkIn Jobs Bangalore
```
(The script opens the sheet by this exact name. If you want a different name, 
change `sheet_name` in `storage.py`.)

Now share this sheet with your service account:
1. Click "Share" button in the top right of the sheet.
2. In the "Add people" field, paste the `client_email` from your JSON key file.
   It looks like `walkin-scanner@walkin-scanner.iam.gserviceaccount.com`.
3. Set permission to "Editor".
4. Uncheck "Notify people" (it would send an email to the service account, which nobody reads).
5. Click "Share".

The script will automatically add the column headers on first run.

---

## PART 5: Add Secrets to GitHub

Now we store all four credentials in GitHub Secrets so they're available 
to the GitHub Actions workflow without being in your source code.

Go to your GitHub repo → Settings → Secrets and variables → Actions → 
"New repository secret" for each of these:

**Secret 1: `GROQ_API_KEY`**  
Value: the key from Step 2.2 (starts with `gsk_...`)

**Secret 2: `TELEGRAM_TOKEN`**  
Value: the bot token from Step 3.1 (format: `1234567890:AAF...`)

**Secret 3: `TELEGRAM_CHAT_ID`**  
Value: your numeric chat ID from Step 3.2 (format: `987654321`)

**Secret 4: `GOOGLE_CREDS_JSON`**  
Value: the **entire contents** of the JSON key file from Step 4.4.
Open the JSON file in a text editor, select all (Ctrl+A), copy, and paste 
directly into the GitHub secret value field. The whole thing on one conceptual 
block — GitHub handles multi-line values fine.

---

## PART 6: Push Your Code and Test

### Step 6.1 — Commit and push all files

```bash
cd walkin-scanner
git add .
git commit -m "Initial scanner setup"
git push origin main
```

### Step 6.2 — Trigger a manual test run

Go to your GitHub repo → Actions tab. You'll see "Walk-In Job Scanner" in 
the left sidebar. Click it → "Run workflow" → "Run workflow" (green button).

Watch the run in real time. Each step shows its output. If something fails, 
click on the failing step to see the error message.

### Step 6.3 — Verify the results

If everything worked, within 2-3 minutes you should:
1. See green checkmarks on all steps in GitHub Actions
2. See new rows appearing in your Google Sheet
3. Receive Telegram messages from your bot

---

## PART 7: Interpreting Your Telegram Alerts

Each alert looks like this:

```
✅ Walk-In Alert  |  Score: 8/10

Role: Cloud Infrastructure Engineer
Company: Accenture (MNC)
Date: 2025-04-15
Time: 10:00-16:00
Venue: Prestige Tech Park, Block B, Whitefield
Contact: hr.bangalore@accenture.com

Summary: Walk-in for experienced cloud engineers...

Red flags:
  None detected

Source: naukri
View Original Listing →
```

The score breakdown: 9-10 means verified high-confidence, attend with full 
confidence. 7-8 means very likely legitimate, verify the venue on Google Maps 
before going. 5-6 means check the company LinkedIn page and call the contact 
number before showing up. Anything that slips through with red flags listed 
means do extra verification — these are borderline cases the AI flagged.

---

## Troubleshooting Common Issues

**GitHub Actions shows "No such file or directory"**: Make sure the 
`.github/workflows/` directory exists (it starts with a dot — some file managers 
hide it) and the YAML file is inside it.

**"GROQ_API_KEY not set" error**: Go to repo Settings → Secrets → confirm 
the secret is named exactly `GROQ_API_KEY` with no typos.

**"Spreadsheet not found" error**: The Google Sheet name must match exactly 
(check for extra spaces). Also confirm the service account email is shared 
on the sheet with Editor permission.

**Telegram bot sends nothing**: Verify you sent the bot a message first 
(you must initiate the conversation). Verify TELEGRAM_CHAT_ID is the number 
from getUpdates, not the bot username.

**JobSpy returns empty**: LinkedIn blocks scrapers periodically. The other 
sources (Naukri, RSS) will still work. This is expected behavior.
