# Things Sara needs to complete

Steps only you can do (accounts, passwords, decisions). Claude keeps this list up to date;
tick items off as you finish them.

## Email (real sending)
- [ ] Create a dedicated sender Gmail account for the app (e.g. `neulearning.app@gmail.com`).
- [ ] Turn on 2-Step Verification for it, then create an **App Password** named `NeuLearning`.
- [ ] Add the SMTP lines to `.env` (template in `.env.example`). Never paste the password in chat.
- [ ] Restart the dev server so it reads the new `.env`.
- [ ] Decide: should Claude add **email verification** at signup and a **"Send me a test email"**
      button before real emails go out? (Recommended: anyone can currently sign up with someone
      else's address, and many users are minors.)
- [ ] Later, for launch: move from Gmail to an email service with your own domain
      (Resend, Postmark, SendGrid or Amazon SES).

## App Store / Google Play (when you're ready)
- [x] Pick a path: installable web app first, then the store wrapper (chosen 2026-11-18).
- [ ] Choose a hosting service for the backend (e.g. Render, Railway or Fly.io) and create an account.
      It must offer a **persistent disk** (the database and uploaded pictures live on it).
- [ ] Buy a domain name (e.g. `neulearning.app`), then set `APP_BASE_URL` to it.
- [ ] On the host, set these settings (Claude can walk you through it once you pick a host):
      `NEULEARNING_ENV=production`, `NEULEARNING_DATA_DIR=<the persistent disk path>`,
      `SECRET_KEY=<a long random value>`, `ANTHROPIC_API_KEY`, `APP_BASE_URL`, and the SMTP lines.
- [ ] Set up a scheduled job on the host that runs `flask --app app notify` every 15 minutes
      (sends practice reminders and tutor alerts).
- [ ] After it's live: open it on your iPhone in Safari → Share → **Add to Home Screen**, and on an
      Android phone in Chrome → **Install**. Check it opens full screen and shows the offline page
      in airplane mode.
- [ ] Apple Developer Program: $99/year (needed to publish on the App Store). Needs a Mac with Xcode.
- [ ] Google Play Console: $25 one-time (needed to publish on Google Play).
- [ ] Write (or approve a draft of) a **privacy policy** and **terms of use**. Required by both stores,
      and important because users include children (COPPA in the US for under-13s).
- [ ] Decide the minimum age, and whether under-13s need a parent's permission to sign up.
