# GitHub and Unraid updates

The repository contains source code. GitHub Actions runs tests on each push and
pull request. Successful pushes to `main` publish the `latest` container image
to `ghcr.io/art101mark46/artflix:latest`. Failed tests leave the previous image available.
Tags such as `v0.4.0` additionally publish `:0.4.0`; each build also publishes a
`sha-COMMIT` tag for an exact rollback target. The image is built for Unraid's
64-bit Intel/AMD systems. The build ID appears under Settings → About.

GitHub supplies the publishing token automatically; do not put your movie API
keys into the repository or workflow. Runtime config, SQLite, backups, local
media and development tooling are excluded. The first upload must use only the
reviewed source files, not the whole working folder or old Git history.

After the first successful build, open the package settings on GitHub and make
the **container package** public if anonymous Unraid pulls are wanted. A public
repository does not necessarily mean its container package is public.

## Switch an existing Compose installation

Keep the live `.env`, share mappings, access modes and `/config` unchanged.
Change only these two lines in the live Compose service:

```yaml
image: ghcr.io/art101mark46/artflix:latest
pull_policy: missing
```

Do not overwrite the live Compose file
with the initial read-only example: live access settings may differ.

From its deployment directory:

```bash
docker compose -f compose.unraid.yml pull
docker compose -f compose.unraid.yml up -d
```

`pull` downloads an image; `up -d` recreates the service using it. Update between
scans and saves. Config remains on the mapped host appdata directory. A backup
before an update permits recovery if a later release changes database structure.
To roll back, use a previous version or `sha-COMMIT` image tag; database changes
may also require the matching config backup.

For a web button while retaining Compose, manage the existing stack in Unraid's
Compose Manager and use its update controls. Verify that plugin's current import
workflow before moving the stack. Alternatively use a native Unraid template
for native Docker-tab update buttons, preserving the same mounts and settings.
Do not have both mechanisms recreate the same container.

References:
- https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images
- https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility
- https://github.com/mstrhakr/compose_plugin
