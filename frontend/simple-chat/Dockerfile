FROM cgr.dev/chainguard/node:latest AS build

# make the 'app' folder the current working directory
WORKDIR /app

COPY --chown=node:node . .

# install project dependencies
RUN npm install
RUN npm run build

FROM cgr.dev/chainguard/nginx:latest

COPY --from=build /app/build /usr/share/nginx/html
# nginx image auto-processes files in /etc/nginx/templates/ with envsubst at startup
COPY --from=build /app/nginx/nginx.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 8080
