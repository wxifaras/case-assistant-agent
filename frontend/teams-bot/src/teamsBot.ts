import {
  AgentApplication,
  MemoryStorage,
  MessageFactory,
  TurnContext,
  TurnState,
} from "@microsoft/agents-hosting";
import { SSOCommandMap } from "./commands/SSOCommandMap";
import config from "./config";
import {
  AgentClient,
  conversationIdToSessionId,
  QueryResponse,
} from "./agentClient";
import { buildAnswerCard } from "./answerCard";

export class TeamsBot extends AgentApplication<TurnState> {
  private readonly agentClient = new AgentClient(config.agentUrl);

  constructor() {
    super({
      storage: new MemoryStorage(),
      authorization: {
        graph: { name: config.botSsoConnectionName },
      },
    });

    this.onConversationUpdate("membersAdded", async (context: TurnContext, _state: TurnState) => {
      const membersAdded = context.activity.membersAdded ?? [];
      for (let cnt = 0; cnt < membersAdded.length; cnt++) {
        if (membersAdded[cnt].id) {
          await context.sendActivity(
            "Welcome to the Case Assistant! Ask me a question, or type 'show' to see your profile.",
          );
          break;
        }
      }
    });

    this.authorization.onSignInSuccess(async (_context: TurnContext, _state: TurnState) => {
      console.log("User signed in successfully.");
    });

    this.authorization.onSignInFailure(async (context: TurnContext, _state: TurnState, authId?: string, err?: string) => {
      console.error(`Sign in failure in ${authId}: ${err}`);
      await context.sendActivity(MessageFactory.text("Sign in failed. Please try again."));
    });

    this.onError(async (_context: TurnContext, err: unknown) => {
      console.error("Unhandled error in bot:", err);
    });

    this.onMessage("logout", async (context: TurnContext, state: TurnState) => {
      await this.authorization.signOut(context, state, "graph");
      await context.sendActivity(MessageFactory.text("You have been signed out."));
    });

    // Generic message handler. SSO is only triggered when the user explicitly
    // invokes an SSO command (e.g. "show") — free-form messages are routed to
    // the backend without requiring a Teams tenant, which lets the bot run in
    // the Agents Playground with empty clientId/tenantId.
    this.onActivity("message", async (context: TurnContext, _state: TurnState) => {
      console.log("Running with Message Activity.");

      let txt = context.activity.text ?? "";
      const removedMentionText = context.activity.removeRecipientMention();
      if (removedMentionText) {
        txt = removedMentionText.toLowerCase().replace(/\n|\r/g, "").trim();
      }

      const SSOCommand = SSOCommandMap.get(txt);
      if (SSOCommand) {
        try {
          const tokenResponse = await this.authorization.getToken(context, "graph");
          if (!tokenResponse?.token) {
            await context.sendActivity(
              MessageFactory.text("Unable to get token. Please sign in first."),
            );
            return;
          }
          await SSOCommand.operationWithToken(context, tokenResponse.token);
        } catch (err) {
          console.error("SSO command failed:", err);
          await context.sendActivity(
            MessageFactory.text(
              "SSO isn't configured for this environment. Ask me an IT question instead.",
            ),
          );
        }
        return;
      }

      await this.handleAgenticQuery(context, txt);
    });
  }

  /**
   * Forward the user's message to the Case Assistant backend and render
   * the response in Teams.
   */
  private async handleAgenticQuery(context: TurnContext, text: string): Promise<void> {
    const userText = (text || context.activity.text || "").trim();

    if (!userText) {
      return;
    }

    const userId =
      context.activity.from?.aadObjectId ||
      context.activity.from?.id ||
      "anonymous";
    const sessionId = conversationIdToSessionId(
      context.activity.conversation?.id ?? `user-${userId}`,
    );

    // Optional: pass through Teams SSO token if backend auth is enabled.
    let bearerToken: string | undefined;
    try {
      const tokenResponse = await this.authorization.getToken(context, "graph");
      bearerToken = tokenResponse?.token;
    } catch {
      // Non-fatal: backend currently allows unauthenticated requests in dev.
    }

    // Show a "…" typing indicator while the backend runs. Teams auto-expires
    // the indicator after ~10s, so refresh it every 4s until the call returns.
    const sendTyping = () =>
      context
        .sendActivity({ type: "typing" } as unknown as Parameters<typeof context.sendActivity>[0])
        .catch(() => undefined);
    await sendTyping();
    const typingTimer = setInterval(sendTyping, 4000);

    try {
      const response: QueryResponse = await this.agentClient.query({
        query: userText,
        userId,
        sessionId,
        bearerToken,
      });

      await context.sendActivity(
        MessageFactory.attachment(buildAnswerCard(response, config.agentUrl)),
      );
    } catch (err) {
      console.error("Agent query failed:", err);
      await context.sendActivity(
        MessageFactory.text(
          "Sorry, I couldn't reach the Case Assistant agent right now. Please try again shortly.",
        ),
      );
    } finally {
      clearInterval(typingTimer);
    }
  }
}
