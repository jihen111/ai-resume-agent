ARG NODE_VERSION=20
FROM node:${NODE_VERSION}-alpine

WORKDIR /app
RUN chown node:node /app

# Run as the non-root "node" user provided by the image. node_modules must belong
# to it: Vite writes its pre-bundling cache into node_modules/.vite.
USER node

# Dependencies first: this layer stays cached until package*.json changes.
COPY --chown=node:node package*.json ./
RUN npm install --no-audit --no-fund

COPY --chown=node:node . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
