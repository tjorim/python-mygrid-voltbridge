# Contributing

Follow the development and validation commands in README.md when changing code.

## Documentation ownership

Update documentation alongside behavior changes. Keep Python examples, protocol
references, device prerequisites and library validation in the Python library;
keep HA setup, configuration, entities, Energy dashboard instructions, automation
examples and integration acceptance in the HA integration. Cross-device
introductions and website navigation belong in `custom-mygrid`.

The documentation website imports authoritative Markdown at pinned commits.
Edit the original source, not a website snapshot. Preserve existing headings
where possible because published pages may link to their anchors. Use relative
Markdown links for files in this repository. Link to another repository's source
rather than copying its detailed instructions.

After a documentation change merges, the hub's scheduled update workflow proposes
a snapshot refresh. To publish sooner, run that workflow manually or update the
pins with `pnpm docs:update` in `custom-mygrid`. The website changes only after
the snapshot-update PR is reviewed and merged. See the
[website publishing guide](https://tjorim.github.io/custom-mygrid/docs/website/#upstream-documentation-ownership).
