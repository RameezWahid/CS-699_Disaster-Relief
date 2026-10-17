# Build context is the repo root so the image can include data/tiles/mumbai.pmtiles.
FROM node:20.20-alpine3.22 AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# public/basemap/mumbai.pmtiles is a symlink in the repo; replace it with the real file.
RUN rm public/basemap/mumbai.pmtiles
COPY data/tiles/mumbai.pmtiles public/basemap/mumbai.pmtiles
RUN npm run build

FROM nginx:1.29-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
