#!/bin/bash
# Package a clean PyInstaller onedir. Never archive a folder Minify has already used.
set -euo pipefail
cd "$(dirname "$0")/../.."
arch=${1:?expected arm64 or x86_64}
[[ $arch = arm64 || $arch = x86_64 ]] && [[ $(uname -m) = "$arch" ]] || exit 1
dist="$PWD/scripts/dist"
src="$dist/Minify"
[[ -d $src && ! -L $src ]] || exit 1
shopt -s dotglob
for item in "$src"/*; do
    case "${item##*/}" in
        Minify|_internal|mods|plugins|themes|README.md|LICENSE) ;;
        *) echo "Refusing unexpected release content: $item" >&2; exit 1 ;;
    esac
done
unexpected=$(find "$src" \( -iname '*.exe' -o -iname '*.dll' -o -iname rescomproot -o -iname 'steamcmd*' -o -iname 'DepotDownloader*' \
    -o -name .DS_Store -o -name '._*' -o -name __MACOSX -o -iname loginusers.vdf -o -iname localconfig.vdf \
    -o -iname steamapps -o -iname drive_c -o -iname wine -o -iname wine64 -o -iname wineserver -o -iname '*.tar.xz' \
    -o \( -iname '*.zip' ! -path "$src/_internal/base_library.zip" \) -o -iname '*.vpk' \) -print -quit)
[[ -z $unexpected ]] || { echo "Refusing to ship runtime or game content: $unexpected" >&2; exit 1; }
while IFS= read -r -d '' link; do
    case "$(realpath "$link")" in "$src"/*) ;; *) echo "Refusing external link: $link" >&2; exit 1 ;; esac
done < <(find "$src" -type l -print0)
for asset in _internal/ui/index.html _internal/bin/settings.json _internal/locales/en.json \
    'mods/#base/manifest.json' themes/light.css plugins/community/ui/index.html plugins/d2pfx/ui/index.html LICENSE README.md; do
    test -s "$src/$asset"
done
# The cross-platform builder copies entire mods and plugins directories, including ignored local mods.
python3 - "$src" <<'PY'
import os
from pathlib import Path
import subprocess
import sys

src = Path(sys.argv[1])
tracked = {}
for entry in subprocess.check_output(["git", "ls-files", "-sz"]).split(b"\0"):
    if entry:
        meta, name = entry.split(b"\t", 1)
        tracked[os.fsdecode(name)] = meta.split()[1].decode()
assets = {src / name: name for name in ("README.md", "LICENSE")}
for packaged, source in (("mods", "Minify/mods"), ("plugins", "Minify/plugins"), ("themes", "Minify/themes"),
                         ("_internal/bin", "Minify/bin"), ("_internal/locales", "Minify/locales")):
    folder = src / packaged
    for file in folder.rglob("*"):
        if not file.is_file():
            continue
        relative = file.relative_to(folder)
        path = f"{source}/{relative}"
        if packaged == "plugins" and relative.parts[1:] == ("ui", "index.html") and f"{source}/{relative.parts[0]}/src/index.html" in tracked:
            continue  # Vite generates this page from the tracked plugin.
        assets[file] = path
        if path not in tracked:
            sys.exit(f"Refusing untracked release asset: {path}. Package from a clean checkout.")
blobs = subprocess.check_output(["git", "hash-object", "--no-filters", "--", *assets]).decode().splitlines()
for file, blob in zip(assets, blobs):
    if tracked.get(assets[file]) != blob:
        sys.exit(f"Refusing modified release asset: {file}. Package from a clean checkout.")
PY
lipo "$src/Minify" -verify_arch "$arch"
[[ ! -e $dist/Minify.app && ! -L $dist/Minify.app ]] || { echo "Move the previous Minify.app before packaging." >&2; exit 1; }

tmp=$(mktemp -d "$dist/.macos-package.XXXXXXXX")
trap 'rm -rf "$tmp" || echo "Could not remove temporary packaging folder: $tmp" >&2' EXIT
portable="$tmp/Minify"
bundle="$tmp/Minify.app"
contents="$bundle/Contents"
resources="$contents/Resources"
portable_zip="Minify-macos-$arch.zip"
app_zip="Minify-app-macos-$arch.zip"
ditto --norsrc "$src" "$portable"
install -m 755 "scripts/macos/Start Minify.command" scripts/macos/{setup-workshop,prepare-workshop,check-workshop,fetch-verified} "$portable/"
install -m 644 docs/macos.md scripts/macos/{prepare-wine,prepare-dxvk} "$portable/"
mkdir -p "$portable/launcher-bin"
install -m 755 scripts/macos/launcher-bin/wine "$portable/launcher-bin/wine"
ditto -ck --norsrc --keepParent "$portable" "$tmp/$portable_zip"

mkdir -p "$contents/MacOS" "$resources/defaults" "$resources/macos"
ditto --norsrc "$src/Minify" "$contents/MacOS/Minify"
ditto --norsrc "$src/_internal" "$contents/Frameworks"
# codesign treats everything in Frameworks as code. Keep Python's data reachable through relative links.
for item in "$contents/Frameworks"/*; do
    [[ -n $(find "$item" \( -name '*.so' -o -name '*.dylib' -o -name '*.framework' \) -print -quit) ]] && continue
    name=${item##*/}
    mv "$item" "$resources/$name"
    ln -s "../Resources/$name" "$item"
done
# codesign mistakes python3.13 for a nested bundle. Keep Python's import path as a symlink.
for item in "$contents/Frameworks"/python3.*; do
    [[ -d $item && ! -L $item ]] || continue
    name=${item##*/}
    mv "$item" "$contents/Frameworks/${name//./__dot__}"
    ln -s "${name//./__dot__}" "$item"
done
for item in mods plugins themes README.md LICENSE; do ditto --norsrc "$src/$item" "$resources/defaults/$item"; done
for item in setup-workshop prepare-workshop check-workshop fetch-verified prepare-wine prepare-dxvk launcher-bin macos.md; do
    ditto --norsrc "$portable/$item" "$resources/macos/$item"
done

version=$(sed -nE 's/^VERSION = "([0-9]+(\.[0-9]+){0,2})"$/\1/p' Minify/core/base.py)
[[ -n $version ]] || { echo "Expected numeric VERSION in Minify/core/base.py." >&2; exit 1; }
sips -s format icns Minify/bin/images/favicon.png --out "$resources/Minify.icns" >/dev/null
cat > "$contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
    <key>CFBundleIdentifier</key><string>org.dota2minify.Minify</string>
    <key>CFBundleName</key><string>Minify</string>
    <key>CFBundleExecutable</key><string>Minify</string>
    <key>CFBundleIconFile</key><string>Minify.icns</string>
    <key>CFBundleVersion</key><string>$version</string>
    <key>CFBundleShortVersionString</key><string>$version</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
    <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
plutil -lint "$contents/Info.plist"
codesign --force --sign - "$bundle"
ditto -ck --norsrc --keepParent "$bundle" "$tmp/$app_zip"

# Verify both archives, not the staging trees; discovery runs even for --version.
mkdir "$tmp/extracted"
ditto -xk "$tmp/$portable_zip" "$tmp/extracted"
ditto -xk "$tmp/$app_zip" "$tmp/extracted"
export HOME="$tmp/home" CFFIXED_USER_HOME="$tmp/home"
dota="$HOME/Library/Application Support/Steam/steamapps/common/dota 2 beta/game/bin/osx64/dota2.app/Contents/MacOS"
mkdir -p "$dota"
touch "$dota/dota2"
portable="$tmp/extracted/Minify"
for entry in 'Start Minify.command' setup-workshop prepare-workshop check-workshop fetch-verified launcher-bin/wine Minify; do test -x "$portable/$entry"; done
"$portable/Start Minify.command" --skip-setup --version | grep -Fx "$version"
app="$tmp/extracted/Minify.app"
lipo "$app/Contents/MacOS/Minify" -verify_arch "$arch"
codesign --verify --deep --strict "$app"
"$app/Contents/MacOS/Minify" --version | grep -Fx "$version"
workspace="$HOME/Library/Application Support/Dota2-Minify/Workspace"
[[ ! -e "$workspace/Minify" && ! -e "$workspace/_internal" ]]
cmp "$app/Contents/Resources/defaults/themes/light.css" "$workspace/themes/light.css"
test -s "$workspace/config/minify_config.json"
# A startup that writes into its own bundle invalidates its seal.
codesign --verify --deep --strict "$app"
mv "$bundle" "$dist/Minify.app"
mv "$tmp/$portable_zip" "$tmp/$app_zip" "$dist/"
