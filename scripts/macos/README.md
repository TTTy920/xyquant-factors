# Maintaining the macOS package

Edit the shared tool declarations and workflows in `client/` and the shared Skills in `plugins/xyquant-factors/skills/`. The Mac-specific factor-count Skill stays in the Mac plugin directory. The Mac packager stages the same client, applies `client.patch` for system-specific operations, takes the version from the Mac manifest and adds `macos_platform.py`. OAuth scopes, the remote endpoint, tool names and arguments, retry rules, download layout and the Barra formula have no separate Mac implementation. The Windows binary remains tied to its own recorded build inputs; rebuild that release separately when preparing a Windows update.

For packagers: Python 3.9+, git and zstd are needed. Run from the repository root:

```sh
python3 scripts/macos/package.py
```

The packager verifies the public, pinned inputs in `runtime-lock.json`, renders both manifest formats and copies only the listed shared Skill files and Mac artifacts. It puts matching self-contained packages in the GitHub marketplace plugin directory and in `.work/macos/xyquant-factors-macos-<version>.zip` (plus its SHA-256 sidecar). The ZIP has the same plugin at its root. Use `--cache <maintainer-cache>` for pre-downloaded inputs and `--out <release-directory>` to change the output. Building the files does not install or launch the plugin. Keep versioned Git tags and release assets intact when shipping updates.

Both architecture archives contain CPython 3.12 and pinned runtime dependencies. On x86_64, PyPI did not publish a compatible cryptography wheel at the pinned version: its conda-forge build and the exact OpenSSL shared libraries are included instead. Upstream notices are retained. When dependencies change, review macOS platform tags and dynamic library paths for both platforms, update asset URLs and digests, and regenerate rather than replacing only one architecture's files.

The launcher chooses the native architecture, checks the archive digests, and atomically prepares a per-user, per-version runtime under Application Support; the installation does not fetch executable code at runtime. The same interpreter is used for login, download workers and local Barra when requested. Tokens are stored in macOS Keychain. No customer files or credentials are part of the public repository or release.
