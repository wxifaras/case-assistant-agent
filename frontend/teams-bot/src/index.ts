// Import required packages
import path from "path";
import send from "send";
import { startServer } from "@microsoft/agents-hosting-express";

// This bot's main dialog.
import { TeamsBot } from "./teamsBot";

// Create the bot that will handle incoming messages.
const bot = new TeamsBot();

// Create HTTP server with the agent application.
const expressApp = startServer(bot);

// Low-level request log so we can see EVERY HTTP call hitting the bot,
// including ones that the Agents SDK may not surface (useful for debugging
// Adaptive Card Action.Submit delivery in the Agents Playground).
expressApp.use((req, _res, next) => {
  console.log(`[HTTP] ${req.method} ${req.url}`);
  next();
});

// Serve auth-start.html and auth-end.html for Teams SSO
expressApp.get(["/auth-start.html", "/auth-end.html"], async (req, res) => {
  send(
    req,
    path.join(
      __dirname,
      "../public",
      req.url.includes("auth-start.html") ? "auth-start.html" : "auth-end.html"
    )
  ).pipe(res);
});
