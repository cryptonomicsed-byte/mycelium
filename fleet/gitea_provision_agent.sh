#!/bin/bash
# gitea_provision_agent.sh <agent-name> — GitHub-style per-agent account.
# Flow (Gitea 1.26.4 API constraints, verified 2026-08-19):
#   1. admin creates user agent-<name>
#   2. admin resets password (PATCH needs login_name in Gitea 1.26.4)
#   3. agent authenticates with its own basic-auth to mint its token
#      (admin tokens CANNOT mint tokens for other users in this version)
#   4. org membership (org 'ares', owners team)
# Creds saved to /root/.agent-creds/agent-<name>.env (0600).
set -u
NAME="$1"
GITEA="http://127.0.0.1:3001"
ADMIN_TOKEN="eaae981851cc5eef1b20e527c8fdefc509b092dd"
USER="agent-$NAME"
EMAIL="$USER@ares.local"
PASS="Ag3nt-$(openssl rand -hex 8)"
ORG="ares"
DIR="/root/.agent-creds"
mkdir -p "$DIR"; chmod 700 "$DIR"

# 1. create user (201 ok, 422 exists)
curl -s -m 20 -X POST -H "Authorization: token $ADMIN_TOKEN" -H "Content-Type: application/json" \
  "$GITEA/api/v1/admin/users" \
  -d "{\"username\":\"$USER\",\"email\":\"$EMAIL\",\"password\":\"$PASS\",\"must_change_password\":false,\"restricted\":false}" \
  -o /dev/null -w "create user: %{http_code}\n"

# 2. reset password (idempotent; PATCH requires login_name)
curl -s -m 20 -X PATCH -H "Authorization: token $ADMIN_TOKEN" -H "Content-Type: application/json" \
  "$GITEA/api/v1/admin/users/$USER" -d "{\"login_name\":\"$USER\",\"password\":\"$PASS\"}" \
  -o /dev/null -w "reset pw: %{http_code}\n"

# 3. agent mints its own token via basic auth
TOKEN=$(curl -s -m 20 -X POST -u "$USER:$PASS" -H "Content-Type: application/json" \
  "$GITEA/api/v1/users/$USER/tokens" \
  -d '{"name":"agent-birth","scopes":["all"]}' | python3 -c "import json,sys; print(json.load(sys.stdin).get('sha1',''))" 2>/dev/null)
if [ -z "$TOKEN" ]; then echo "TOKEN FAILED"; exit 1; fi
echo "token: ${TOKEN:0:8}..."

# 4. org membership (create org if needed, add to owners team)
curl -s -m 20 -X POST -H "Authorization: token $ADMIN_TOKEN" -H "Content-Type: application/json" \
  "$GITEA/api/v1/orgs" -d "{\"username\":\"$ORG\",\"description\":\"Agent collective\"}" -o /dev/null -w "org: %{http_code}\n"
curl -s -m 20 -X PUT -H "Authorization: token $ADMIN_TOKEN" \
  "$GITEA/api/v1/orgs/$ORG/teams/owners/members/$USER" -o /dev/null -w "member: %{http_code}\n"

# 5. creds file
cat > "$DIR/$USER.env" <<EOF
GITEA_USER=$USER
GITEA_TOKEN=$TOKEN
GITEA_PASS=$PASS
GITEA_URL=$GITEA
EOF
chmod 600 "$DIR/$USER.env"
echo "READY $USER -> $DIR/$USER.env"
