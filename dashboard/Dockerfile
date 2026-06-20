FROM node:20-slim
WORKDIR /app
COPY package.json .
RUN npm install --production
COPY server.js .
COPY public ./public
ENV NODE_ENV=production
ENV TRADES_CSV_PATH=/data/trades.csv
ENV AGENT_LOG_PATH=/logs/agent.log
ENV IBKR_GATEWAY_URL=https://localhost:5000
EXPOSE 3000
CMD ["node", "server.js"]
