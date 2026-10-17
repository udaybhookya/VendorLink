# The Slack MCP server the Liaison's Slack channel agent calls over HTTP when deployed
# (docs/DEPLOY.md). Local dev runs the same package over stdio with npx.
# docker build -f infra/slack_mcp/Dockerfile -t slack-mcp .
# Env: SLACK_BOT_TOKEN, SLACK_TEAM_ID, AUTH_TOKEN (bearer token our client sends), PORT.
FROM node:20-slim
RUN npm install -g @zencoderai/slack-mcp-server@0.0.1 && npm cache clean --force
USER node
ENV PORT=8080
CMD ["sh", "-c", "exec slack-mcp --transport http --port ${PORT}"]
