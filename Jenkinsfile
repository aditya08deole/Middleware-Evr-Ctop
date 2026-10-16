pipeline {
    agent { label 'evara-middleware-production' }

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
                        credentialsId: 'evara-middleware-github'
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

        stage('Prepare Secrets') {
            steps {
                // All three come from Jenkins' own Credentials store (Secret
                // file type), configured once by a Jenkins admin — not
                // re-uploaded per build. See HOW_TO_RUN.md's "Jenkins
                // Pipeline Setup" section for the one-time setup steps.
                // encryption.key MUST be the credential holding the real
                // working key — a different one can decrypt nothing already
                // stored (see git history for the outage this caused once).
                withCredentials([
                    file(credentialsId: 'evara-env-file', variable: 'ENV_FILE'),
                    file(credentialsId: 'evara-service-account-key', variable: 'SA_KEY_FILE'),
                    file(credentialsId: 'evara-encryption-key', variable: 'ENCRYPTION_KEY_FILE')
                ]) {
                    sh '''
                        set -eux
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
                // Runs in a throwaway container, not on the agent's own
                // Python — the agent only needs Docker, same as every other
                // stage. A failure here stops the pipeline before Build/
                // Deploy ever touch the running production containers.
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
                sh 'set -eux; docker compose build'
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
