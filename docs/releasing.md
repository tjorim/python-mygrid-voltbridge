# Releases

The version comes from git tags (hatch-vcs); there is no version to edit. The package is `mygrid-voltbridge` on PyPI and has not been published yet. Confirm name ownership before the first release.

1. Run `uv run ruff check . && uv run ruff format --check . && uv run ty check . && uv run pytest` and `uv build`.
2. If a device is reachable, run the live smoke tests: `VOLTBRIDGE_HOST=<ip> uv run pytest -m live`. They check the HTTP endpoints, the telegram CRC and the UDP broadcast against a real VoltBridge.
3. Configure PyPI trusted publishing for `.github/workflows/publish.yml` and the GitHub environment `pypi`. No API token is stored in source.
4. Push a tag `vX.Y.Z`. The workflow tests, builds, runs `twine check`, attests the distributions and publishes through the protected environment.
5. Once published, set the `mygrid-voltbridge==X.Y.Z` requirement in the `ha-mygrid-core` manifest (the umbrella Home Assistant integration; the HACS repo `ha-mygrid` vendors the library instead).
