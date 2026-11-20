# Minify for macOS

Install Dota 2 in native macOS Steam. Minify includes Python, its UI, stock mods, plugins and themes. You don't need a compiler, system Perl, zsh, Homebrew, CrossOver, NotProton or Windows Steam.

## Install the app

1. Sign in to GitHub and open **Actions → Build macOS app and portable** in the repository. Under **Artifacts** on a successful run, download **Minify-app-macos-arm64.zip** for Apple Silicon or **Minify-app-macos-x86_64.zip** for Intel. These are [GitHub Actions artifacts](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts), not files on the public release page.
2. Unzip the archive in Finder. Move **Minify.app** to `~/Applications` or `/Applications` and double-click it.
3. Open Minify. With Dota installed and an internet connection, it sets up missing dependencies without asking you to confirm downloads or sign in to Steam. First launch can take a few minutes. Once the window opens, the **Terminal** tab shows what setup did. The hammer button retries setup.

| Component | What happens on first launch |
| --- | --- |
| Wine | Reuses standalone Wine, or downloads the pinned [Wine Stable build](https://github.com/Gcenx/macOS_Wine_builds/releases/tag/11.0_1) (177 MB) for your user. Apple Silicon may also need [Rosetta](https://support.apple.com/en-us/102527). |
| Workshop compiler | Reuses tools in the workspace, or downloads the 72 MB [community compiler archive](https://github.com/Dota-Modding-Community/workshoptools/releases/tag/v1.0.1). It doesn't silently import files from another installation. |
| DirectX 11 support | The community archive has no Vulkan renderer. Minify downloads [DXVK-macOS](https://github.com/Gcenx/DXVK-macOS/releases/tag/v1.10.3-20230507-repack) (2.8 MB) and puts two DLLs beside that compiler, not in system Wine. A full tools install with Valve’s Vulkan renderer doesn't need them. |
| Patch tools | On opening the app, Minify fetches missing ripgrep and, when the compiler is ready, Source2Viewer. No separate installer. |

The Wine, compiler and DXVK archives are checked against pinned SHA-256 hashes. On a fresh setup the launcher compiles a Panorama script and checks for `check.vjs_c` before enabling compiler mods. Later launches reuse the installed files. This sets up the compiler Minify uses, not the entire Workshop editor suite.

Every build is ad-hoc signed, not notarized. macOS may require approval for Minify itself. Before running downloaded Wine, Minify asks Gatekeeper whether it can run. If the check fails, Minify leaves Wine installed but doesn't start it. Open Minify’s **Terminal** tab for the exact Wine path. If you trust that copy, follow [Apple’s per-app approval instructions](https://support.apple.com/en-us/102445), then click the hammer in Minify. Minify does not grant that approval or turn off Gatekeeper.

## Apply a patch

Quit Dota. In Minify, choose your output language and a mod, then click **Patch**. **Stat Site Buttons** exercises the compiler: check `Workspace/logs/resourcecompiler.txt` for compiled assets and zero failures, and look for a fresh `game/dota_<locale>/pak66_dir.vpk` in the Dota installation. A successful setup check isn't proof that the game loaded a patch.

Add the language flag Minify prints to **Steam → Dota 2 → Properties → Launch Options** (for Russian output: `-language russian`). Minify doesn't restart Steam or set launch options on macOS.

## Keep your mod list or start fresh

The Finder app stores its data in `~/Library/Application Support/Dota2-Minify/`. Inside it, `Workspace/` holds settings, mods, logs, the Wine prefix and Workshop files. A Wine runtime downloaded by the launcher lives alongside `Workspace/`. Replacing the app doesn't reset either one.

- **Save selections:** quit Minify and copy `Workspace/config/mods.json` somewhere outside that folder. For the portable build, save `Minify/config/mods.json` instead. The list contains enable/disable settings, not the mod files. Keep separate copies of any custom mods you can't download again.
- **Start fresh:** with Minify closed, move the whole `Dota2-Minify` data folder aside and open the app. A system-wide Wine installation will still be detected. This reset doesn't uninstall a VPK that was already applied to Dota.
- **Restore later:** reinstall any missing mods. Quit Minify, copy the saved list back to `Workspace/config/mods.json`, then reopen it.

## Other ways to launch

The portable CI ZIP (`Minify-macos-<arch>.zip`) runs from a writable folder. Extract the whole folder and double-click **Start Minify.command**.

For the Finder app, these commands assume you installed it in `~/Applications`:

```sh
# Regular mods only: skip Wine and Workshop setup.
"$HOME/Applications/Minify.app/Contents/MacOS/Minify" --skip-setup

# On first setup, import your own complete Windows Workshop Tools instead of downloading.
MINIFY_DOTA_WINDOWS='/Volumes/Windows Games/steamapps/common/dota 2 beta' "$HOME/Applications/Minify.app/Contents/MacOS/Minify"
```

If the private tools are incomplete, quit Minify and move `Workspace/config/rescomproot` aside before retrying. Setup won't replace it. The packaged app patches with the same private compiler it tested, even if the Steam library also contains a Windows compiler. If setup fails, the Terminal tab shows the failure and log path without opening a dialog; regular mods remain available.

## Build and distribution

GitHub Actions builds for Apple Silicon and Intel on pushes to `macos` or `main` and on pull requests targeting either branch. Each architecture produces an app ZIP and a portable ZIP. CI tests the setup scripts with fixtures, launches both extracted builds with a temporary home, and checks the app signature before and after launch. It does not sign in to Steam, download Valve’s tools, or patch a real game.

Distribute the clean CI artifact, not a used workspace. The app doesn't ship Valve’s compiler or a copy of Wine. A public download isn't permission to redistribute Valve’s binaries; see the [Steam Subscriber Agreement §2](https://store.steampowered.com/subscriber_agreement/#2).
