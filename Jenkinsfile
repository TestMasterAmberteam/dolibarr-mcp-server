// Use from SCM or paste into a Pipeline job. The remote deployment helper is
// installed separately. Requires a Linux agent, Credentials Binding, an SSH
// Username with private key, and a Secret file containing pinned host keys.
pipeline {
    agent { label 'linux' }
    options {
        skipDefaultCheckout(true)
        disableConcurrentBuilds()
        timestamps()
        timeout(time: 20, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }
    parameters {
        string(name: 'DEPLOY_HOST', defaultValue: '',
            description: 'Target DNS name or IPv4 address')
        string(name: 'RELEASE_TAG', defaultValue: 'latest',
            description: 'Published stable GitHub release: latest or vMAJOR.MINOR.PATCH')
        string(name: 'SSH_CREDENTIALS_ID', defaultValue: 'dolibarr-mcp-ssh',
            description: 'SSH Username with private key credential; username root')
        string(name: 'SSH_KNOWN_HOSTS_CREDENTIALS_ID', defaultValue: 'dolibarr-mcp-known-hosts',
            description: 'Secret file credential containing the pinned OpenSSH known_hosts entry')
    }
    stages {
        stage('Validate') {
            steps {
                script {
                    if (!(params.RELEASE_TAG ==~ /latest|v[0-9]+\.[0-9]+\.[0-9]+/)) {
                        error('RELEASE_TAG must be latest or vMAJOR.MINOR.PATCH')
                    }
                    if (!params.SSH_CREDENTIALS_ID?.trim()) {
                        error('SSH_CREDENTIALS_ID is required')
                    }
                    if (!(params.DEPLOY_HOST?.trim() ==~ /[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?/)) {
                        error('DEPLOY_HOST must be a DNS name or IPv4 address')
                    }
                    if (!params.SSH_KNOWN_HOSTS_CREDENTIALS_ID?.trim()) {
                        error('SSH_KNOWN_HOSTS_CREDENTIALS_ID is required')
                    }
                }
            }
        }
        stage('Deploy release') {
            steps {
                withEnv(["DEPLOY_HOST=${params.DEPLOY_HOST.trim()}"]) {
                    withCredentials([
                        sshUserPrivateKey(
                            credentialsId: params.SSH_CREDENTIALS_ID,
                            keyFileVariable: 'SSH_KEY',
                            usernameVariable: 'SSH_USER'
                        ),
                        file(
                            credentialsId: params.SSH_KNOWN_HOSTS_CREDENTIALS_ID,
                            variable: 'SSH_KNOWN_HOSTS'
                        )
                    ]) {
                        // Shell expansion keeps credential paths out of Groovy interpolation.
                        sh '''
                            set -eu
                            set +x
                            test "$SSH_USER" = root
                            known_hosts=$(mktemp)
                            trap 'rm -f "$known_hosts"' EXIT
                            cp "$SSH_KNOWN_HOSTS" "$known_hosts"
                            chmod 0600 "$known_hosts"
                            ssh-keygen -F "$DEPLOY_HOST" -f "$known_hosts" >/dev/null
                            ssh -i "$SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes \
                                -o StrictHostKeyChecking=yes \
                                -o UserKnownHostsFile="$known_hosts" \
                                -o ConnectTimeout=10 -o ServerAliveInterval=15 \
                                -o ServerAliveCountMax=3 "$SSH_USER@$DEPLOY_HOST" \
                                "/usr/local/sbin/dolibarr-mcp-deploy '$RELEASE_TAG'"
                        '''
                    }
                }
            }
        }
        stage('Verify LAN endpoint') {
            steps {
                withEnv(["DEPLOY_HOST=${params.DEPLOY_HOST.trim()}"]) {
                    sh '''
                        set -eu
                        curl -fsS --max-time 10 "http://$DEPLOY_HOST:8080/health/live"
                        curl -fsS --max-time 10 "http://$DEPLOY_HOST:8080/health/ready"
                        code=$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' \
                            "http://$DEPLOY_HOST:8080/mcp")
                        test "$code" = 401
                    '''
                }
            }
        }
    }
}
