# News image storage

The API stores one News image under `/var/www/fover/uploads/news` and stores
its absolute public URL in `news.image_url`. The local and production Compose
overlays share that container path while choosing different persistent storage
sources and public URLs.

## Local Docker Desktop

The local overlay adds the Compose named volume
`<compose-project>_news_uploads` and a small Nginx container. The API mounts the
volume read-write at `/var/www/fover/uploads`; Nginx mounts it read-only at the
same path. Nginx serves `/uploads/news/` on port 8081 by default.

Before the first local API recreation, inspect the existing container's
`/var/www/fover/uploads/news` directory. A mount hides the container's previous
directory contents. Copy any files found into the named volume without
overwriting conflicts. In the current development environment the directory
was empty.

Start or update only the API and local image server with:

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build api nginx
```

Set `LOCAL_NGINX_PORT` before running Compose to use another free local port.
Set `LOCAL_PUBLIC_HOST` when clients need a host/LAN address instead of
`localhost`. The default image URL is
`http://localhost:8081/uploads/news/`.

Inspect the volume and its files:

```powershell
docker volume inspect fover_backend_news_uploads
docker run --rm --entrypoint find -v fover_backend_news_uploads:/uploads:ro nginx:stable-alpine /uploads/news -maxdepth 1 -type f -print
```

Back up the volume to a Docker Desktop-shared host directory (replace
`D:/safe-backups` with the intended destination):

```powershell
docker run --rm -v fover_backend_news_uploads:/source:ro -v D:/safe-backups:/backup nginx:stable-alpine tar -czf /backup/news-uploads.tgz -C /source .
```

Ordinary `docker compose down` preserves named volumes. Do not use
`docker compose down -v` as a restart or update command; it removes named
volumes.

## Production Linux preparation

The production overlay is a template, not an instruction to deploy. After the
active server configuration, actual source directory, Nginx worker identity,
and permissions have been verified, set `FOVER_UPLOADS_HOST_DIR` to the
approved persistent Linux host directory (proposed: `/var/www/fover/uploads`)
and render the merged configuration:

```sh
FOVER_UPLOADS_HOST_DIR=/var/www/fover/uploads \
  docker compose -f docker-compose.yml -f docker-compose.production.yml config --quiet
```

The overlay bind-mounts that host directory at
`/var/www/fover/uploads` in the API container and sets the production public
URL. Native Nginx must serve the same host directory using the
`nginx/uploads.conf` location snippet. Include the snippet inside the
appropriate HTTPS `server` block; do not assume the repository snippet is
already active. Review the existing server configuration and preserve its
current TLS and API proxy directives. Because Nginx runs on the host, the
verified bind source must match the host path in the Nginx alias. The
repository alias expects `/var/www/fover/uploads/news`; if the verified host
directory differs, update the alias as part of the separately approved
production preparation.

Before deployment, verify the actual Nginx worker user/group on the host with:

```sh
ps -eo user,group,args | grep '[n]ginx: worker process'
```

The API container runs as UID 1000. Provision the verified host directory so
UID 1000 can create/write files and the actual Nginx worker group can traverse
directories and read files. Do not use world-writable permissions. Confirm
that newly created files inherit readable group/ACL permissions. Also run
`nginx -t` against the active configuration before any separately approved
reload.

The production host directory and active Nginx configuration were not
inspected by this repository-only change. The overlay must not be activated
until those facts and the existing-image source have been checked.

## Existing image migration and backup

For production, identify the actual upload path in every API container and
host mount before copying. Back up the database and all candidate image
directories. Match stored production URLs to filenames, copy (do not move)
without overwriting, compare counts and checksums, and verify existing public
URLs. Keep the source files until the new storage has been confirmed. Restore
the database and image files from matching recovery points: database-only
restores leave broken image URLs, while files-only restores leave orphaned
files.

No production images were inspected or moved by this change.
