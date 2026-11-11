# Unity Asset Library

A focused desktop library for Unity assets. macOS Apple Silicon (arm64) only. Python and dependencies are bundled.

## Install

### Homebrew

```sh
brew install zeemrx0/tap/unity-asset-library
```

Homebrew installs the prebuilt app into Applications and clears the download quarantine flag, so macOS shows no "unidentified developer" prompt. The app updates itself, so run `brew upgrade --cask unity-asset-library` only if you want Homebrew to track the version.

### Manual download

1. Download the DMG from the [releases page](https://github.com/zeemrx0/unity-asset-library/releases).
2. Drag Unity Asset Library into Applications.
3. Open it. The app is ad-hoc signed and not notarized, so macOS blocks the first launch.
4. On macOS 15 and later, open System Settings > Privacy & Security and choose Open Anyway. On macOS 14 and earlier, right-click the app and choose Open.

Install the app in Applications, not from the mounted DMG. The in-app updater needs a writable install location.

## Maintainers

Pushing a stable tag `v<version>` runs the release workflow. It tests, builds, publishes the GitHub release and then updates `Casks/unity-asset-library.rb` in `ZeeMRx0/homebrew-tap` from `packaging/homebrew/unity-asset-library.rb`. Beta tags never touch the tap.

The tap update needs the `HOMEBREW_TAP_TOKEN` repository secret: a fine-grained token with Contents read and write access to `ZeeMRx0/homebrew-tap`.
