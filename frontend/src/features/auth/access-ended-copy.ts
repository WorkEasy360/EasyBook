import { personReasonOf } from "@/lib/auth/workeasy360-paths";

/**
 * What the access-ended page says for each `?reason=`. Anything unknown is the organization's own ended
 * access, so a hand-edited URL can never show a message the backend did not choose.
 */
export function accessEndedCopy(reason: string | undefined): { title: string; description: string } {
  if (reason === "unverified") {
    return {
      title: "EasyBook can't confirm your access",
      description:
        "EasyBook cannot confirm your organization's access with Workeasy360 at the moment. Try again in a few minutes; if this continues, contact support.",
    };
  }
  if (reason === "sign_in_required") {
    return {
      title: "Sign in with Workeasy360",
      description:
        "EasyBook now signs everyone in through Workeasy360, and this session was opened before that. Sign in with Workeasy360 to continue; your books are kept.",
    };
  }
  const person = personReasonOf(reason);
  if (person === "membership_inactive") {
    return {
      title: "Your access has been removed",
      description:
        "Your access to this organization has been removed in Workeasy360. Ask an owner or administrator of the organization to restore it; your organization's books are kept.",
    };
  }
  if (person === "no_seat") {
    return {
      title: "You no longer have an EasyBook seat",
      description:
        "Your seat for EasyBook in this organization has been removed. Ask an owner or administrator to assign one in Workeasy360; your organization's books are kept.",
    };
  }
  return {
    title: "EasyBook access has ended",
    description:
      "Your organization's access to EasyBook has ended. An owner can renew it in Workeasy360; your books are kept.",
  };
}
