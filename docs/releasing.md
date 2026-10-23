# Releases

The package is `mygrid-voltbridge` on PyPI; it has not been published yet. Confirm name ownership before the first release.

1. Run `uv run ruff check . && uv run mypy && uv run pytest` and `uv build`; review the built files.
2. Update `project.version` and refresh the lockfile. The tag must match the version, e.g. `v0.1.1`.
3. Configure PyPI trusted publishing for `.github/workflows/publish.yml` and the GitHub environment `pypi`. No API token is stored in source.
4. Publish a GitHub release for the tag, or dispatch the workflow manually for an existing tag. It tests, builds, runs `twine check`, then publishes through the protected environment.
5. Once published, change `ha-mygrid-voltbridge`'s manifest requirement from the GitHub tag to `mygrid-voltbridge==<version>`.
