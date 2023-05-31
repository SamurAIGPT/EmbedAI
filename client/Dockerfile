FROM node:18-alpine

WORKDIR /app/

COPY package*.json ./

RUN npm config set strict-ssl=false \
    && npm install -g npm@9.6.7 \
    && npm install

COPY . .