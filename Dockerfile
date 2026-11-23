# Multi-stage build: compile the React app, then serve it and the API from one Python container.
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /srv/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=web /web/dist /srv/frontend/dist
ENV NFLPROPS_CACHE=/srv/backend/data/nfl_cache DATABASE_URL=sqlite:////srv/backend/data/app.db
# glibc gives every thread that allocates its own malloc arena, up to 8 per core: on Railway's 32-core hosts the web
# process had 24 of them holding ~100 MB within half an hour, and memory freed across many arenas rarely goes back to
# the OS. Two is plenty under the GIL, and memory is what Railway bills.
ENV MALLOC_ARENA_MAX=2
# Mount persistent storage at /srv/backend/data (docker run -v, or a Railway volume). No VOLUME line: Railway rejects it.
# The NFL cache (schedule, stats, play-by-play) lives there too, beside the NBA's, so a deploy doesn't start it empty.
EXPOSE 8000
# Hosts like Railway assign the port in $PORT
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
