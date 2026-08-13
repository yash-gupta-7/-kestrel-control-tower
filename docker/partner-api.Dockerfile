# Wraps the assignment pack's own partner_api/server.py. The fixture code
# is baked in at BUILD time from the ASSIGNMENT_PACK_DIR additional build
# context named "pack" (see docker-compose.yml) -- it is not committed to
# this repo, and it is not a runtime bind mount. See DECISIONS.md for why:
# a runtime bind mount to an arbitrary host path requires that path to be
# allow-listed in Docker Desktop's File Sharing settings; a build context
# does not.
FROM python:3.11-slim
RUN pip install --no-cache-dir fastapi uvicorn
WORKDIR /app
COPY --from=pack . /app
EXPOSE 8088
CMD ["python3", "server.py"]
