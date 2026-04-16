# ── build stage ────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install --no-audit --no-fund
COPY tsconfig.json vite.config.ts index.html ./
COPY public ./public
COPY src ./src
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
# VITE_SHOW_DRAFTS=1 includes draft-marked MDX posts (src/content/blog/*.mdx
# with `draft: true` frontmatter) in the build. Leave unset for prod.
ARG VITE_SHOW_DRAFTS=
ENV VITE_SHOW_DRAFTS=$VITE_SHOW_DRAFTS
RUN npm run build

# ── runtime: static server via nginx ───────────────────────
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s CMD wget -q --spider http://localhost/ || exit 1
