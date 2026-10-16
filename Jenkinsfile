pipeline {
    agent { label 'scrc-main-production' }

    options {
        timestamps()
        timeout(time: 45, unit: 'MINUTES')
    }

    parameters {
        string(name: 'BACKEND_URL', defaultValue: 'http://localhost:8090', description: 'Production middleware URL used for the health check')
        stashedFile(name: 'BACKEND_ENV_FILE', description: 'Upload the production .env file')
        stashedFile(name: 'SERVICE_ACCOUNT_KEY_FILE', description: 'Upload the Firebase service-account-key.json file')
        // The sibling Middleware-water-SPCRC pipeline this was modeled on
        // never uploads this one. Without it, a fresh checkout has no
        // encryption.key at all (it's gitignored) and the app silently
        // generates a brand-new one on first boot -- which can't decrypt
        // any device credential already stored under the real key. Upload
        // the existing, working key here every time; never a freshly
        // generated one.
        stashedFile(name: 'ENCRYPTION_KEY_FILE', description: 'Upload the existing, working encryption.key (not a new one)')
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
                sh 'rm -rf .jenkins-env .env service-account-key.json encryption.key && mkdir -p .jenkins-env/env .jenkins-env/firebase .jenkins-env/enckey'
                dir('.jenkins-env/env') { unstash 'BACKEND_ENV_FILE' }
                sh '''
                    set -eux
                    UPLOADED_ENV_FILE=$(find .jenkins-env/env -type f -print -quit)
                    test -n "$UPLOADED_ENV_FILE"
                    cp "$UPLOADED_ENV_FILE" .env
                    chmod 600 .env
                    test -f .env
                '''

                dir('.jenkins-env/firebase') { unstash 'SERVICE_ACCOUNT_KEY_FILE' }
                sh '''
                    set -eux
                    UPLOADED_SERVICE_ACCOUNT_FILE=$(find .jenkins-env/firebase -type f -print -quit)
                    test -n "$UPLOADED_SERVICE_ACCOUNT_FILE"
                    cp "$UPLOADED_SERVICE_ACCOUNT_FILE" service-account-key.json
                    chmod 600 service-account-key.json
                    test -f service-account-key.json
                '''

                dir('.jenkins-env/enckey') { unstash 'ENCRYPTION_KEY_FILE' }
                sh '''
                    set -eux
                    UPLOADED_ENCRYPTION_KEY_FILE=$(find .jenkins-env/enckey -type f -print -quit)
                    test -n "$UPLOADED_ENCRYPTION_KEY_FILE"
                    cp "$UPLOADED_ENCRYPTION_KEY_FILE" encryption.key
                    chmod 600 encryption.key
                    test -f encryption.key
                '''
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
