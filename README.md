---
title: Reservation Turnover Tool
emoji: 🏠
colorFrom: blue
colorTo: indigo
sdk: gradio
app_file: app.py
pinned: false
---

## For the administrator

**Visibility** — Set the Space to **Public**. The password gate (not HF private mode) controls who can use it. Private mode would require every user to have a Hugging Face account; the password approach does not.

**Secrets** — In the Space → Settings → Variables and secrets, add two secrets:

| Name | Value |
|---|---|
| `APP_USER` | A short shared username (e.g. `rc`) |
| `APP_PASS` | A strong shared password |

The Space will refuse to start if either secret is missing.

**Sharing with users** — Send them the Space URL along with the username and password. Nothing else is needed on their end.

**Changing the password** — Edit the `APP_PASS` secret in Space Settings and restart the Space. No code change required.

**Adding a new property's city** — Find the exact property name in the output CSV's **Property** column, append a row to `property_to_city.csv`, and push the file to the Space. It rebuilds automatically.

---

## For users

1. Open the link provided to you.
2. Enter the username and password provided by your administrator.
3. Upload the **Check-out CSV** and the **Check-in CSV** from your Guesty export, click **Process**, and download the result.
