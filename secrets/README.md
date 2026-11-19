# Local secrets for Docker Compose

This folder holds the two files `compose.yaml` mounts as Docker secrets:

    secret_key.txt    Flask SECRET_KEY (64 hex characters)
    db_password.txt   the PostgreSQL password

Create them with the commands at the top of `compose.yaml`. Every file in
this folder except this README is ignored by Git (see `.gitignore`), and
the `.dockerignore` allow-list keeps the folder out of the image.
