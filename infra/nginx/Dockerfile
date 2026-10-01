FROM node:24.11.1-alpine3.22 AS client-build
WORKDIR /client
RUN corepack enable && corepack prepare pnpm@11.19.0 --activate
COPY apps/client-web/package.json apps/client-web/pnpm-lock.yaml apps/client-web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/client-web/ ./
RUN pnpm build

FROM nginx:1.30.5-alpine3.24
COPY --from=client-build /client/dist /usr/share/nginx/html/client
