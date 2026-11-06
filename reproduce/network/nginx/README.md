# Nginx reverse proxy for Voxl

`voxl.conf` is the repository copy of the machine's Nginx site configuration, with the host-specific IP generalized to `server_name _;`. It includes the fix for large uploads: no Nginx request-body cap and streaming proxying to Voxl instead of buffering the complete body first.

## Install/update

Back up the active configuration before replacing it. On Debian/Kali, for example:

```sh
# Run from the workstation-rebuild repository root, after preparing target TLS.
# Back up any existing site before replacing it; do not blindly replace live sites.
sudo install -m 644 reproduce/network/nginx/voxl.conf /etc/nginx/sites-available/voxl
# Review/enable this site and remove conflicting default sites deliberately.
sudo nginx -t
# setup.sh intentionally masks Nginx. Only after the site, TLS and firewall are ready:
sudo systemctl unmask nginx.service
sudo systemctl enable --now nginx.service
```

Make sure the TLS certificate and key exist at `/etc/nginx/ssl/voxl.crt` and `/etc/nginx/ssl/voxl.key`, or edit those paths. The config proxies to Voxl on `127.0.0.1:6901`; change the upstream if Voxl listens elsewhere. Keep an appropriate `server_name` for the host.

`client_max_body_size 0` means unlimited request size. Voxl itself also has `--max-upload-mb` (0 means unlimited); if a limit is desired, set a deliberate size in both Nginx and Voxl. Unlimited uploads can consume disk, so only use this where the service is appropriately access-controlled and storage is monitored.
