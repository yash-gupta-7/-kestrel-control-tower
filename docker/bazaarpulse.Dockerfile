# Serves the assignment pack's static BazaarPulse competitor-price site.
# The site content is baked in at BUILD time from the ASSIGNMENT_PACK_DIR
# additional build context named "site" (see docker-compose.yml) -- it is
# not committed to this repo, and it is not a runtime bind mount. See
# DECISIONS.md for why: a runtime bind mount to an arbitrary host path
# requires that path to be allow-listed in Docker Desktop's File Sharing
# settings; a build context does not.
FROM python:3.11-slim
WORKDIR /site
COPY --from=site . /site
EXPOSE 8080
CMD ["python3", "-m", "http.server", "8080"]
