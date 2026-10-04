import { importAction } from "../../../../../lib/server/cv-import";
export const POST = (request: Request) => importAction(request, "/apply");
