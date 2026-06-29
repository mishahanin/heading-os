# VPS Deployment Guide -- Claude Code Workspace on Ubuntu 24.04 LTS

> Complete step-by-step instructions for deploying the Claude Code workspace to a Hostinger VPS. Written for someone who has never used Linux before. Every command is explained.
> Last Updated: 2026-04-18

---

## Table of Contents

- [Part 0: Before You Start](#part-0-before-you-start)
- [Part 1: First Commands -- Install Midnight Commander](#part-1-first-commands----install-midnight-commander)
- [Part 2: System Setup](#part-2-system-setup)
- [Part 3: Install Claude Code](#part-3-install-claude-code)
- [Part 4: Clone the Workspace](#part-4-clone-the-workspace)
- [Part 5: Python Environment](#part-5-python-environment)
- [Part 6: Environment Variables (.env file)](#part-6-environment-variables-env-file)
- [Part 7: Platform Configuration](#part-7-platform-configuration)
- [Part 8: Sentinel Service](#part-8-sentinel-service)
- [Part 9: Telegram Session Setup](#part-9-telegram-session-setup)
- [Part 10: Sync Setup](#part-10-sync-setup)
- [Part 11: Verification Checklist](#part-11-verification-checklist)
- [Part 12: Daily Operations Quick Reference](#part-12-daily-operations-quick-reference)
- [Part 13: Troubleshooting](#part-13-troubleshooting)
- [Appendix A: Platform Differences](#appendix-a-platform-differences)
- [Appendix B: File Map](#appendix-b-file-map)

---

## Part 0: Before You Start

### What You Need

Before touching the VPS, gather these items:

| Item | Where to Find It |
|------|-------------------|
| VPS IP address | Hostinger dashboard > VPS > "IP Address" field |
| Root password | Hostinger sent it by email when you created the VPS. Also visible in Hostinger dashboard > VPS > "Root Password" |
| GitHub personal access token | https://github.com/settings/tokens -- needed to clone private repos |
| Anthropic API key | https://console.anthropic.com/settings/keys |
| Your `.env` values from Windows | Open the `.env` file in your Windows workspace and copy the values |

### Step 0.1: Download PuTTY

PuTTY is a free program that lets you connect to a Linux server from Windows.

1. Open your browser and go to: **https://www.putty.org/**
2. Click **"Download PuTTY"** (the big green button)
3. Under **"MSI (Windows Installer)"**, click the **64-bit x86** link
4. Run the downloaded installer. Click Next, Next, Install, Finish.
5. PuTTY is now installed. You can find it in the Start Menu.

### Step 0.2: Connect to Your VPS

1. Open **PuTTY** from the Start Menu
2. You will see a window with a field labeled **"Host Name (or IP address)"**
3. Type your VPS IP address into that field (example: `123.45.67.89`)
4. Make sure **Port** is set to `22`
5. Make sure **Connection type** is set to `SSH`
6. Click the **"Open"** button at the bottom

### Step 0.3: The Security Warning

The first time you connect to a new server, PuTTY will show a warning that says:

> "The host key is not cached for this server..."

This is normal for a first connection. Click **"Accept"** (or "Yes").

### Step 0.4: Log In

A black terminal window will appear with a blinking cursor.

1. It says: `login as:` -- type **`root`** and press **Enter**
2. It says: `root@123.45.67.89's password:` -- paste your root password

**How to paste in PuTTY:** You cannot use Ctrl+V. Instead, **right-click** anywhere in the black terminal window. The password will be pasted (you won't see any characters -- that's normal for passwords in Linux). Press **Enter**.

3. If the password is correct, you will see something like:

```
Welcome to Ubuntu 24.04 LTS
root@vps-hostname:~#
```

**You are now connected to your VPS.** The blinking cursor after `#` is where you type commands. Everything you type here runs on the VPS, not on your Windows machine.

### Step 0.5: Quick Orientation

A few things to know about the Linux terminal:

- **You type commands and press Enter** to run them
- **Copy from PuTTY:** Select text with your mouse (it copies automatically)
- **Paste into PuTTY:** Right-click (pastes whatever is in your clipboard)
- **The `#` symbol** at the end of the prompt means you are logged in as root (administrator)
- **To disconnect:** Type `exit` and press Enter, or simply close the PuTTY window
- **If PuTTY disconnects:** Just open PuTTY again and reconnect (Step 0.2). Nothing is lost.

---

## Part 1: First Commands -- Install Midnight Commander

The very first thing to install is **Midnight Commander** (`mc`) -- a visual file manager for Linux. Think of it as Total Commander or Windows Explorer, but inside the terminal. It comes with **`mcedit`**, a text editor that works like a normal editor (arrow keys, typing, saving with a menu) -- much easier than `nano` or `vim`.

### Step 1.1: Update the Software List

Type this command and press Enter:

```bash
apt update
```

**What this does:** Refreshes the list of available software packages. Like clicking "Check for Updates" in Windows. You will see several lines of output ending with "Reading package lists... Done".

### Step 1.2: Install Midnight Commander

```bash
apt install -y mc
```

**What this does:** Downloads and installs Midnight Commander. The `-y` means "yes, install it without asking me to confirm." You will see download progress and "Setting up mc..." near the end.

### Step 1.3: Try It Out

Type `mc` and press Enter. You will see a two-panel file manager:

```
+--Left panel-----------+--Right panel-----------+
| ..                     | ..                     |
| .bashrc                | .bashrc                |
| .profile               | .profile               |
| .ssh/                  | .ssh/                  |
+------------------------+------------------------+
| Command line here                               |
+--------------------------------------------------
```

- **Navigate:** Arrow keys to move up/down, Enter to open a folder
- **Switch panels:** Press Tab
- **Exit mc:** Press F10, then Enter (or press Escape twice)

Press F10 now to exit back to the normal terminal.

### Step 1.4: Set mcedit as Your Default Text Editor

Whenever this guide says "edit a file," we will use `mcedit`. Set it as the default editor:

```bash
echo 'export EDITOR=mcedit' >> ~/.bashrc
echo 'export VISUAL=mcedit' >> ~/.bashrc
source ~/.bashrc
```

**What this does:** Tells Linux to use `mcedit` whenever any program needs to open a text editor. The `source` command reloads the settings so they take effect immediately.

### Step 1.5: How to Use mcedit

To edit any file, type:

```bash
mcedit filename.txt
```

Inside the editor:
- **Type normally** -- it works like Notepad
- **Arrow keys** move the cursor
- **F2** = Save the file
- **F10** = Exit the editor (it will ask to save if you have unsaved changes)
- **F7** = Search for text
- **F4** = Replace text

That's all you need. Every time this guide says "edit a file," open it with `mcedit`.

---

## Part 2: System Setup

Now install everything the workspace needs to run.

### Step 2.1: Install Essential System Packages

Copy and paste this entire command (right-click in PuTTY to paste):

```bash
apt update && apt upgrade -y && apt install -y \
  git git-lfs curl wget unzip build-essential \
  python3 python3-pip python3-venv python3-dev \
  libffi-dev libssl-dev libxml2-dev libxslt1-dev \
  libjpeg-dev zlib1g-dev libpng-dev \
  fonts-liberation ca-certificates gnupg \
  pango1.0-tools libpango-1.0-0 libpangoft2-1.0-0
```

**What this does (line by line):**
- `apt update && apt upgrade -y` -- Updates the software list, then upgrades all installed packages to their latest versions
- `git git-lfs` -- Version control (like GitHub Desktop, but command-line) and Large File Storage
- `curl wget` -- Tools for downloading files from the internet
- `build-essential` -- Compiler tools needed by some Python packages
- `python3 python3-pip python3-venv python3-dev` -- Python programming language and its package manager
- `libffi-dev libssl-dev ...` -- Libraries that Python packages need to compile
- `fonts-liberation` -- Fonts needed for document generation
- `pango1.0-tools ...` -- Text rendering libraries (needed by WeasyPrint for PDF generation)

This will take 1-3 minutes. Wait until you see the `root@...:#` prompt again.

### Step 2.2: Install Node.js 24.x

Claude Code requires Node.js. Install version 24:

```bash
curl -fsSL https://deb.nodesource.com/setup_24.x | bash -
```

**What this does:** Downloads and runs the NodeSource setup script, which adds the Node.js 24.x repository to your system.

Wait for it to finish, then install Node.js:

```bash
apt install -y nodejs
```

### Step 2.3: Initialize Git LFS

```bash
git lfs install
```

**What this does:** Activates Git Large File Storage. The workspace uses this for large binary files (PDFs, images, etc.).

You should see: `Git LFS initialized.`

### Step 2.4: Configure Git Identity

```bash
git config --global user.name "Misha Hanin"
git config --global user.email "mishahanin@users.noreply.github.com"
```

**What this does:** Sets your name and email for git commits made on this VPS.

### Step 2.5: Verify Everything

Run these commands one by one and check the output:

```bash
python3 --version
```
**Expected:** `Python 3.12.x` (the exact minor version may vary)

```bash
node --version
```
**Expected:** `v24.x.x`

```bash
git --version
```
**Expected:** `git version 2.x.x`

```bash
git lfs version
```
**Expected:** `git-lfs/3.x.x`

If all four commands show version numbers, you are ready for the next step.

---

## Part 3: Install Claude Code

### Step 3.1: Install Claude Code

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

**What this does:** Downloads and runs the official Claude Code installer. It will install the `claude` command to `~/.local/bin/`.

Wait for the installation to complete. You should see a success message.

### Step 3.2: Make Claude Available

The installer adds `~/.local/bin` to your PATH, but you need to reload your shell:

```bash
source ~/.bashrc
```

### Step 3.3: Verify Installation

```bash
claude --version
```

**Expected:** A version number like `1.x.x`

### Step 3.4: Authenticate Claude Code

On a VPS (no browser), authenticate using your Anthropic API key. Run:

```bash
claude
```

Claude Code will start and ask you to authenticate. Choose the **API key** method and enter your Anthropic API key when prompted.

Alternatively, you can set it as an environment variable before running Claude:

```bash
export ANTHROPIC_API_KEY="<your-anthropic-api-key>"
```

Then add it permanently:

```bash
echo 'export ANTHROPIC_API_KEY="<your-anthropic-api-key>"' >> ~/.bashrc
source ~/.bashrc
```

**Replace `sk-ant-your-key-here` with your actual key.** Get it from https://console.anthropic.com/settings/keys

### Step 3.5: Test Claude Code

```bash
claude -p "Say hello in one sentence"
```

**Expected:** Claude responds with a greeting. If you see a response, Claude Code is working.

Type `/exit` or press Ctrl+C to exit if you entered interactive mode.

---

## Part 4: Clone the Workspace

### Step 4.1: Create the Workspace Directory

```bash
mkdir -p ~/workspaces
```

**What this does:** Creates a folder called `workspaces` in your home directory (`~` means home directory, which is `/root` for the root user). The `-p` flag means "don't complain if it already exists."

### Step 4.2: Navigate Into It

```bash
cd ~/workspaces
```

**What this does:** Moves you into the `workspaces` folder. Like double-clicking a folder in Windows Explorer.

### Step 4.3: Clone the Repository

```bash
git clone https://github.com/mishahanin/your-workspace-workspace.git your-workspace
```

**What this does:** Downloads the entire workspace from GitHub into a folder called `your-workspace`. This includes all skills, scripts, rules, CRM contacts, reference files, and everything else tracked in git.

If the repository is private, git will ask for your username and password. Use:
- **Username:** your GitHub username
- **Password:** Your GitHub personal access token (NOT your GitHub password)

To create a personal access token: go to https://github.com/settings/tokens > "Generate new token (classic)" > select `repo` scope > Generate > copy the token.

This may take a minute depending on the repository size.

### Step 4.4: Enter the Workspace

```bash
cd your-workspace
```

### Step 4.5: Pull Large Files

```bash
git lfs pull
```

**What this does:** Downloads the large files (PDFs, images, presentations) that Git LFS tracks separately. Without this step, those files would be placeholder pointers instead of actual content.

### Step 4.6: Create Runtime Directories

These directories are excluded from git (they contain runtime state and auth tokens) so they need to be created manually:

```bash
mkdir -p .sentinel .sessions/google .sessions/telegram
```

**What this does:** Creates three directories:
- `.sentinel/` -- Where the Sentinel comms monitor stores its state and logs
- `.sessions/google/` -- Where Google OAuth tokens are stored
- `.sessions/telegram/` -- Where the Telegram client session is stored

### Step 4.7: Verify the Clone

```bash
ls -la
```

**Expected:** You should see folders like `.claude/`, `context/`, `crm/`, `datastore/`, `outputs/`, `reference/`, `scripts/`, and files like `CLAUDE.md`, `.gitignore`, etc.

```bash
git status
```

**Expected:** `On branch main` with a clean working tree (or only untracked files like `.sentinel/`).

---

## Part 5: Python Environment

Python scripts in the workspace need many third-party packages. We install them in an isolated "virtual environment" so they don't interfere with the system Python.

### Step 5.1: Create the Virtual Environment

Make sure you are in the workspace directory:

```bash
cd ~/workspaces/your-workspace
```

Then create the virtual environment:

```bash
python3 -m venv .venv
```

**What this does:** Creates a folder called `.venv` inside the workspace. This folder contains a private copy of Python with its own package directory. Think of it as a sandbox for Python packages.

### Step 5.2: Activate the Virtual Environment

```bash
source .venv/bin/activate
```

**What this does:** Switches your terminal to use the Python inside `.venv` instead of the system Python. You will notice your prompt changes to show `(.venv)` at the beginning:

```
(.venv) root@vps-hostname:~/workspaces/your-workspace#
```

**Important:** You need to run this command every time you open a new PuTTY session and want to work with Python. To make it automatic, add it to your `.bashrc`:

```bash
echo 'cd ~/workspaces/your-workspace && source .venv/bin/activate' >> ~/.bashrc
```

### Step 5.3: Upgrade pip

```bash
pip install --upgrade pip
```

**What this does:** Updates the Python package installer to the latest version. Prevents warnings during package installation.

### Step 5.4: Install All Dependencies

```bash
pip install \
  exchangelib>=5.0.0 \
  telethon>=1.34.0 \
  anthropic>=0.42.0 \
  google-api-python-client>=2.100.0 \
  google-auth-httplib2>=0.2.0 \
  google-auth-oauthlib>=1.2.0 \
  python-docx>=1.0.0 \
  openpyxl>=3.1.0 \
  python-pptx>=0.6.0 \
  Pillow>=10.0.0 \
  playwright>=1.40.0 \
  weasyprint>=60.0 \
  scapy>=2.5.0 \
  yt-dlp>=2024.1.0 \
  youtube-transcript-api>=0.6.0 \
  replicate>=1.0.0 \
  pyyaml>=6.0.0 \
  python-dotenv>=1.0.0 \
  requests>=2.31.0 \
  beautifulsoup4>=4.12.0 \
  markdown>=3.5.0 \
  requests-ntlm>=1.3.0 \
  cryptography>=41.0.0 \
  dnspython>=2.4.0 \
  xlsxwriter>=3.1.0
```

**What this does:** Installs all the Python packages that the workspace scripts need. This will take 2-5 minutes as it downloads and compiles packages.

**What each package does (summary):**
- `exchangelib` -- Connects to Microsoft Exchange (corporate email/calendar)
- `telethon` -- Connects to Telegram
- `anthropic` -- Connects to Claude AI API
- `google-api-*` -- Connects to Google services (Contacts, Gmail)
- `python-docx`, `openpyxl`, `python-pptx` -- Creates Word, Excel, PowerPoint documents
- `playwright` -- Browser automation (screenshots, scraping)
- `weasyprint` -- Converts HTML to PDF
- `pyyaml`, `python-dotenv` -- Configuration file handling

### Step 5.5: Install Playwright Browsers

```bash
playwright install --with-deps chromium
```

**What this does:** Downloads the Chromium browser engine that Playwright uses for web automation. The `--with-deps` flag also installs system libraries that Chromium needs. This may take 1-2 minutes.

### Step 5.6: Save the Dependency List

```bash
pip freeze > requirements-vps.txt
```

**What this does:** Saves the exact versions of all installed packages to a file. Useful for recreating the environment later.

### Step 5.7: Verify Python Setup

```bash
python3 -c "import exchangelib; print('exchangelib OK')"
python3 -c "import telethon; print('telethon OK')"
python3 -c "import anthropic; print('anthropic OK')"
python3 -c "from playwright.sync_api import sync_playwright; print('playwright OK')"
```

**Expected:** Each command prints `OK`. If any command fails with `ModuleNotFoundError`, that package did not install correctly -- re-run the `pip install` command for that specific package.

---

## Part 6: Environment Variables (.env file)

The workspace scripts need API keys and credentials to connect to services like Claude, Telegram, Exchange email, etc. These are stored in a `.env` file that is never committed to git.

### Step 6.1: Create the .env File

Make sure you are in the workspace directory:

```bash
cd ~/workspaces/your-workspace
```

Open the editor:

```bash
mcedit .env
```

### Step 6.2: Type the Variables

Type the following into the editor, replacing the placeholder values with your real values. You can find your current values in the `.env` file on your Windows machine.

```
# Claude API (required)
ANTHROPIC_API_KEY=sk-ant-your-key-here

# Perplexity (recommended -- powers deep research)
PERPLEXITY_API_KEY=pplx-your-key-here

# Replicate (optional -- powers AI image generation)
REPLICATE_API_TOKEN=r8_your-token-here

# Context7 (optional -- powers library documentation lookup)
CONTEXT7_API_KEY=your-context7-key-here

# Telegram (required for Sentinel Telegram monitoring)
TELEGRAM_API_ID=your-telegram-api-id
TELEGRAM_API_HASH=your-telegram-api-hash
TELEGRAM_PHONE=+15550100100

# Exchange Email (required for Sentinel email monitoring)
EXCHANGE_EMAIL=ceo@31c.io
EXCHANGE_PASSWORD=<your-exchange-password>
EXCHANGE_SERVER=your-exchange-server
```

### Step 6.3: Save and Exit

- Press **F2** to save
- Press **F10** to exit

### Step 6.4: Secure the File

```bash
chmod 600 .env
```

**What this does:** Sets the file permissions so that only the root user can read it. Without this, other users on the server could potentially read your API keys and passwords.

### Step 6.5: Verify

```bash
python3 -c "
from dotenv import load_dotenv
import os
load_dotenv()
key = os.getenv('ANTHROPIC_API_KEY', 'NOT SET')
print(f'ANTHROPIC_API_KEY: {key[:12]}...' if key != 'NOT SET' else 'NOT SET')
"
```

**Expected:** Shows the first 12 characters of your API key, like `ANTHROPIC_API_KEY: sk-ant-a3x7k...`

---

## Part 7: Platform Configuration

The workspace needs slightly different settings on Linux vs Windows (different Python paths, different permissions). A setup script handles this automatically.

### Step 7.1: Run the Platform Setup Script

```bash
cd ~/workspaces/your-workspace
bash scripts/setup-platform.sh
```

**Expected output:**

```
Platform detected: Linux
Settings copied:   settings.local.linux.json -> settings.local.json
Done.
```

### Step 7.2: Verify

```bash
cat .claude/settings.local.json
```

**Expected:** You should see JSON content with `"python3"` in the hook commands (not `/mnt/c/Python314/python.exe` which is the Windows version).

---

## Part 8: Sentinel Service

Sentinel is the background comms monitor that checks email and Telegram every 15 minutes. On Linux, we run it as a **systemd service** -- this means it starts automatically when the VPS boots and restarts itself if it crashes.

### Step 8.1: Copy the Service File

```bash
cp ~/workspaces/your-workspace/reference/sentinel.service /etc/systemd/system/sentinel.service
```

**What this does:** Copies the Sentinel service definition to the system directory where Linux looks for service configurations.

### Step 8.2: Reload systemd

```bash
systemctl daemon-reload
```

**What this does:** Tells Linux to re-read all service files. Necessary after adding or changing a service.

### Step 8.3: Enable the Service (Auto-Start on Boot)

```bash
systemctl enable sentinel
```

**What this does:** Configures Sentinel to start automatically every time the VPS restarts. You will see a message about creating a symlink -- that's normal.

### Step 8.4: Start Sentinel Now

```bash
systemctl start sentinel
```

**What this does:** Starts Sentinel immediately (without waiting for a reboot).

### Step 8.5: Check That It's Running

```bash
systemctl status sentinel
```

**Expected:** You should see output like:

```
● sentinel.service - Sentinel -- CEO Comms Monitor
     Loaded: loaded (/etc/systemd/system/sentinel.service; enabled)
     Active: active (running) since ...
```

The key words are **"active (running)"** in green. If you see "failed" in red, see the Troubleshooting section.

### Step 8.6: View Sentinel Logs

To see what Sentinel is doing in real-time:

```bash
journalctl -u sentinel -f
```

**What this does:** Shows live log output from Sentinel. You will see it checking emails, connecting to Telegram, etc.

**To stop watching logs:** Press **Ctrl+C** (hold Control and press C). This stops the log viewer, not Sentinel itself.

To see today's logs (not live):

```bash
journalctl -u sentinel --since today
```

### Step 8.7: Other Useful Commands

```bash
systemctl restart sentinel     # Restart Sentinel (e.g., after config changes)
systemctl stop sentinel        # Stop Sentinel
systemctl status sentinel      # Check if running
```

**Important:** If you are also running Sentinel on the Windows machine, you will get duplicate alerts. Recommendation: run Sentinel on the VPS only (it's always on) and stop it on Windows.

---

## Part 9: Telegram Session Setup

Sentinel monitors Telegram messages, which requires an authenticated Telegram session. **Do NOT copy the Telegram session from your Windows machine** -- using the same session on two devices simultaneously will cause Telegram to invalidate one of them.

Instead, create a new session on the VPS.

### Step 9.1: Run the Telegram Authentication

Make sure you are in the workspace with the venv activated:

```bash
cd ~/workspaces/your-workspace
source .venv/bin/activate
```

Run the Sentinel test (which triggers Telegram login):

```bash
python3 scripts/sentinel.py --test
```

### Step 9.2: Enter the Phone Code

Telegram will send a verification code to your phone (via Telegram app or SMS). When prompted in the terminal, type the code and press Enter.

If Telegram asks for your 2FA password, enter that too.

### Step 9.3: Verify

After authentication, Sentinel should show a successful test cycle. The session file is saved in `.sessions/telegram/` and will be reused automatically for future runs.

Restart Sentinel to use the new session:

```bash
systemctl restart sentinel
```

---

## Part 10: Sync Setup

The VPS workspace stays in sync with your Windows workspace through GitHub. The flow is:

```
Windows (primary) --push--> GitHub --pull--> VPS (secondary)
```

You work on Windows, run `/backup` to push changes, and the VPS pulls them.

### Step 10.1: Test Manual Sync

```bash
cd ~/workspaces/your-workspace
bash scripts/vps-sync.sh
```

**Expected:** You should see log messages ending with `=== VPS Sync Complete ===`

If there are new changes on GitHub, they will be downloaded. If everything is already up to date, it will say so.

### Step 10.2: Set Up Automatic Sync (Optional)

To have the VPS automatically pull changes every 30 minutes:

```bash
crontab -e
```

**What this does:** Opens the cron (scheduled tasks) editor. If it asks which editor to use, choose `mcedit` (or the number next to it).

Add this line at the bottom of the file:

```
*/30 * * * * /root/workspaces/your-workspace/scripts/vps-sync.sh >> /root/vps-sync.log 2>&1
```

**What this line means:**
- `*/30 * * * *` -- Run every 30 minutes
- `/root/workspaces/your-workspace/scripts/vps-sync.sh` -- The script to run
- `>> /root/vps-sync.log` -- Append output to a log file
- `2>&1` -- Also capture error messages

Save (F2) and exit (F10).

### Step 10.3: Verify Cron is Set

```bash
crontab -l
```

**Expected:** Shows the line you just added.

### Step 10.4: Check the Sync Log

After 30 minutes have passed:

```bash
cat /root/vps-sync.log
```

**Expected:** Shows timestamped sync entries.

### Step 10.5: Sync Workflow (Day-to-Day)

1. **Work on Windows** as usual
2. **Run `/backup`** on Windows to push changes to GitHub
3. **VPS automatically pulls** every 30 minutes (if cron is set up)
4. **Or manually sync:** SSH into VPS and run `bash scripts/vps-sync.sh`
5. **If you work on VPS** (rare): commit and push from VPS, then pull on Windows before your next session

---

## Part 11: Verification Checklist

Run each command and check the expected output. All checks should pass before considering the deployment complete.

### Check 1: Git Clone is Complete

```bash
cd ~/workspaces/your-workspace && git status
```

**Expected:** `On branch main` -- no errors.

### Check 2: LFS Objects Present

```bash
git lfs ls-files | head -5
```

**Expected:** Shows file hashes and filenames (at least a few lines).

### Check 3: Python Virtual Environment Works

```bash
source .venv/bin/activate
python3 --version
```

**Expected:** Python version number, `(.venv)` in your prompt.

### Check 4: Key Python Packages Installed

```bash
python3 -c "import exchangelib; import telethon; import anthropic; print('All core packages OK')"
```

**Expected:** `All core packages OK`

### Check 5: Playwright Browser Installed

```bash
python3 -c "from playwright.sync_api import sync_playwright; print('Playwright OK')"
```

**Expected:** `Playwright OK`

### Check 6: Claude Code Installed

```bash
claude --version
```

**Expected:** Version number.

### Check 7: Claude Code Authenticated

```bash
claude -p "Reply with just the word VERIFIED"
```

**Expected:** Claude responds with `VERIFIED` (or similar).

### Check 8: .env File Loaded

```bash
python3 -c "
from dotenv import load_dotenv; import os; load_dotenv()
keys = ['ANTHROPIC_API_KEY','PERPLEXITY_API_KEY','TELEGRAM_API_ID','EXCHANGE_EMAIL']
for k in keys:
    v = os.getenv(k, 'NOT SET')
    print(f'{k}: {v[:8]}...' if v != 'NOT SET' else f'{k}: NOT SET')
"
```

**Expected:** Shows the first 8 characters of each key (not "NOT SET").

### Check 9: Platform Settings Correct

```bash
cat .claude/settings.local.json | grep python
```

**Expected:** Shows `python3` (not `/mnt/c/Python314/python.exe`).

### Check 10: Hooks Work (Session Start)

```bash
python3 .claude/hooks/session-start.py
```

**Expected:** JSON output or empty output (no Python errors).

### Check 11: Hooks Work (Sanitizer)

```bash
python3 scripts/sanitize-text.py --scan CLAUDE.md
```

**Expected:** Shows word count and "clean" or character findings.

### Check 12: Sentinel is Running

```bash
systemctl status sentinel
```

**Expected:** `active (running)` in green.

### Check 13: Sentinel Logs Are Being Written

```bash
journalctl -u sentinel --since "5 minutes ago" | head -20
```

**Expected:** Recent log entries from Sentinel.

### Check 14: Sync Script Works

```bash
bash scripts/vps-sync.sh
```

**Expected:** `=== VPS Sync Complete ===`

### Check 15: CRM Health Check

```bash
python3 scripts/crm-health.py 2>/dev/null | head -5
```

**Expected:** CRM contact health output (or an error about missing context files, which is normal if some files haven't synced yet).

---

## Part 12: Daily Operations Quick Reference

### Connect to VPS

1. Open PuTTY
2. Type your VPS IP, click Open
3. Login: `root` + your password
4. You're in the workspace automatically (if you set up `.bashrc` in Step 5.2)

### Manually Sync from GitHub

```bash
bash scripts/vps-sync.sh
```

### Check Sentinel Status

```bash
systemctl status sentinel
```

### View Sentinel Logs (Live)

```bash
journalctl -u sentinel -f
```

Press Ctrl+C to stop watching.

### Restart Sentinel

```bash
systemctl restart sentinel
```

### Run Claude Code Interactively

```bash
cd ~/workspaces/your-workspace
claude
```

### Run Claude Code with a One-Off Task

```bash
claude -p "Your task here"
```

### Edit a File

```bash
mcedit path/to/file.md
```

F2 = save, F10 = exit.

### Browse Files Visually

```bash
mc
```

F10 = exit.

### Check Disk Space

```bash
df -h /
```

### Check Memory Usage

```bash
free -h
```

### See Running Processes

```bash
htop
```

(Press `q` to exit. Install with `apt install -y htop` if not present.)

---

## Part 13: Troubleshooting

### Problem: "command not found"

**Example:** `claude: command not found`

**What it means:** The system doesn't know where to find that program.

**Fix:** The program might not be installed, or the PATH is wrong. Try:

```bash
source ~/.bashrc
```

If still not found, reinstall the program (go back to the relevant installation step).

### Problem: "Permission denied"

**What it means:** You don't have permission to run that command or access that file.

**Fix:** If you're running a script:

```bash
chmod +x scripts/vps-sync.sh
bash scripts/vps-sync.sh
```

If you need root access, make sure you're logged in as `root` (your prompt should end with `#` not `$`).

### Problem: Sentinel Won't Start

Check what went wrong:

```bash
systemctl status sentinel
journalctl -u sentinel --since "10 minutes ago"
```

**Common causes:**
- `.env` file missing or has wrong values -- check Part 6
- Python virtual environment not found -- check Part 5
- Missing Python packages -- run `pip install` again (Part 5.4)

**Fix and retry:**

```bash
systemctl restart sentinel
systemctl status sentinel
```

### Problem: Git Pull Fails

**"Authentication failed"**

Your GitHub token expired or is wrong. Generate a new one at https://github.com/settings/tokens and update your git credentials:

```bash
git remote set-url origin https://YOUR_TOKEN@github.com/mishahanin/your-workspace-workspace.git
```

**"Merge conflict"**

Someone changed the same file on both Windows and VPS. The sync script automatically stashes your local changes. To resolve:

```bash
git stash list                    # See stashed changes
git stash show -p stash@{0}      # See what was stashed
git stash drop stash@{0}         # Discard stashed changes (if not needed)
```

### Problem: PuTTY Disconnects

**"Network error: Software caused connection abort"**

This happens when your internet connection drops or the VPS restarts.

**Fix:** Simply reopen PuTTY and reconnect (Part 0, Step 0.2). Nothing is lost on the server. Sentinel continues running even when you're not connected.

**Prevention:** In PuTTY, before connecting, go to Connection > set "Seconds between keepalives" to `30`. This sends a tiny signal every 30 seconds to keep the connection alive.

### Problem: "No space left on device"

Your VPS disk is full.

```bash
df -h /
```

**Fix:** Delete old log files:

```bash
journalctl --vacuum-time=7d
rm -f /root/vps-sync.log
```

### Problem: Python Package Import Error

**"ModuleNotFoundError: No module named 'xxx'"**

The virtual environment isn't activated, or the package isn't installed.

**Fix:**

```bash
cd ~/workspaces/your-workspace
source .venv/bin/activate
pip install package-name
```

---

## Appendix A: Platform Differences

| Aspect | Windows (Primary) | VPS (Linux) |
|--------|-------------------|-------------|
| Workspace path | `c:\ai\claude-workspaces\ceo-main\` | `/root/workspaces/your-workspace` |
| Python binary | `/mnt/c/Python314/python.exe` | `.venv/bin/python3` |
| Python version | 3.14.3 | 3.12.x |
| Text editor | VS Code / Notepad | `mcedit` (Midnight Commander) |
| Sentinel | Manual start (`--daemon` flag) | systemd service (auto-start) |
| Claude Code | VS Code extension + terminal | Terminal only (headless) |
| Settings file | `settings.local.windows.json` | `settings.local.linux.json` |
| File manager | Windows Explorer | `mc` (Midnight Commander) |
| Connect via | Already there | PuTTY SSH |

---

## Appendix B: File Map

Key files on the VPS and what they do:

```
~/workspaces/your-workspace/
|
|-- .claude/
|   |-- settings.local.json       # Platform-specific hooks (auto-generated)
|   |-- settings.local.linux.json  # Linux template (tracked in git)
|   |-- hooks/
|   |   |-- session-start.py      # Runs at session start (CRM checks)
|   |   |-- post-write-sanitize.py # Scans files for hidden characters
|   |-- rules/                    # Auto-loaded rules (terminology, voice, etc.)
|   |-- skills/                   # All 45 skills (/prime, /osint, /sentinel, etc.)
|
|-- .env                          # API keys and secrets (NEVER in git)
|-- .sentinel/                    # Sentinel runtime state and logs
|-- .sessions/                    # Auth tokens (Google, Telegram)
|
|-- context/                      # Business context files
|-- crm/                          # Personal CRM contacts
|-- datastore/                    # Source-of-truth documents
|   |-- product/                  # Architecture, datasheets, hardware, sales
|   |-- presentations/            # Corporate presentations, pitch decks
|   |-- intelligence/             # Competitors, industry reports, use cases
|   |-- brand/                    # Templates (.dotx, .pptx) and assets (logos)
|   |-- financial/                # Investment terms, correspondence
|   |-- events/                   # MWC contact database, schedules
|   |-- _sync/                    # Auto-generated: calendar, emails
|
|-- outputs/                      # Generated outputs
|   |-- deliverables/             # Documents, presentations, proposals
|   |-- intel/                    # OSINT, briefs, newsletters, pulse
|   |-- content/                  # Images, LinkedIn drafts, follow-ups
|   |-- operations/               # Dashboard, meeting-prep, workspace docs
|
|-- reference/                    # Reference documents
|   |-- sentinel.service          # systemd service template
|   |-- vps-deployment-guide.md   # This guide
|
|-- scripts/
|   |-- sentinel.py               # Comms monitor (runs as service)
|   |-- sentinel_config.yaml      # Sentinel configuration
|   |-- vps-sync.sh               # Git pull + service restart
|   |-- setup-platform.sh         # OS detection + settings copy
|   |-- sanitize-text.py          # Hidden character scanner
|   |-- crm-health.py             # CRM contact health
|   |-- sync-exchange.py          # Exchange email/calendar sync
|   |-- generate-dashboard.py     # CEO morning dashboard
|
|-- CLAUDE.md                     # Master workspace instructions
|-- .gitignore                    # Files excluded from git
```

---

*Last updated: 2026-03-13*
