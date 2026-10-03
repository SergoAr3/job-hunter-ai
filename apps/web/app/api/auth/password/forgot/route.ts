import { emailHandler } from "../../../../../lib/server/auth-handlers";
export async function POST(request: Request) {
  return emailHandler(request, "password/forgot");
}
