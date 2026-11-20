# Minify for macOS

## Install and patch

1. Install Dota 2 in macOS Steam. On GitHub, sign in and open **Actions → Build macOS app and portable**. From a successful run’s **Artifacts**, get `Minify-app-macos-arm64.zip` (Apple silicon) or `Minify-app-macos-x86_64.zip` (Intel).
2. Unzip, move **Minify.app** to **Applications**, and open it. Minify downloads missing tools, including Wine and the [Workshop compiler](https://github.com/Dota-Modding-Community/workshoptools/releases/tag/v1.0.1). First launch can take a few minutes. No separate Steam login or CrossOver needed.
3. Quit Dota, select your output language and mods, then click **Patch**. Add the flag Minify prints (e.g. `-language russian`) to **Steam → Dota 2 → Properties → Launch Options**. Relaunch Dota, not Steam.

## Questions and answers

### macOS says it can’t verify Minify. What do I do?

1. Double-click **Minify.app** in Finder and dismiss the verification alert.
2. Open **System Settings → Privacy & Security** and scroll down to **Security**.
3. Click **Open Anyway** next to **Minify**, then **Open**.

### What if Gatekeeper names Wine Stable instead?

Open the `Wine Stable.app` shown in Minify’s **Terminal** tab using Finder. Follow the same steps in **Privacy & Security** for **Wine Stable**, then click the **hammer** in Minify to retry setup.

### Why is my Apple silicon Mac asking for Rosetta?

Wine uses Intel code. If macOS prompts, install [Rosetta from Apple](https://support.apple.com/en-us/102527), then retry with the hammer.

### Why are some mods greyed out?

Mods that require the Workshop compiler stay locked until it compiles a test file. After the window opens, check Minify’s **Terminal** tab for the error and setup log path. Regular mods still work.

### Why didn’t the patch show up in Dota?

Check the launch flag first: even English output uses `-language dutch`. Look for a fresh `game/dota_<language>/pak66_dir.vpk` in your Dota installation, then relaunch the game.

### How do I start fresh without losing my mod list?

Quit Minify and move `~/Library/Application Support/Dota2-Minify/` aside in Finder. Reopen Minify for a fresh setup. To restore selections, reinstall your mods, quit Minify, and copy the backup’s `Workspace/config/mods.json` to the same path in the new folder. That file saves selections, not custom mods. Existing Dota patches stay installed.
