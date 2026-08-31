# syntax=docker/dockerfile:1
FROM node:24-alpine AS build

ARG NPM_CONFIG_REGISTRY=https://registry.npmmirror.com/
ENV NPM_CONFIG_REGISTRY=${NPM_CONFIG_REGISTRY}

WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY . .
RUN npm run build

FROM nginx:1.29-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80