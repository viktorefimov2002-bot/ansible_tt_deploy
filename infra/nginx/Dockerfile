FROM node:24.11.1-alpine3.22 AS client-build
WORKDIR /client
RUN corepack enable && corepack prepare pnpm@11.19.0 --activate
COPY apps/client-web/package.json apps/client-web/pnpm-lock.yaml apps/client-web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/client-web/ ./
RUN pnpm build

FROM node:24.11.1-alpine3.22 AS admin-build
WORKDIR /admin
RUN corepack enable && corepack prepare pnpm@11.19.0 --activate
COPY apps/admin-web/package.json apps/admin-web/pnpm-lock.yaml apps/admin-web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/admin-web/ ./
RUN pnpm build

FROM nginx:1.30.5-alpine3.24
COPY --from=client-build /client/dist /usr/share/nginx/html/client
COPY --from=admin-build /admin/dist /usr/share/nginx/html/admin
COPY infra/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY infra/nginx/security.conf infra/nginx/proxy.conf infra/nginx/client-routes.conf infra/nginx/admin-routes.conf /etc/nginx/ttcp/
COPY --chmod=755 infra/nginx/15-web-origins.sh /docker-entrypoint.d/15-web-origins.sh
