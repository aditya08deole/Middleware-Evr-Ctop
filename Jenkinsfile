pipeline {
    agent { label 'scrc-main-production' }

    options {
        timestamps()
        timeout(time: 45, unit: 'MINUTES')
    }

    parameters {
        string(name: 'BACKEND_URL', defaultValue: 'http://localhost:8090', description: 'Production middleware URL used for the health check')
    }

    stages {
        stage('SCM Checkout') {
            steps {
                checkout([
                    $class: 'GitSCM',
                    branches: [[name: '*/main']],
                    userRemoteConfigs: [[
                        url: 'https://github.com/aditya08deole/Middleware-Evr-Ctop.git',
                        credentialsId: 'SmartCityOrganisation-GitHubApp'
                    ]]
                ])
            }
        }

        stage('Verify Repository') {
            steps {
                sh '''
                    set -eux
                    git remote -v
                    git branch --show-current
                    git log -1 --oneline
                '''
            }
        }

        stage('Prepare Environment File') {
            steps {
                // All three secrets come from Jenkins' own Credentials
                // store (Secret file type), registered ONCE by whoever has
                // Jenkins admin access -- not re-uploaded per build. This
                // is what makes every subsequent push fully hands-off: no
                // one attaches a file to trigger a deploy, ever again.
                // encryption.key MUST be the credential holding the real,
                // existing key -- a freshly generated one can decrypt
                // nothing already stored (see git history for the outage
                // that caused once).
                withCredentials([
                    file(credentialsId: 'evara-env-file', variable: 'ENV_FILE'),
                    file(credentialsId: 'evara-service-account-key', variable: 'SA_KEY_FILE'),
                    file(credentialsId: 'evara-encryption-key', variable: 'ENCRYPTION_KEY_FILE')
                ]) {
                    sh '''
                        set -eux
                        rm -f .env service-account-key.json encryption.key
                        cp "$ENV_FILE" .env
                        cp "$SA_KEY_FILE" service-account-key.json
                        cp "$ENCRYPTION_KEY_FILE" encryption.key
                        chmod 600 .env service-account-key.json encryption.key
                        test -f .env
                        test -f service-account-key.json
                        test -f encryption.key
                    '''
                }
            }
        }

        stage('Lint & Test') {
            steps {
                // Runs in a throwaway container so the agent only ever
                // needs Docker, same as every other stage. A failure here
                // stops the pipeline before Build/Deploy touch the running
                // production containers.
                sh '''
                    set -eux
                    docker run --rm -v "$PWD":/app -w /app python:3.11-slim bash -c "
                        pip install --quiet -r requirements.txt -r requirements-dev.txt &&
                        ruff check . &&
                        black --check . &&
                        python -m pytest tests/ -q
                    "
                '''
            }
        }

        stage('Build') {
            steps {
                sh 'set -eux; test -f .env; docker compose build'
            }
        }

        stage('Deploy Production') {
            steps {
                sh '''
                    set -eux
                    docker compose down || true
                    docker compose up -d
                    docker compose ps
                '''
            }
        }

        stage('Health Check') {
            steps {
                sh '''
                    set -eu

                    for attempt in $(seq 1 30); do
                        if curl --fail --silent --show-error --max-time 5 "$BACKEND_URL/health"; then
                            echo
                            echo "Health check passed on attempt $attempt."
                            exit 0
                        fi

                        echo "Health check attempt $attempt/30 failed; waiting for the container to become ready..."
                        sleep 3
                    done

                    echo "Health check failed after 90 seconds."
                    docker compose ps
                    exit 1
                '''
            }
        }
    }

    post {
        always {
            sh '''
                set +e
                echo "===== Final Production Container Status ====="
                docker compose ps
            '''
        }
        success { echo 'Production deployment and validation completed successfully.' }
        failure { echo 'Production deployment or validation failed.' }
    }
}
