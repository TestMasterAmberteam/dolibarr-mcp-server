#!/usr/bin/env bash
# Install a GitHub release without modifying the active environment.
set -Eeuo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
export GIT_TERMINAL_PROMPT=0
export UV_PYTHON_DOWNLOADS=never
umask 022

readonly root=/opt/dolibarr-mcp
readonly repository=https://github.com/TestMasterAmberteam/dolibarr-mcp-server.git
readonly config=/etc/dolibarr-mcp/config.toml
tag="${1:-latest}"
if (( EUID != 0 )); then
    echo "Run this deployment helper as root." >&2
    exit 1
fi
if (( $# > 1 )) || [[ ! "$tag" =~ ^(latest|v[0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
    echo "Usage: dolibarr-mcp-deploy [latest|vMAJOR.MINOR.PATCH]" >&2
    exit 2
fi

install -d -m 0755 "$root" "$root/releases"
exec 9>"$root/deploy.lock"
flock -n 9 || { echo "Another deployment is running." >&2; exit 1; }
test -r "$config"

# Resolve latest once and require a published, non-draft, stable GitHub release.
tag=$(python3 - "$tag" <<'PY'
import json
import re
import sys
import urllib.request

selected = sys.argv[1]
suffix = "latest" if selected == "latest" else "tags/" + selected
url = "https://api.github.com/repos/TestMasterAmberteam/dolibarr-mcp-server/releases/" + suffix
request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
with urllib.request.urlopen(request, timeout=30) as response:
    release = json.load(response)
tag = release["tag_name"]
if release.get("draft") or release.get("prerelease") or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
    raise SystemExit("Expected a stable vMAJOR.MINOR.PATCH GitHub release")
if selected != "latest" and tag != selected:
    raise SystemExit("GitHub returned a different release tag")
print(tag)
PY
)
if [[ ! -d "$root/repository.git" ]]; then
    git clone --bare "$repository" "$root/repository.git"
fi
[[ "$(git --git-dir="$root/repository.git" remote get-url origin)" == "$repository" ]]
# No force: a moved existing tag is an error.
git --git-dir="$root/repository.git" fetch origin 'refs/tags/*:refs/tags/*'
commit=$(git --git-dir="$root/repository.git" rev-parse --verify "refs/tags/$tag^{commit}")
previous=$(readlink -f "$root/current" || true)
release=$(mktemp -d "$root/releases/$tag-${commit:0:12}-XXXXXX")
chmod 0755 "$release"
git --git-dir="$root/repository.git" archive "$commit" | tar -x -C "$release"
cd "$release"
python3 - "$tag" <<'PY'
import sys
import tomllib
from pathlib import Path

version = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
if "v" + version != sys.argv[1]:
    raise SystemExit("Release tag does not match package version")
PY
uv sync --locked --no-dev --no-editable --python /usr/bin/python3
.venv/bin/python - "$config" "$tag" <<'PY'
import sys
from dolibarr_mcp import __version__
from dolibarr_mcp.config import load_settings

load_settings(sys.argv[1])
if "v" + __version__ != sys.argv[2]:
    raise SystemExit("Installed package version does not match release tag")
print("Configuration and installed version validated.")
PY
printf 'tag=%s\ncommit=%s\ninstalled_at=%s\n' "$tag" "$commit" "$(date -u +%FT%TZ)" > DEPLOYMENT
.venv/bin/dolibarr-mcp --version

healthy() {
    local attempt code
    for ((attempt = 0; attempt < 20; attempt++)); do
        if systemctl is-active --quiet dolibarr-mcp.service &&
           curl -fsS --max-time 3 http://127.0.0.1:8080/health/ready >/dev/null 2>&1; then
            code=$(curl -sS --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/mcp) || code=000
            [[ "$code" == 401 ]] && return 0
        fi
        sleep 1
    done
    return 1
}

rollback() {
    local status="${1:-$?}"
    trap - ERR HUP INT TERM
    if [[ -n "$previous" && -d "$previous" ]]; then
        echo "Deployment failed; restoring $previous." >&2
        ln -sfn "$previous" "$root/current.next"
        mv -Tf "$root/current.next" "$root/current"
        if systemctl restart dolibarr-mcp.service && healthy; then
            echo "Previous release restored and healthy." >&2
        else
            echo "Previous release restored, but health checks failed; inspect the service." >&2
        fi
    else
        systemctl stop dolibarr-mcp.service || true
        echo "Initial deployment failed; service stopped. Inspect systemctl status." >&2
    fi
    exit "${status:-1}"
}

# Install first; switch only after dependency and configuration validation.
trap rollback ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM HUP
ln -sfn "$release" "$root/current.next"
mv -Tf "$root/current.next" "$root/current"
systemctl restart dolibarr-mcp.service
healthy
# Catch services that briefly answer readiness and then crash.
sleep 2
systemctl is-active --quiet dolibarr-mcp.service
trap - ERR INT TERM HUP
if [[ -n "$previous" && -d "$previous" ]]; then
    ln -sfn "$previous" "$root/previous"
fi
echo "Deployed $tag ($commit)."
cat DEPLOYMENT
