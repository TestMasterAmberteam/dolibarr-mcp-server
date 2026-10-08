// Paste into a Pipeline job. The remote deployment helper is installed separately.
// Requires a Linux agent, Credentials Binding, and SSH Username with private key.
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
        string(name: 'RELEASE_TAG', defaultValue: 'latest',
            description: 'Published stable GitHub release: latest or vMAJOR.MINOR.PATCH')
        string(name: 'SSH_CREDENTIALS_ID', defaultValue: 'dolibarr-mcp-ssh',
            description: 'SSH Username with private key credential; username root')
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
                }
                // Public host key read from the target during installation.
                writeFile file: 'dolibarr-mcp-known-hosts', text: '''10.0.1.95 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIF8OIGKwezEt23q8KoFZO/nIROZbbJf7L7qeMk4Awce
'''
            }
        }
        stage('Deploy release') {
            steps {
                withCredentials([sshUserPrivateKey(
                    credentialsId: params.SSH_CREDENTIALS_ID,
                    keyFileVariable: 'SSH_KEY',
                    usernameVariable: 'SSH_USER'
                )]) {
                    // Shell expansion keeps credential paths out of Groovy interpolation.
                    sh '''
                        set -eu
                        set +x
                        test "$SSH_USER" = root
                        ssh -i "$SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes \
                            -o StrictHostKeyChecking=yes \
                            -o UserKnownHostsFile="$WORKSPACE/dolibarr-mcp-known-hosts" \
                            -o ConnectTimeout=10 -o ServerAliveInterval=15 \
                            -o ServerAliveCountMax=3 "$SSH_USER@10.0.1.95" \
                            "/usr/local/sbin/dolibarr-mcp-deploy '$RELEASE_TAG'"
                    '''
                }
            }
        }
        stage('Verify LAN endpoint') {
            steps {
                sh '''
                    set -eu
                    curl -fsS --max-time 10 http://10.0.1.95:8080/health/live
                    curl -fsS --max-time 10 http://10.0.1.95:8080/health/ready
                    code=$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' \
                        http://10.0.1.95:8080/mcp)
                    test "$code" = 401
                '''
            }
        }
    }
}
