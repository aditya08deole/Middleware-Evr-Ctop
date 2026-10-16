#!/bin/bash
# Registers the 3 secret files as Jenkins "Secret file" credentials in one
# shot, via Jenkins' own Script Console API -- so the one-time setup step
# in HOW_TO_RUN.md's "Jenkins Pipeline Setup" section is itself a single
# script run instead of 3 manual UI form submissions.
#
# You run this yourself, from a machine that can reach your Jenkins server,
# using your own Jenkins credentials. Nobody (including this script's
# author) needs to see your Jenkins password/token -- it only ever goes
# from your shell to your own Jenkins server.
#
# Requires: Overall/Administer permission on the target Jenkins instance
# (same permission level needed to add credentials by hand).
#
# Usage:
#   JENKINS_URL=https://jenkins.example.com \
#   JENKINS_USER=youruser \
#   JENKINS_API_TOKEN=<generate at Jenkins -> your user -> Configure -> API Token> \
#   ./scripts/jenkins-register-secrets.sh
set -euo pipefail
cd "$(dirname "$0")/.."

: "${JENKINS_URL:?Set JENKINS_URL, e.g. https://jenkins.example.com}"
: "${JENKINS_USER:?Set JENKINS_USER to your Jenkins username}"
: "${JENKINS_API_TOKEN:?Set JENKINS_API_TOKEN -- generate one at Jenkins > your user > Configure > API Token}"

for f in .env service-account-key.json encryption.key; do
    if [ ! -f "$f" ]; then
        echo "ERROR: $f not found in $(pwd) -- run this from the repo root with these 3 files present." >&2
        exit 1
    fi
done

echo "=== Fetching CSRF crumb ==="
CRUMB_JSON=$(curl -sf -u "$JENKINS_USER:$JENKINS_API_TOKEN" "$JENKINS_URL/crumbIssuer/api/json")
CRUMB_FIELD=$(echo "$CRUMB_JSON" | sed -n 's/.*"crumbRequestField":"\([^"]*\)".*/\1/p')
CRUMB_VALUE=$(echo "$CRUMB_JSON" | sed -n 's/.*"crumb":"\([^"]*\)".*/\1/p')

echo "=== Encoding secret files ==="
ENV_B64=$(base64 -w0 .env)
SA_KEY_B64=$(base64 -w0 service-account-key.json)
ENC_KEY_B64=$(base64 -w0 encryption.key)

echo "=== Registering credentials via Jenkins Script Console ==="
GROOVY_SCRIPT=$(cat <<GROOVY
import com.cloudbees.plugins.credentials.CredentialsScope
import com.cloudbees.plugins.credentials.SecretBytes
import com.cloudbees.plugins.credentials.domains.Domain
import com.cloudbees.plugins.credentials.SystemCredentialsProvider
import org.jenkinsci.plugins.plaincredentials.impl.FileCredentialsImpl

def store = SystemCredentialsProvider.getInstance().getStore()

def addOrReplace = { id, fileName, b64, desc ->
    def existing = com.cloudbees.plugins.credentials.CredentialsProvider
        .lookupCredentials(FileCredentialsImpl.class, Jenkins.instance, null, null)
        .find { it.id == id }
    if (existing != null) {
        store.removeCredentials(Domain.global(), existing)
    }
    def bytes = b64.decodeBase64()
    def cred = new FileCredentialsImpl(CredentialsScope.GLOBAL, id, desc, fileName, SecretBytes.fromBytes(bytes))
    store.addCredentials(Domain.global(), cred)
    println("Registered: " + id)
}

addOrReplace('evara-env-file', '.env', '''$ENV_B64''', 'Evara middleware .env (managed by scripts/jenkins-register-secrets.sh)')
addOrReplace('evara-service-account-key', 'service-account-key.json', '''$SA_KEY_B64''', 'Firebase service account key (managed by scripts/jenkins-register-secrets.sh)')
addOrReplace('evara-encryption-key', 'encryption.key', '''$ENC_KEY_B64''', 'Credential encryption key -- DO NOT replace with a freshly generated one (managed by scripts/jenkins-register-secrets.sh)')
GROOVY
)

RESPONSE=$(curl -sf -u "$JENKINS_USER:$JENKINS_API_TOKEN" \
    -H "$CRUMB_FIELD: $CRUMB_VALUE" \
    --data-urlencode "script=$GROOVY_SCRIPT" \
    "$JENKINS_URL/scriptText")

echo "$RESPONSE"
echo "=== Done. All 3 secrets are now registered as Jenkins credentials. ==="
echo "Next: create the pipeline job (New Item -> Pipeline -> Pipeline script from SCM) if you haven't already."
