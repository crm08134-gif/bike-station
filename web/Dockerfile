# 지도 웹 이미지. npm run build 결과(dist/)를 nginx가 서빙한다.

FROM node:24-bookworm-slim AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Vite는 빌드할 때 이 값을 코드에 넣는다 (compose.yaml의 build.args)
ARG VITE_API_BASE=http://localhost:3000
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

FROM nginx:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
