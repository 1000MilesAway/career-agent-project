# The cabinet: built with Node, served by nginx.
#
# Two stages so the shipped image carries static files and a web server, not a
# Node toolchain — the build stage is around 400 MB and none of it is needed
# once the bundle exists.

FROM node:22-alpine AS build

WORKDIR /build

# Dependencies first, in their own layer: application code changes on every
# deploy, the lockfile rarely, so this layer survives most rebuilds.
COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig*.json vite.config.ts index.html ./
COPY src/ ./src/
# robots.txt and sitemap.xml: Vite copies public/ to the root of dist as is.
COPY public/ ./public/
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html

EXPOSE 80

# The stock nginx image already runs the master as root and workers as nginx;
# the healthcheck is what deploy.sh watches, so it asks for the page rather
# than for the port.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD wget --quiet --tries=1 --spider http://127.0.0.1/ || exit 1
