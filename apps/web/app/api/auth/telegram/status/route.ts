import { telegramChallenge } from "../../../../../lib/server/telegram-auth";
export const POST = (request: Request) => telegramChallenge(request, "status");
