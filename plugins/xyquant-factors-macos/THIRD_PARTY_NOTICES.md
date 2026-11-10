# Runtime notices

XYQuant's bridge and Skills are distributed under the included MIT license. The embedded CPython interpreter and third-party modules retain their respective licenses.

- The two `python-*.tgz` archives are unmodified [python-build-standalone](https://github.com/astral-sh/python-build-standalone/releases/tag/20260825) CPython 3.12.14 distributions. The distributions include Python's and bundled dependencies' license notices.
- The two `packages-*.tgz` archives retain Python package metadata, licenses and resources, including MCP, Pydantic, httpx2 and truststore. Each architecture uses the same versions of the client's Python runtime dependencies. On Intel, the cryptography Python package (50.0.1) and its shared OpenSSL libraries (3.5.9) come from conda-forge; their additional notices are under `share/licenses/cryptography` and `share/licenses/openssl` in the Intel package.
- The exact upstream URLs, filenames and SHA-256 digests are listed in the repository's [runtime lock](https://github.com/TTTy920/xyquant-factors/blob/macos-v0.3.0-rc.1/scripts/macos/runtime-lock.json).
