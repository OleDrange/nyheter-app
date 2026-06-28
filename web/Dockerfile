# ---- bygg ----
FROM node:22-slim AS build
WORKDIR /app
COPY web/package.json ./
RUN npm install
COPY web/ .
RUN npm run build

# ---- kjør ----
FROM node:22-slim
ENV TZ=Europe/Oslo \
    NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=8080 \
    BRIEFING_DIR=/data/briefings
WORKDIR /app
COPY --from=build /app/dist ./dist
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/package.json ./package.json
EXPOSE 8080
CMD ["node", "./dist/server/entry.mjs"]
